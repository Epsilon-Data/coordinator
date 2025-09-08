"""
Epsilon Coordinator API Module
Provides REST API endpoints for accessing job logs and result files
"""

__version__ = "1.0.0"
__author__ = "Epsilon Team"

from .files_api import JobFilesAPI

__all__ = ["JobFilesAPI"]