"""
Main entry point for the Epsilon Executor Worker
"""
import sys
import signal
import os
from typing import Optional

from config import get_settings, validate_environment
from utils import setup_logging, get_logger
from factories import ExecutorFactory
from exceptions import ConfigurationError, ExecutorError
from worker import ExecutorWorker, WorkerMode


def setup_signal_handlers(worker: ExecutorWorker) -> None:
    """Set up graceful shutdown signal handlers"""
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        worker.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def get_environment_config():
    """Get configuration from environment variables"""
    return {
        'config_check': os.getenv('CONFIG_CHECK', 'false').lower() == 'true',
        'mock': os.getenv('USE_MOCK', 'false').lower() == 'true',
        'log_level': os.getenv('LOG_LEVEL', 'INFO'),
        'mode': os.getenv('JOB_FETCH_MODE', 'polling')
    }


def validate_runtime_environment() -> None:
    """Validate the runtime environment configuration"""
    logger.info("Validating environment configuration...")
    
    try:
        settings = get_settings()
        validate_environment(settings)
        logger.info("✅ Environment validation passed")
        
        # Log key configuration
        logger.info(f"Worker ID: {settings.worker_id}")
        logger.info(f"Environment: {settings.environment}")
        logger.info(f"Use local enclave: {settings.enclave.use_local_client}")
        logger.info(f"Storage path: {settings.storage.shared_storage_path}")
        
    except Exception as e:
        logger.error(f"❌ Configuration validation failed: {e}")
        raise ConfigurationError(f"Configuration validation failed: {e}")


def create_executor(use_mock: bool = False):
    """Create executor instance"""
    settings = get_settings()
    
    if use_mock:
        logger.info("Creating mock executor for testing")
        return ExecutorFactory.create_mock_executor(settings)
    else:
        logger.info("Creating production executor")
        return ExecutorFactory.create_executor(settings)


def main() -> None:
    """Main entry point"""
    config = get_environment_config()
    
    # Set up logging first
    global logger
    logger = setup_logging(
        name='epsilon.executor',
        level=config['log_level']
    )
    
    logger.info("🚀 Starting Epsilon Executor Worker")
    logger.info("=" * 60)
    
    try:
        # Validate environment
        validate_runtime_environment()
        
        # Configuration check mode
        if config['config_check']:
            logger.info("✅ Configuration check passed")
            return
        
        # Create executor
        executor = create_executor(use_mock=config['mock'])
        
        # Check if executor is ready
        if not executor.is_ready:
            raise RuntimeError("Executor is not ready to accept jobs")
        
        logger.info("✅ Executor is ready")
        
        # Get worker mode from environment
        mode = WorkerMode(config['mode'])
        logger.info(f"Worker mode: {mode.value}")
        
        # Create and start worker
        worker = ExecutorWorker(executor, mode)
        
        # Set up signal handlers for graceful shutdown
        setup_signal_handlers(worker)
        
        # Start the worker
        logger.info("🏃 Starting worker loop...")
        worker.start()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("👋 Executor worker stopped")


if __name__ == "__main__":
    main()