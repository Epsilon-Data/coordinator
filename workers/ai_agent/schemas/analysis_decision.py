from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class CodeViolation(BaseModel):
    """Individual code policy violation"""
    file: str
    line: int
    field: str
    code: str
    type: str  # 'attribute_access', 'dictionary_access', 'print_statement'


class AnalysisDecision(BaseModel):
    """Final analysis decision from CrewAI agents"""
    
    approved: bool
    confidence_score: float  # 0.0 to 1.0
    reasoning: str
    risks_identified: List[str]
    recommendations: List[str]
    pii_details: Optional[List[CodeViolation]] = []  # Detailed code violations
    analyzed_files: Optional[List[str]] = []  # Files that were analyzed