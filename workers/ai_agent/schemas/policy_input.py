from pydantic import BaseModel
from typing import List, Dict, Any


class PolicyAnalysisInput(BaseModel):
    """Input for policy analysis"""
    
    job_id: str
    repo_path: str
    dummy_data_path: str
    pii_fields: List[str]
    sensitive_patterns: List[str]
    approval_rules: Dict[str, Any]