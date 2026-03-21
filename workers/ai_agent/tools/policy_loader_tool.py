import logging
from typing import Dict, Any, Optional

from crewai.tools import BaseTool

from workers.ai_agent.security_constants import (
    BLOCKED_IMPORTS, SAFE_IMPORTS, BLOCKED_FUNCTIONS,
    DEFAULT_PII_FIELDS, THREAT_TIERS,
)
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
            "pii_fields": list(DEFAULT_PII_FIELDS),
            "blocked_imports": sorted(BLOCKED_IMPORTS),
            "blocked_functions": list(BLOCKED_FUNCTIONS),
            "safe_imports": sorted(SAFE_IMPORTS),
            "threat_tiers": dict(THREAT_TIERS),
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
                    if "pii_fields" in custom and isinstance(custom["pii_fields"], list):
                        existing = set(default_policy["pii_fields"])
                        for pii_field in custom["pii_fields"]:
                            if pii_field not in existing:
                                default_policy["pii_fields"].append(pii_field)
                    if "safe_imports" in custom and isinstance(custom["safe_imports"], list):
                        existing = set(default_policy["safe_imports"])
                        for imp in custom["safe_imports"]:
                            if imp not in existing:
                                default_policy["safe_imports"].append(imp)
                    if "approval_rules" in custom and isinstance(custom["approval_rules"], dict):
                        default_policy["approval_rules"].update(custom["approval_rules"])

        return default_policy
