"""
Multi-mode worker for processing job execution requests
Supports both RabbitMQ and polling modes
"""
import json
import time
import threading
import requests
from typing import Optional, Dict, Any
from enum import Enum

import pika
from pika.adapters.blocking_connection import BlockingChannel

from interfaces import IExecutor
from models.execution_models import JobExecutionRequest, ExecutionResult
from config import get_settings
from exceptions import ExecutorError
from utils import get_logger, measure_time

logger = get_logger(__name__)


class WorkerMode(Enum):
    """Worker execution modes"""
    RABBITMQ = "rabbitmq"
    POLLING = "polling"
    HYBRID = "hybrid"


class ExecutorWorker:
    """Multi-mode worker for processing job execution requests"""
    
    def __init__(self, executor: IExecutor, mode: WorkerMode = WorkerMode.HYBRID):
        """
        Initialize executor worker
        
        Args:
            executor: Job executor instance
            mode: Worker mode (rabbitmq, polling, or hybrid)
        """
        self._executor = executor
        self._settings = get_settings()
        self._mode = mode
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[BlockingChannel] = None
        self._is_running = False
        self._polling_thread: Optional[threading.Thread] = None
        self._stats = {
            'jobs_processed': 0,
            'jobs_successful': 0,
            'jobs_failed': 0,
            'total_execution_time': 0.0,
            'start_time': None
        }
        
        logger.info(f"ExecutorWorker initialized for worker {self._settings.worker_id} in {mode.value} mode")
    
    def start(self) -> None:
        """Start the worker based on configured mode"""
        logger.info(f"Starting executor worker in {self._mode.value} mode...")
        
        try:
            self._stats['start_time'] = time.time()
            
            if self._mode == WorkerMode.RABBITMQ:
                self._start_rabbitmq_mode()
            elif self._mode == WorkerMode.POLLING:
                self._start_polling_mode()
            elif self._mode == WorkerMode.HYBRID:
                self._start_hybrid_mode()
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
            self.stop()
        except Exception as e:
            logger.error(f"Worker failed to start: {e}")
            raise
        finally:
            self._cleanup_connection()
    
    def _start_rabbitmq_mode(self) -> None:
        """Start worker in RabbitMQ-only mode"""
        self._setup_connection()
        self._setup_queues()
        self._start_consuming()
    
    def _start_polling_mode(self) -> None:
        """Start worker in polling-only mode"""
        self._start_polling_loop()
    
    def _start_hybrid_mode(self) -> None:
        """Start worker in hybrid mode (both RabbitMQ and polling)"""
        # Start RabbitMQ consumer in separate thread
        rabbitmq_thread = threading.Thread(target=self._rabbitmq_consumer_thread, daemon=True)
        rabbitmq_thread.start()
        
        # Start polling in main thread
        self._start_polling_loop()
    
    def _rabbitmq_consumer_thread(self) -> None:
        """Run RabbitMQ consumer in separate thread"""
        try:
            self._setup_connection()
            self._setup_queues()
            self._start_consuming()
        except Exception as e:
            logger.error(f"RabbitMQ consumer thread failed: {e}")
    
    def _start_polling_loop(self) -> None:
        """Start the polling loop"""
        self._is_running = True
        logger.info(f"Starting polling loop with {self._settings.polling.interval_seconds}s interval")
        
        while self._is_running:
            try:
                # Poll for jobs
                job_request = self._poll_for_job()
                
                if job_request:
                    logger.info(f"Found job via polling: {job_request.job_id}")
                    self._process_job(job_request)
                
                # Wait before next poll
                time.sleep(self._settings.polling.interval_seconds)
                
            except Exception as e:
                logger.error(f"Polling loop error: {e}")
                time.sleep(self._settings.polling.interval_seconds)
    
    def _poll_for_job(self) -> Optional[JobExecutionRequest]:
        """Poll for available jobs from database"""
        try:
            import psycopg2
            import os
            
            # Get database connection
            database_url = os.getenv('DATABASE_URL')
            if not database_url:
                logger.error("DATABASE_URL not set for polling")
                return None
            
            conn = psycopg2.connect(database_url)
            cursor = conn.cursor()
            
            # Query for AI-approved jobs ready for execution
            query = """
                SELECT jr.job_id, jr.workspace_id, jr.user_id, jr.commit_sha, 
                       jr.commit_message, jr.commit_author, jr.created_at,
                       w.github_repo, w.github_branch
                FROM job_requests jr
                JOIN workspaces w ON jr.workspace_id = w.id
                WHERE jr.status = 'ai_approved'
                ORDER BY jr.created_at ASC
                LIMIT 1
                FOR UPDATE OF jr SKIP LOCKED
            """
            
            cursor.execute(query)
            result = cursor.fetchone()
            
            if result:
                job_id, workspace_id, user_id, commit_sha, commit_message, commit_author, created_at, github_repo, github_branch = result
                
                # Mark job as executing
                update_query = """
                    UPDATE job_requests 
                    SET status = 'executing', 
                        started_at = NOW(),
                        updated_at = NOW()
                    WHERE job_id = %s
                """
                cursor.execute(update_query, (job_id,))
                conn.commit()
                
                # Create job request - need to determine repo path from workspace
                repo_path = f"/shared/epsilon/repositories/{job_id}"
                
                job_request = JobExecutionRequest(
                    job_id=job_id,
                    repo_path=repo_path,
                    script_path='example_analysis.py',  # Default script
                    data_path=None,
                    workspace_id=workspace_id,
                    ai_decision={
                        'commit_sha': commit_sha,
                        'github_repo': github_repo,
                        'github_branch': github_branch
                    },
                    metadata={
                        'user_id': user_id,
                        'commit_message': commit_message,
                        'commit_author': commit_author,
                        'created_at': str(created_at)
                    }
                )
                
                cursor.close()
                conn.close()
                
                logger.info(f"Found job from database: {job_id}")
                return job_request
            
            cursor.close()
            conn.close()
            return None
                
        except Exception as e:
            logger.error(f"Database polling failed: {e}")
            return None
    
    def _process_job(self, job_request: JobExecutionRequest) -> None:
        """Process a single job (used by both RabbitMQ and polling)"""
        job_start_time = time.time()
        
        try:
            logger.info(f"Processing job {job_request.job_id}")
            
            # Check if executor is ready
            if not self._executor.is_ready:
                raise ExecutorError("Executor is not ready to process jobs")
            
            # Execute the job
            logger.error(f"🔥🔥🔥 WORKER CALLING EXECUTOR.EXECUTE for job {job_request.job_id} 🔥🔥🔥")
            logger.error(f"🔥 Executor type: {type(self._executor)}")
            result = self._executor.execute(job_request)
            logger.error(f"🔥🔥🔥 EXECUTOR RETURNED: {result} 🔥🔥🔥")
            
            # Send result back
            self._send_result_to_coordinator(result)
            
            # Update statistics
            self._update_stats(result, time.time() - job_start_time)
            
            logger.info(f"Job {job_request.job_id} completed with status: {result.status}")
            
        except Exception as e:
            logger.error(f"Failed to process job {job_request.job_id}: {e}")
            
            # Create error result
            error_result = ExecutionResult(
                job_id=job_request.job_id,
                status='failed',
                execution_time=time.time() - job_start_time,
                error=str(e)
            )
            
            try:
                self._send_result_to_coordinator(error_result)
                self._update_stats(error_result, time.time() - job_start_time)
            except Exception as send_error:
                logger.error(f"Failed to send error result: {send_error}")
    
    def _send_result_to_coordinator(self, result: ExecutionResult) -> None:
        """Send result to database"""
        try:
            import psycopg2
            import json
            import os
            
            # Get database connection
            database_url = os.getenv('DATABASE_URL')
            if not database_url:
                logger.error("DATABASE_URL not set for result submission")
                return
            
            conn = psycopg2.connect(database_url)
            cursor = conn.cursor()
            
            # Update job status in database using correct column names
            update_query = """
                UPDATE job_requests 
                SET status = %s,
                    execution_output = %s,
                    execution_error = %s,
                    duration_seconds = %s,
                    exit_code = %s,
                    logs = %s,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE job_id = %s
            """
            
            status = 'completed' if result.status == 'success' else 'failed'
            duration_seconds = int(result.execution_time) if result.execution_time else None
            exit_code = 0 if result.status == 'success' else 1
            
            cursor.execute(update_query, (
                status,
                result.output,
                result.error,
                duration_seconds,
                exit_code,
                result.logs,
                result.job_id
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.debug(f"Sent result for job {result.job_id} to database")
                
        except Exception as e:
            logger.error(f"Failed to send result to database: {e}")
            raise
    
    def stop(self) -> None:
        """Stop the worker gracefully"""
        logger.info("Stopping executor worker...")
        
        self._is_running = False
        
        # Stop RabbitMQ consumer
        if self._channel and not self._channel.is_closed:
            self._channel.stop_consuming()
        
        # Stop polling thread if running
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=5.0)
        
        self._log_final_stats()
    
    def _setup_connection(self) -> None:
        """Set up RabbitMQ connection"""
        credentials = pika.PlainCredentials(
            self._settings.rabbitmq.username,
            self._settings.rabbitmq.password
        )
        
        parameters = pika.ConnectionParameters(
            host=self._settings.rabbitmq.host,
            port=self._settings.rabbitmq.port,
            credentials=credentials
        )
        
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        
        # Set QoS to process one message at a time
        self._channel.basic_qos(
            prefetch_count=self._settings.rabbitmq.prefetch_count
        )
        
        logger.info(f"Connected to RabbitMQ at {self._settings.rabbitmq.host}:{self._settings.rabbitmq.port}")
    
    def _setup_queues(self) -> None:
        """Set up RabbitMQ queues"""
        # Declare execution queue
        self._channel.queue_declare(
            queue=self._settings.rabbitmq.execution_queue,
            durable=True
        )
        
        # Declare result queue
        self._channel.queue_declare(
            queue=self._settings.rabbitmq.result_queue,
            durable=True
        )
        
        logger.info(
            f"Queues declared: {self._settings.rabbitmq.execution_queue} -> "
            f"{self._settings.rabbitmq.result_queue}"
        )
    
    def _start_consuming(self) -> None:
        """Start consuming messages from the execution queue"""
        self._is_running = True
        
        # Set up consumer
        self._channel.basic_consume(
            queue=self._settings.rabbitmq.execution_queue,
            on_message_callback=self._process_message,
            auto_ack=False
        )
        
        logger.info(f"Worker {self._settings.worker_id} waiting for messages...")
        
        # Start consuming (blocking)
        self._channel.start_consuming()
    
    @measure_time
    def _process_message(
        self,
        channel: BlockingChannel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes
    ) -> None:
        """
        Process a single RabbitMQ message
        
        Args:
            channel: RabbitMQ channel
            method: Delivery method
            properties: Message properties
            body: Message body
        """
        request = None
        
        try:
            # Parse message
            message_data = json.loads(body.decode('utf-8'))
            request = JobExecutionRequest.from_message(message_data)
            
            # Process the job using common method
            self._process_job(request)
            
            # Send result via RabbitMQ
            # Note: _process_job already handles sending to coordinator
            # For RabbitMQ mode, we might also want to send via queue
            
            # Acknowledge message
            channel.basic_ack(delivery_tag=method.delivery_tag)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            
        except Exception as e:
            logger.error(f"Failed to process RabbitMQ message: {e}", exc_info=True)
            
            # Create error result if we have a request
            if request:
                try:
                    error_result = ExecutionResult(
                        job_id=request.job_id,
                        status='failed',
                        execution_time=0.0,
                        error=str(e)
                    )
                    self._send_result(error_result)
                except Exception as send_error:
                    logger.error(f"Failed to send error result: {send_error}")
            
            # Reject message (don't requeue to avoid infinite loops)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    
    def _send_result(self, result: ExecutionResult) -> None:
        """Send execution result to result queue"""
        try:
            result_message = json.dumps(result.to_dict())
            
            self._channel.basic_publish(
                exchange='',
                routing_key=self._settings.rabbitmq.result_queue,
                body=result_message,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    message_id=result.job_id,
                    timestamp=int(time.time())
                )
            )
            
            logger.debug(f"Sent result for job {result.job_id}")
            
        except Exception as e:
            logger.error(f"Failed to send result for job {result.job_id}: {e}")
            raise
    
    def _update_stats(self, result: ExecutionResult, processing_time: float) -> None:
        """Update worker statistics"""
        self._stats['jobs_processed'] += 1
        
        if result.status == 'success':
            self._stats['jobs_successful'] += 1
        else:
            self._stats['jobs_failed'] += 1
        
        self._stats['total_execution_time'] += processing_time
        
        # Log stats periodically
        if self._stats['jobs_processed'] % 10 == 0:
            self._log_stats()
    
    def _log_stats(self) -> None:
        """Log current worker statistics"""
        uptime = time.time() - self._stats['start_time'] if self._stats['start_time'] else 0
        avg_time = (
            self._stats['total_execution_time'] / self._stats['jobs_processed']
            if self._stats['jobs_processed'] > 0 else 0
        )
        
        logger.info(
            f"📊 Worker Stats - Processed: {self._stats['jobs_processed']}, "
            f"Success: {self._stats['jobs_successful']}, "
            f"Failed: {self._stats['jobs_failed']}, "
            f"Avg Time: {avg_time:.2f}s, "
            f"Uptime: {uptime:.1f}s"
        )
    
    def _log_final_stats(self) -> None:
        """Log final statistics before shutdown"""
        logger.info("📈 Final Worker Statistics:")
        logger.info(f"  Jobs Processed: {self._stats['jobs_processed']}")
        logger.info(f"  Successful: {self._stats['jobs_successful']}")
        logger.info(f"  Failed: {self._stats['jobs_failed']}")
        
        if self._stats['jobs_processed'] > 0:
            success_rate = (self._stats['jobs_successful'] / self._stats['jobs_processed']) * 100
            avg_time = self._stats['total_execution_time'] / self._stats['jobs_processed']
            logger.info(f"  Success Rate: {success_rate:.1f}%")
            logger.info(f"  Average Execution Time: {avg_time:.2f}s")
        
        if self._stats['start_time']:
            uptime = time.time() - self._stats['start_time']
            logger.info(f"  Total Uptime: {uptime:.1f}s")
    
    def _cleanup_connection(self) -> None:
        """Clean up RabbitMQ connection"""
        try:
            if self._connection and not self._connection.is_closed:
                self._connection.close()
                logger.info("RabbitMQ connection closed")
        except Exception as e:
            logger.error(f"Error closing RabbitMQ connection: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current worker statistics"""
        stats = self._stats.copy()
        if stats['start_time']:
            stats['uptime_seconds'] = time.time() - stats['start_time']
        return stats