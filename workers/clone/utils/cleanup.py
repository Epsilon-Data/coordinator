import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_repository(repo_path: Path) -> bool:
    """
    Clean up a repository directory
    
    Args:
        repo_path: Path to repository to clean up
        
    Returns:
        True if successful, False otherwise
    """
    if not isinstance(repo_path, Path):
        repo_path = Path(repo_path)
        
    if repo_path.exists():
        try:
            shutil.rmtree(repo_path)
            logger.info(f"Successfully cleaned up: {repo_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to cleanup {repo_path}: {e}")
            return False
    else:
        logger.warning(f"Path does not exist: {repo_path}")
        return False