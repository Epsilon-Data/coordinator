import json
from pathlib import Path
from typing import Dict, Any, List
from crewai.tools import BaseTool


class PolicyLoaderTool(BaseTool):
    name: str = "Policy Loader" 
    description: str = "Load privacy policies and PII field definitions"
    
    def _run(self) -> Dict[str, Any]:
        """Load policy configuration"""
        
        # Default hardcoded healthcare policy
        default_policy = {
            "name": "Healthcare Privacy Policy",
            "version": "1.0",
            "description": "Privacy policy for healthcare data analysis",
            "pii_fields": [
                "name",
                "first_name", 
                "last_name",
                "email",
                "phone",
                "ssn",
                "social_security",
                "address",
                "date_of_birth",
                "dob",
                "patient_id",
                "mrn",
                "medical_record_number",
                "insurance_number",
                "credit_card",
                "account_number"
            ],
            "sensitive_patterns": [
                r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
                r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
                r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"  # Phone
            ],
            "approval_rules": {
                "allow_if_no_pii": True,
                "reject_if_pii_detected": True,
                "require_anonymization": False
            },
            "risk_levels": {
                "no_pii": "LOW",
                "potential_pii": "MEDIUM",
                "confirmed_pii": "HIGH"
            }
        }
        return default_policy