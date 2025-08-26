import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import List, Optional

import pika
import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from shared.config import Config
from shared.database import job_repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class JobFetcherWorker:
    def __init__(self):
        self.config = Config()
        self.job_repo = job_repository
        self.connection = None
        self.channel = None
        self.polling_interval = int(os.getenv('POLLING_INTERVAL', '5'))

    def connect_rabbitmq(self):
        """Establish connection to RabbitMQ."""
        try:
            parameters = pika.URLParameters(self.config.rabbitmq_url)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # Declare the exchange
            self.channel.exchange_declare(
                exchange=self.config.exchange_name,
                exchange_type=self.config.exchange_type,
                durable=True
            )
            
            logger.info("Connected to RabbitMQ successfully")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
            raise

    def fetch_pending_jobs(self) -> List[dict]:
        """Fetch pending jobs from the database with row-level locking."""
        try:
            with self.job_repo.db.get_cursor() as cursor:
                # Use SELECT FOR UPDATE SKIP LOCKED to prevent race conditions
                query = """
                    SELECT jr.job_id, jr.workspace_id, jr.user_id, jr.commit_sha, 
                           jr.commit_message, jr.commit_author, jr.created_at,
                           w.github_repo, w.github_branch
                    FROM job_requests jr
                    JOIN workspaces w ON jr.workspace_id = w.id
                    WHERE jr.status = 'pending'
                    ORDER BY jr.created_at ASC
                    LIMIT 10
                    FOR UPDATE OF jr SKIP LOCKED
                """
                cursor.execute(query)
                jobs = cursor.fetchall()
                
                # Update status to 'queued' for fetched jobs
                if jobs:
                    job_ids = [job['job_id'] for job in jobs]
                    update_query = """
                        UPDATE job_requests 
                        SET status = 'queued', updated_at = NOW()
                        WHERE job_id = ANY(%s)
                    """
                    cursor.execute(update_query, (job_ids,))
                    logger.info(f"Fetched and marked {len(jobs)} jobs as queued")
                
                return [dict(job) for job in jobs]
        except Exception as e:
            logger.error(f"Error fetching pending jobs: {str(e)}")
            return []

    def publish_job(self, job: dict):
        """Publish a job to RabbitMQ with retry logic."""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # Check if connection/channel is valid
                if not self.connection or self.connection.is_closed:
                    logger.warning("Connection is closed, reconnecting...")
                    self.connect_rabbitmq()
                
                if not self.channel or self.channel.is_closed:
                    logger.warning("Channel is closed, creating new channel...")
                    self.channel = self.connection.channel()
                    # Redeclare exchange after channel recreation
                    self.channel.exchange_declare(
                        exchange=self.config.exchange_name,
                        exchange_type=self.config.exchange_type,
                        durable=True
                    )
                
                message_body = json.dumps({
                    'job_id': job['job_id'],
                    'workspace_id': job['workspace_id'],
                    'user_id': job['user_id'],
                    'github_repo': job['github_repo'],
                    'branch': job['github_branch'],
                    'commit_sha': job['commit_sha'],
                    'commit_message': job['commit_message'],
                    'commit_author': job['commit_author'],
                    'created_at': job['created_at'].isoformat() if isinstance(job['created_at'], datetime) else job['created_at']
                })
                
                # Publish with routing key 'job.created' to trigger the clone worker
                self.channel.basic_publish(
                    exchange=self.config.exchange_name,
                    routing_key='job.created',
                    body=message_body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # Make message persistent
                        content_type='application/json'
                    )
                )
                
                logger.info(f"Published job {job['job_id']} to RabbitMQ with routing key 'job.created'")
                
                # Add log entry to database
                self.job_repo.add_job_log(
                    job['job_id'],
                    'job_fetcher',
                    f"Job published to queue with routing key 'job.created'"
                )
                
                return  # Success, exit retry loop
                
            except Exception as e:
                logger.error(f"Error publishing job {job.get('job_id', 'unknown')} (attempt {attempt + 1}/{max_retries}): {str(e)}")
                
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    # Final attempt failed, revert job status
                    logger.error(f"Failed to publish job {job['job_id']} after {max_retries} attempts")
                    self.revert_job_status(job['job_id'])

    def revert_job_status(self, job_id: str):
        """Revert job status back to pending if publishing fails."""
        try:
            with self.job_repo.db.get_cursor() as cursor:
                query = """
                    UPDATE job_requests 
                    SET status = 'pending', updated_at = NOW()
                    WHERE job_id = %s
                """
                cursor.execute(query, (job_id,))
                logger.warning(f"Reverted job {job_id} status to pending")
        except Exception as e:
            logger.error(f"Error reverting job status: {str(e)}")

    def _poll_for_jobs(self):
        """Poll for pending jobs and process them"""
        try:
            # Fetch pending jobs
            jobs = self.fetch_pending_jobs()
            
            if self.config.job_fetch_mode == "polling":
                # In polling mode, process jobs directly without RabbitMQ
                for job in jobs:
                    logger.info(f"Found pending job {job['job_id']} - marked as queued")
            else:
                # In RabbitMQ mode, publish to queue
                for job in jobs:
                    self.publish_job(job)
                    
        except Exception as e:
            logger.error(f"Error polling for jobs: {e}")

    def run(self):
        """Main worker loop."""
        logger.info(f"Starting Job Fetcher Worker in {self.config.job_fetch_mode} mode...")
        
        if self.config.job_fetch_mode != "polling":
            # Connect to RabbitMQ only if not in polling mode
            self.connect_rabbitmq()
        
        logger.info(f"Starting polling loop with interval: {self.polling_interval} seconds")
        
        while True:
            try:
                self._poll_for_jobs()
                
                # Wait before next poll
                time.sleep(self.polling_interval)
                
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                # Try to reconnect to RabbitMQ if connection was lost
                if self.config.job_fetch_mode != "polling" and self.connection and self.connection.is_closed:
                    logger.info("Reconnecting to RabbitMQ...")
                    try:
                        self.connect_rabbitmq()
                    except Exception as re:
                        logger.error(f"Failed to reconnect: {str(re)}")
                
                time.sleep(self.polling_interval)

    def shutdown(self):
        """Gracefully shutdown the worker."""
        logger.info("Shutting down Job Fetcher Worker...")
        if self.connection and not self.connection.is_closed:
            self.connection.close()
        logger.info("Job Fetcher Worker stopped")


def main():
    worker = JobFetcherWorker()
    
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.shutdown()


if __name__ == "__main__":
    main()