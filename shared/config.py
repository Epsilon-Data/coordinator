import os
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# Auto-load .env file from project root
from dotenv import load_dotenv

_logger = logging.getLogger(__name__)

# Find project root (where .env is located)
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent  # Go up from shared/ to project root

# Load .env file
_env_file = _project_root / '.env'
if _env_file.exists():
    load_dotenv(_env_file)
    _logger.debug("Loaded environment from %s", _env_file)
else:
    # Try current working directory
    _cwd_env = Path.cwd() / '.env'
    if _cwd_env.exists():
        load_dotenv(_cwd_env)
        _logger.debug("Loaded environment from %s", _cwd_env)


@dataclass
class Config:
    """Configuration for Epsilon Coordinator services"""

    # Database settings
    database_url: str = os.getenv("DATABASE_URL")

    def __post_init__(self):
        """Validate required configuration after initialization"""
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required but not set")

    # Shared storage settings
    shared_storage_path: str = os.getenv("SHARED_STORAGE_PATH", "/shared/epsilon")

    # Worker settings
    worker_name: Optional[str] = os.getenv("WORKER_NAME")
    worker_concurrency: int = int(os.getenv("WORKER_CONCURRENCY", "1"))

    # AI Agent configuration
    ai_agent_enabled: bool = os.getenv("AI_AGENT_ENABLED", "false").lower() == "true"

    # Polling configuration
    polling_interval: int = int(os.getenv("POLLING_INTERVAL", "5"))
    batch_size: int = int(os.getenv("BATCH_SIZE", "10"))


config = Config()