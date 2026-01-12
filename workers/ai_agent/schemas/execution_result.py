from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class ExecutionResult(BaseModel):
    """Result from code execution"""
    
    success: bool
    stdout: str
    stderr: str
    return_code: int
    execution_time: float
    output_files: List[Dict[str, Any]]
    error_message: Optional[str] = None