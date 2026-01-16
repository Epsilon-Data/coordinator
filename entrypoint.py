#!/usr/bin/env python3
"""
Unified entrypoint for Epsilon Coordinator workers.

Usage:
    WORKER_MODE=fetcher python entrypoint.py
    WORKER_MODE=clone python entrypoint.py
    WORKER_MODE=executor python entrypoint.py
    WORKER_MODE=ai python entrypoint.py
"""
import os
import sys
import logging
import importlib
from typing import Dict, Type, Any

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('epsilon.entrypoint')

# Worker registry: mode -> (module_path, class_name)
WORKER_REGISTRY: Dict[str, tuple] = {
    'fetcher': ('workers.job_fetcher.job_fetcher', 'JobFetcherWorker'),
    'clone': ('workers.clone.clone_worker', 'CloneWorker'),
    'executor': ('workers.executor.worker', 'ExecutorWorker'),
    'ai': ('workers.ai_agent.ai_agent_worker', 'AIAgentWorker'),
}

# Aliases for convenience
WORKER_ALIASES: Dict[str, str] = {
    'job-fetcher': 'fetcher',
    'job_fetcher': 'fetcher',
    'clone-worker': 'clone',
    'executor-worker': 'executor',
    'ai-agent': 'ai',
    'ai_agent': 'ai',
}


def get_worker_class(mode: str) -> Type[Any]:
    """
    Dynamically import and return the worker class for the given mode.

    Args:
        mode: Worker mode (fetcher, clone, executor, ai)

    Returns:
        Worker class

    Raises:
        ValueError: If mode is not recognized
        ImportError: If worker module cannot be imported
    """
    # Resolve aliases
    resolved_mode = WORKER_ALIASES.get(mode, mode)

    if resolved_mode not in WORKER_REGISTRY:
        available = list(WORKER_REGISTRY.keys()) + list(WORKER_ALIASES.keys())
        raise ValueError(
            f"Unknown worker mode: '{mode}'. "
            f"Available modes: {', '.join(sorted(set(available)))}"
        )

    module_path, class_name = WORKER_REGISTRY[resolved_mode]

    try:
        module = importlib.import_module(module_path)
        worker_class = getattr(module, class_name)
        return worker_class
    except ImportError as e:
        logger.error(f"Failed to import worker module '{module_path}': {e}")
        raise
    except AttributeError as e:
        logger.error(f"Worker class '{class_name}' not found in '{module_path}': {e}")
        raise


def main() -> None:
    """Main entrypoint for worker execution."""
    mode = os.getenv('WORKER_MODE', 'executor').lower().strip()

    logger.info("=" * 60)
    logger.info(f"EPSILON COORDINATOR - Worker Entrypoint")
    logger.info(f"Worker Mode: {mode}")
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    logger.info("=" * 60)

    try:
        # Get worker class
        worker_class = get_worker_class(mode)
        logger.info(f"Loaded worker: {worker_class.__name__}")

        # Create and run worker
        worker = worker_class()
        logger.info(f"Starting {worker_class.__name__}...")
        worker.run()

    except KeyboardInterrupt:
        logger.info("Received shutdown signal, stopping worker...")
        sys.exit(0)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.error("Make sure all dependencies are installed for this worker mode")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Worker failed with error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Worker stopped")


if __name__ == '__main__':
    main()