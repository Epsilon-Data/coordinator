"""Shared utilities for AI agent build.yml parsing and script discovery."""
import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Standardized fallback order for main script discovery
MAIN_SCRIPT_FALLBACKS = ["main.py", "run.py", "app.py", "analysis.py", "example_analysis.py"]


def parse_build_config(repo_path: str) -> Optional[dict]:
    """Parse build/build.yml and return the config dict, or None if not found."""
    build_yml = Path(repo_path) / "build" / "build.yml"
    if not build_yml.exists():
        return None
    try:
        with open(build_yml, "r") as f:
            config = yaml.safe_load(f)
        return config if isinstance(config, dict) else None
    except Exception as e:
        logger.warning(f"Error reading build.yml: {e}")
        return None


def find_main_script(repo_path: str) -> tuple[str, str]:
    """Find and read the main analysis script.

    Returns (content, filename). Falls back through MAIN_SCRIPT_FALLBACKS.
    Returns ("", "No main script found") if nothing is found.
    """
    repo = Path(repo_path)
    config = parse_build_config(repo_path)

    # Try build.yml script_file first
    if config and "analysis" in config and "script_file" in config["analysis"]:
        script_file = config["analysis"]["script_file"]
        script_path = repo / script_file
        if script_path.exists():
            try:
                return script_path.read_text(encoding="utf-8"), script_file
            except Exception as e:
                logger.warning(f"Error reading {script_file}: {e}")

    # Fallback to standard main files
    for main_file in MAIN_SCRIPT_FALLBACKS:
        script_path = repo / main_file
        if script_path.exists():
            try:
                return script_path.read_text(encoding="utf-8"), main_file
            except Exception as e:
                logger.warning(f"Error reading {main_file}: {e}")
                continue

    return "", "No main script found"


def find_requirements_path(repo_path: str) -> Optional[Path]:
    """Find the requirements file from build.yml or fallback to requirements.txt."""
    repo = Path(repo_path)
    config = parse_build_config(repo_path)

    if config and "analysis" in config and "requirements" in config["analysis"]:
        requirements = config["analysis"]["requirements"]
        # Try build folder first
        req_path = repo / "build" / requirements
        if req_path.exists():
            return req_path
        # Try repo root
        req_path = repo / requirements
        if req_path.exists():
            return req_path

    # Fallback to standard requirements.txt
    req_path = repo / "requirements.txt"
    if req_path.exists():
        return req_path

    return None
