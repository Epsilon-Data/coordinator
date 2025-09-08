"""
API for accessing job result files
Handles fetching execution results, AI analysis results, and file summaries
"""
import json
import os
from typing import Optional, Dict, Any
import sys

# Add parent directory to path for shared module imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import config


class JobFilesAPI:
    """API for accessing job result files"""
    
    @staticmethod
    def get_execution_result(job_id: str) -> Dict[str, Any]:
        """
        Get execution result file for a job
        
        Args:
            job_id: The job ID
            
        Returns:
            Dictionary with execution result data
        """
        try:
            # Try different result paths in order of preference
            result_paths = [
                f"{config.shared_storage_path}/enclave_execution_results/{job_id}/execution_result.json",
                f"{config.shared_storage_path}/ai_execution_results/{job_id}/ai_execution_result.json",
                f"{config.shared_storage_path}/execution_results/{job_id}/execution_result.json"
            ]
            
            for result_path in result_paths:
                if os.path.exists(result_path):
                    with open(result_path, 'r', encoding='utf-8') as f:
                        result_data = json.load(f)
                    
                    # Extract result type from path
                    result_type = os.path.dirname(result_path).split('/')[-1]
                    
                    return {
                        "success": True,
                        "job_id": job_id,
                        "file_path": result_path,
                        "result_type": result_type,
                        "data": result_data,
                        "file_size": os.path.getsize(result_path),
                        "last_modified": os.path.getmtime(result_path)
                    }
            
            return {
                "success": False,
                "error": "No execution result file found",
                "job_id": job_id,
                "searched_paths": [path.replace(config.shared_storage_path, "...") for path in result_paths]
            }
            
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Invalid JSON in result file: {str(e)}",
                "job_id": job_id
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }
    
    @staticmethod
    def get_ai_analysis_result(job_id: str) -> Dict[str, Any]:
        """
        Get AI analysis result file for a job
        
        Args:
            job_id: The job ID
            
        Returns:
            Dictionary with AI analysis result data
        """
        try:
            analysis_path = f"{config.shared_storage_path}/ai_analysis/{job_id}/analysis_result.json"
            
            if not os.path.exists(analysis_path):
                return {
                    "success": False,
                    "error": "AI analysis result file not found",
                    "job_id": job_id,
                    "path": analysis_path.replace(config.shared_storage_path, "...")
                }
            
            with open(analysis_path, 'r', encoding='utf-8') as f:
                analysis_data = json.load(f)
            
            return {
                "success": True,
                "job_id": job_id,
                "file_path": analysis_path,
                "data": analysis_data,
                "file_size": os.path.getsize(analysis_path),
                "last_modified": os.path.getmtime(analysis_path)
            }
            
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Invalid JSON in AI analysis file: {str(e)}",
                "job_id": job_id
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }
    
    @staticmethod
    def get_job_files_summary(job_id: str) -> Dict[str, Any]:
        """
        Get a summary of all available files for a job
        
        Args:
            job_id: The job ID
            
        Returns:
            Dictionary with available files information
        """
        try:
            files_summary = {
                "job_id": job_id,
                "available_files": []
            }
            
            # Check for different types of result files
            file_checks = [
                {
                    "type": "execution_result",
                    "category": "execution",
                    "path": f"{config.shared_storage_path}/enclave_execution_results/{job_id}/execution_result.json",
                    "description": "Secure enclave execution result"
                },
                {
                    "type": "ai_execution_result", 
                    "category": "execution",
                    "path": f"{config.shared_storage_path}/ai_execution_results/{job_id}/ai_execution_result.json",
                    "description": "AI-supervised execution result"
                },
                {
                    "type": "execution_result_alt",
                    "category": "execution",
                    "path": f"{config.shared_storage_path}/execution_results/{job_id}/execution_result.json",
                    "description": "Standard execution result"
                },
                {
                    "type": "ai_analysis_result",
                    "category": "analysis",
                    "path": f"{config.shared_storage_path}/ai_analysis/{job_id}/analysis_result.json",
                    "description": "AI pre-execution analysis result"
                }
            ]
            
            for file_check in file_checks:
                if os.path.exists(file_check["path"]):
                    try:
                        stat = os.stat(file_check["path"])
                        files_summary["available_files"].append({
                            "type": file_check["type"],
                            "category": file_check["category"],
                            "description": file_check["description"],
                            "path": file_check["path"],
                            "relative_path": file_check["path"].replace(f"{config.shared_storage_path}/", ""),
                            "size_bytes": stat.st_size,
                            "last_modified": stat.st_mtime,
                            "readable": os.access(file_check["path"], os.R_OK)
                        })
                    except Exception as e:
                        # Log error but continue processing other files
                        print(f"Error accessing file {file_check['path']}: {e}")
                        continue
            
            return {
                "success": True,
                **files_summary,
                "files_count": len(files_summary["available_files"]),
                "total_size_bytes": sum(f["size_bytes"] for f in files_summary["available_files"])
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }