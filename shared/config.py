import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote


@dataclass
class Config:
    """Configuration for Epsilon Coordinator services"""
    
    # RabbitMQ settings
    rabbitmq_host: str = os.getenv("RABBITMQ_HOST", "rabbitmq")
    rabbitmq_port: int = int(os.getenv("RABBITMQ_PORT", "5672"))
    rabbitmq_user: str = os.getenv("RABBITMQ_USER", "epsilon")
    rabbitmq_pass: str = os.getenv("RABBITMQ_PASS", "epsilon")
    rabbitmq_vhost: str = os.getenv("RABBITMQ_VHOST", "/")
    
    # Database settings - using existing Neon database
    database_url: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://user:pass@localhost:5432/epsilon"
    )
    
    # Shared storage settings
    shared_storage_path: str = os.getenv("SHARED_STORAGE_PATH", "/shared/epsilon")
    
    # Worker settings
    worker_name: Optional[str] = os.getenv("WORKER_NAME")
    worker_concurrency: int = int(os.getenv("WORKER_CONCURRENCY", "1"))
    
    # Exchange and queue names
    exchange_name: str = "epsilon.jobs"
    exchange_type: str = "topic"
    
    # Queue configurations
    clone_queue: str = "epsilon.clone.queue"
    ai_queue: str = "epsilon.ai.queue"
    execute_queue: str = "epsilon.execute.queue"
    notify_queue: str = "epsilon.notify.queue"
    
    # Routing keys
    routing_key_created: str = "job.created"
    routing_key_cloned: str = "job.cloned"
    routing_key_approved: str = "job.approved"
    routing_key_rejected: str = "job.rejected"
    routing_key_completed: str = "job.completed"
    routing_key_failed: str = "job.failed"
    
    @property
    def rabbitmq_url(self) -> str:
        """Get RabbitMQ connection URL"""
        vhost = quote(self.rabbitmq_vhost, safe='')
        return f"amqp://{self.rabbitmq_user}:{self.rabbitmq_pass}@{self.rabbitmq_host}:{self.rabbitmq_port}/{vhost}"


config = Config()