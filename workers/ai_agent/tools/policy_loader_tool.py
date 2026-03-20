import logging
from typing import Dict, Any, Optional

from crewai.tools import BaseTool

from workers.ai_agent.utils import parse_build_config

logger = logging.getLogger(__name__)


class PolicyLoaderTool(BaseTool):
    name: str = "Policy Loader"
    description: str = "Load security policies and threat detection rules for code review"

    repo_path: Optional[str] = None

    def _run(self) -> Dict[str, Any]:
        """Load security policy configuration, merging custom policy from build.yml if available."""

        default_policy = {
            "name": "Enclave Code Security Policy",
            "version": "2.0",
            "description": "Security policy for researcher-submitted code executing inside TEE with patient data",
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
            "blocked_imports": [
                "socket", "subprocess", "http", "http.client", "http.server",
                "urllib", "urllib.request", "requests", "httpx", "aiohttp",
                "paramiko", "ftplib", "smtplib", "telnetlib",
                "ctypes", "cffi", "pty", "shlex",
                "pickle", "marshal", "shelve", "jsonpickle",
            ],
            "blocked_functions": [
                "eval", "exec", "compile", "__import__",
                "os.system", "os.popen", "os.exec",
                "getattr",  # on module objects
            ],
            "safe_imports": [
                "pandas", "numpy", "scipy", "sklearn", "matplotlib", "seaborn",
                "json", "csv", "pathlib", "datetime", "math", "statistics",
                "collections", "itertools", "typing", "dataclasses", "enum",
                "re", "string", "textwrap", "io", "copy", "functools",
                "openpyxl", "xlrd",
            ],
            "threat_tiers": {
                "critical": "Data exfiltration, code injection, reverse shells — immediate reject",
                "high": "Filesystem escape, obfuscated code, credential harvesting — reject unless justified",
                "medium": "Suspicious imports, system reconnaissance, excessive output — flag for review",
                "low": "Minor concerns, informational — does not block execution"
            },
            "approval_rules": {
                "reject_on_any_critical": True,
                "reject_on_high_in_main_path": True,
                "reject_on_aggregate_score_gte": 50,
                "approve_requires_clean_execution": True,
                "bias": "reject_when_uncertain"
            },
        }

        # Merge custom policy from build.yml if repo_path is set
        if self.repo_path:
            config = parse_build_config(self.repo_path)
            if config and "analysis" in config and "policy" in config["analysis"]:
                custom = config["analysis"]["policy"]
                logger.info("Merging custom policy from build.yml")
                if isinstance(custom, dict):
                    # Extend PII fields with custom ones
                    if "pii_fields" in custom and isinstance(custom["pii_fields"], list):
                        existing = set(default_policy["pii_fields"])
                        for field in custom["pii_fields"]:
                            if field not in existing:
                                default_policy["pii_fields"].append(field)
                    # Extend safe imports
                    if "safe_imports" in custom and isinstance(custom["safe_imports"], list):
                        existing = set(default_policy["safe_imports"])
                        for imp in custom["safe_imports"]:
                            if imp not in existing:
                                default_policy["safe_imports"].append(imp)
                    # Merge approval rules
                    if "approval_rules" in custom and isinstance(custom["approval_rules"], dict):
                        default_policy["approval_rules"].update(custom["approval_rules"])

        return default_policy
