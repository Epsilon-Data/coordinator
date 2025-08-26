import json
import logging
import signal
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from .config import config
from .database import job_repository

logger = logging.getLogger(__name__)


class BaseWorker(ABC):
    """Base class for all worker services"""
    
    def __init__(
        self, 
        worker_name: str,
        queue_name: str,
        routing_keys: list[str]
    ):
        self.worker_name = worker_name
        self.queue_name = queue_name
        self.routing_keys = routing_keys
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[BlockingChannel] = None
        self.should_stop = False
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.should_stop = True
        if self.connection and not self.connection.is_closed:
            self.connection.close()
        sys.exit(0)
        
    def connect(self):
        """Connect to RabbitMQ and set up exchanges/queues"""
        logger.info(f"Connecting to RabbitMQ at {config.rabbitmq_host}...")
        
        # Create connection with retry
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.connection = pika.BlockingConnection(
                    pika.URLParameters(config.rabbitmq_url)
                )
                self.channel = self.connection.channel()
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                    time.sleep(5)
                else:
                    raise
                    
        # Declare exchange
        self.channel.exchange_declare(
            exchange=config.exchange_name,
            exchange_type=config.exchange_type,
            durable=True
        )
        
        # Declare queue
        self.channel.queue_declare(
            queue=self.queue_name,
            durable=True,
            arguments={
                "x-message-ttl": 3600000,  # 1 hour TTL
                "x-max-length": 10000      # Max 10k messages
            }
        )
        
        # Bind queue to exchange with routing keys
        for routing_key in self.routing_keys:
            self.channel.queue_bind(
                exchange=config.exchange_name,
                queue=self.queue_name,
                routing_key=routing_key
            )
            
        # Set QoS
        self.channel.basic_qos(prefetch_count=config.worker_concurrency)
        
        logger.info(f"Connected and bound to queue {self.queue_name}")
        
    def publish_message(
        self, 
        routing_key: str, 
        message: Dict[str, Any],
        properties: Optional[BasicProperties] = None
    ):
        """Publish a message to the exchange"""
        if not properties:
            properties = pika.BasicProperties(
                delivery_mode=2,  # Persistent
                content_type='application/json'
            )
            
        self.channel.basic_publish(
            exchange=config.exchange_name,
            routing_key=routing_key,
            body=json.dumps(message),
            properties=properties
        )
        
        logger.debug(f"Published message with routing key {routing_key}: {message}")
        
    def _on_message(
        self, 
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes
    ):
        """Handle incoming message"""
        try:
            # Parse message
            message = json.loads(body.decode('utf-8'))
            job_id = message.get('job_id')
            
            logger.info(f"Received message for job {job_id}: {method.routing_key}")
            
            # Log to database
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"Processing started with routing key: {method.routing_key}",
                metadata={"routing_key": method.routing_key}
            )
            
            # Process message
            self.process_message(message)
            
            # Acknowledge message
            channel.basic_ack(delivery_tag=method.delivery_tag)
            
            logger.info(f"Successfully processed job {job_id}")
            
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            
            # Log error to database if we have job_id
            if 'job_id' in locals() and job_id:
                try:
                    job_repository.add_job_log(
                        job_id=job_id,
                        worker_name=self.worker_name,
                        message=f"Processing failed: {str(e)}",
                        level="error",
                        metadata={"error": str(e), "error_type": type(e).__name__}
                    )
                    
                    # Update job status to failed
                    job_repository.update_job_status(
                        job_id=job_id,
                        status="failed",
                        error_message=str(e)
                    )
                    
                    # Publish failure message
                    self.publish_message(
                        routing_key=config.routing_key_failed,
                        message={
                            'job_id': job_id,
                            'worker': self.worker_name,
                            'error': str(e)
                        }
                    )
                except Exception as db_error:
                    logger.error(f"Failed to update database: {db_error}")
                    
            # Reject message and don't requeue (to avoid infinite loops)
            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=False
            )
            
    @abstractmethod
    def process_message(self, message: Dict[str, Any]):
        """Process a message - must be implemented by subclasses"""
        pass
        
    def start(self):
        """Start consuming messages"""
        logger.info(f"Starting {self.worker_name} worker...")
        
        # Connect to RabbitMQ
        self.connect()
        
        # Start consuming
        self.channel.basic_consume(
            queue=self.queue_name,
            on_message_callback=self._on_message,
            auto_ack=False
        )
        
        logger.info(f"{self.worker_name} worker started. Waiting for messages...")
        
        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            self.stop()
            
    def stop(self):
        """Stop the worker gracefully"""
        logger.info(f"Stopping {self.worker_name} worker...")
        
        if self.channel and not self.channel.is_closed:
            self.channel.stop_consuming()
            
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            
        logger.info(f"{self.worker_name} worker stopped")