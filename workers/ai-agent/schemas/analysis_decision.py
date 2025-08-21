from pydantic import BaseModel
from typing import List


class AnalysisDecision(BaseModel):
    """Final analysis decision from CrewAI agents"""
    
    approved: bool
    confidence_score: float  # 0.0 to 1.0
    reasoning: str
    risks_identified: List[str]
    recommendations: List[str]