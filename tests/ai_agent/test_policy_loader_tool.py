"""
Tests for workers/ai_agent/tools/policy_loader_tool.py
"""
import tempfile
import shutil
from pathlib import Path

import pytest

from workers.ai_agent.tools.policy_loader_tool import PolicyLoaderTool


class TestPolicyLoaderTool:
    """Tests for PolicyLoaderTool."""

    def test_default_policy(self):
        """Returns default enclave security policy when no repo_path."""
        tool = PolicyLoaderTool()
        policy = tool._run()

        assert policy["name"] == "Enclave Code Security Policy"
        assert policy["version"] == "2.0"
        assert "ssn" in policy["pii_fields"]
        assert "email" in policy["pii_fields"]
        assert "socket" in policy["blocked_imports"]
        assert "subprocess" in policy["blocked_imports"]
        assert "pandas" in policy["safe_imports"]
        assert "numpy" in policy["safe_imports"]
        assert policy["approval_rules"]["reject_on_any_critical"] is True
        assert policy["approval_rules"]["bias"] == "reject_when_uncertain"

    def test_threat_tiers_present(self):
        """All four threat tiers are defined."""
        tool = PolicyLoaderTool()
        policy = tool._run()

        assert "critical" in policy["threat_tiers"]
        assert "high" in policy["threat_tiers"]
        assert "medium" in policy["threat_tiers"]
        assert "low" in policy["threat_tiers"]

    def test_custom_from_build_yml(self):
        """Custom policy fields from build.yml are merged."""
        tmpdir = tempfile.mkdtemp()
        try:
            build_dir = Path(tmpdir) / "build"
            build_dir.mkdir()
            (build_dir / "build.yml").write_text(
                "analysis:\n"
                "  script_file: main.py\n"
                "  policy:\n"
                "    pii_fields:\n"
                "      - passport_number\n"
                "    safe_imports:\n"
                "      - custom_lib\n"
                "    approval_rules:\n"
                "      reject_on_aggregate_score_gte: 100\n"
            )

            tool = PolicyLoaderTool(repo_path=tmpdir)
            policy = tool._run()

            # Custom fields added
            assert "passport_number" in policy["pii_fields"]
            assert "custom_lib" in policy["safe_imports"]
            # Custom rule merged
            assert policy["approval_rules"]["reject_on_aggregate_score_gte"] == 100
            # Defaults preserved
            assert "ssn" in policy["pii_fields"]
            assert "pandas" in policy["safe_imports"]
            assert policy["approval_rules"]["reject_on_any_critical"] is True
        finally:
            shutil.rmtree(tmpdir)

    def test_no_duplicate_fields(self):
        """Duplicate fields from custom policy are not added."""
        tmpdir = tempfile.mkdtemp()
        try:
            build_dir = Path(tmpdir) / "build"
            build_dir.mkdir()
            (build_dir / "build.yml").write_text(
                "analysis:\n"
                "  policy:\n"
                "    pii_fields:\n"
                "      - ssn\n"
                "      - email\n"
                "      - new_field\n"
            )

            tool = PolicyLoaderTool(repo_path=tmpdir)
            policy = tool._run()

            assert policy["pii_fields"].count("ssn") == 1
            assert "new_field" in policy["pii_fields"]
        finally:
            shutil.rmtree(tmpdir)

    def test_required_fields_present(self):
        """All required policy sections are always present."""
        tool = PolicyLoaderTool()
        policy = tool._run()

        assert "name" in policy
        assert "version" in policy
        assert "pii_fields" in policy
        assert "blocked_imports" in policy
        assert "blocked_functions" in policy
        assert "safe_imports" in policy
        assert "threat_tiers" in policy
        assert "approval_rules" in policy
        assert isinstance(policy["pii_fields"], list)
        assert isinstance(policy["blocked_imports"], list)
        assert isinstance(policy["safe_imports"], list)

    def test_missing_build_yml_uses_default(self):
        """Falls back to default policy when build.yml has no policy section."""
        tmpdir = tempfile.mkdtemp()
        try:
            build_dir = Path(tmpdir) / "build"
            build_dir.mkdir()
            (build_dir / "build.yml").write_text(
                "analysis:\n  script_file: main.py\n"
            )

            tool = PolicyLoaderTool(repo_path=tmpdir)
            policy = tool._run()

            assert policy["name"] == "Enclave Code Security Policy"
            assert len(policy["blocked_imports"]) > 0
            # Verify constants are used (not hardcoded duplicates)
            assert "socket" in policy["blocked_imports"]
            assert "pandas" in policy["safe_imports"]
        finally:
            shutil.rmtree(tmpdir)
