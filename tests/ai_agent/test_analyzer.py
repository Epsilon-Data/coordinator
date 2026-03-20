"""
Tests for workers/ai_agent/analyzer.py
"""
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import field

import pytest

from workers.ai_agent.analyzer import analyze_repository
from workers.ai_agent.schemas import AnalysisDecision
from workers.ai_agent.utils import find_main_script
from workers.ai_agent.tools.ast_scanner_tool import ScanReport
from workers.ai_agent.tools.output_disclosure_tool import OutputReport


class TestFindMainScript:
    """Tests for find_main_script utility."""

    def test_find_from_build_yml(self, temp_repo_with_script):
        content, filename = find_main_script(temp_repo_with_script)
        assert filename == "main.py"
        assert "Hello from test" in content

    def test_fallback_when_no_build_yml(self):
        tmpdir = tempfile.mkdtemp()
        try:
            Path(tmpdir, "run.py").write_text("print('running')")
            content, filename = find_main_script(tmpdir)
            assert filename == "run.py"
            assert "running" in content
        finally:
            shutil.rmtree(tmpdir)

    def test_fallback_order(self):
        tmpdir = tempfile.mkdtemp()
        try:
            Path(tmpdir, "main.py").write_text("main")
            Path(tmpdir, "run.py").write_text("run")
            _, filename = find_main_script(tmpdir)
            assert filename == "main.py"
        finally:
            shutil.rmtree(tmpdir)

    def test_no_script_found(self):
        tmpdir = tempfile.mkdtemp()
        try:
            content, filename = find_main_script(tmpdir)
            assert content == ""
            assert filename == "No main script found"
        finally:
            shutil.rmtree(tmpdir)


# Patch all CrewAI components + new scanner tools
_ANALYZER_PATCHES = [
    "workers.ai_agent.analyzer.Crew",
    "workers.ai_agent.analyzer.create_decision_task",
    "workers.ai_agent.analyzer.create_analyzer_task",
    "workers.ai_agent.analyzer.create_policy_task",
    "workers.ai_agent.analyzer.create_decision_agent",
    "workers.ai_agent.analyzer.create_analyzer_agent",
    "workers.ai_agent.analyzer.create_policy_agent",
    "workers.ai_agent.analyzer.CodeExecutorTool",
    "workers.ai_agent.analyzer.PolicyLoaderTool",
    "workers.ai_agent.analyzer.ASTSecurityScanner",
    "workers.ai_agent.analyzer.OutputDisclosureTool",
]


def _setup_mocks(MockPolicyTool, MockExecutorTool, MockASTScanner, MockOutputChecker,
                 sample_execution_result, crew_pydantic_result, MockCrew):
    """Helper to set up all mocks consistently."""
    mock_policy_instance = MagicMock()
    mock_policy_instance._run.return_value = {
        "name": "Test Policy",
        "pii_fields": ["ssn", "email"],
    }
    MockPolicyTool.return_value = mock_policy_instance

    mock_executor_instance = MagicMock()
    mock_executor_instance._run.return_value = sample_execution_result
    MockExecutorTool.return_value = mock_executor_instance

    mock_ast_instance = MagicMock()
    mock_ast_instance._run.return_value = ScanReport(safe=True, summary="All clean")
    MockASTScanner.return_value = mock_ast_instance

    mock_output_instance = MagicMock()
    mock_output_instance._run.return_value = OutputReport(safe=True, summary="All clean")
    MockOutputChecker.return_value = mock_output_instance

    mock_crew_output = MagicMock()
    mock_crew_output.pydantic = crew_pydantic_result
    mock_crew_instance = MagicMock()
    mock_crew_instance.kickoff.return_value = mock_crew_output
    MockCrew.return_value = mock_crew_instance


class TestAnalyzeRepository:
    """Tests for analyze_repository function."""

    @patch(_ANALYZER_PATCHES[0])
    @patch(_ANALYZER_PATCHES[1])
    @patch(_ANALYZER_PATCHES[2])
    @patch(_ANALYZER_PATCHES[3])
    @patch(_ANALYZER_PATCHES[4])
    @patch(_ANALYZER_PATCHES[5])
    @patch(_ANALYZER_PATCHES[6])
    @patch(_ANALYZER_PATCHES[7])
    @patch(_ANALYZER_PATCHES[8])
    @patch(_ANALYZER_PATCHES[9])
    @patch(_ANALYZER_PATCHES[10])
    def test_approved_decision(
        self, MockOutputChecker, MockASTScanner,
        MockPolicyTool, MockExecutorTool,
        mock_create_policy_agent, mock_create_analyzer_agent, mock_create_decision_agent,
        mock_create_policy_task, mock_create_analyzer_task, mock_create_decision_task,
        MockCrew,
        sample_execution_result, sample_analysis_decision, temp_repo_with_script,
    ):
        """Returns approved AnalysisDecision on successful analysis."""
        _setup_mocks(MockPolicyTool, MockExecutorTool, MockASTScanner, MockOutputChecker,
                     sample_execution_result, sample_analysis_decision, MockCrew)

        result = analyze_repository(temp_repo_with_script, "JOB-TEST")

        assert isinstance(result, AnalysisDecision)
        assert result.approved is True
        assert result.confidence_score == 0.95
        assert "main.py" in result.analyzed_files

    @patch(_ANALYZER_PATCHES[0])
    @patch(_ANALYZER_PATCHES[1])
    @patch(_ANALYZER_PATCHES[2])
    @patch(_ANALYZER_PATCHES[3])
    @patch(_ANALYZER_PATCHES[4])
    @patch(_ANALYZER_PATCHES[5])
    @patch(_ANALYZER_PATCHES[6])
    @patch(_ANALYZER_PATCHES[7])
    @patch(_ANALYZER_PATCHES[8])
    @patch(_ANALYZER_PATCHES[9])
    @patch(_ANALYZER_PATCHES[10])
    def test_rejected_decision(
        self, MockOutputChecker, MockASTScanner,
        MockPolicyTool, MockExecutorTool,
        mock_create_policy_agent, mock_create_analyzer_agent, mock_create_decision_agent,
        mock_create_policy_task, mock_create_analyzer_task, mock_create_decision_task,
        MockCrew,
        sample_execution_result, sample_rejected_decision, temp_repo_with_script,
    ):
        """Returns rejected AnalysisDecision when threats detected."""
        _setup_mocks(MockPolicyTool, MockExecutorTool, MockASTScanner, MockOutputChecker,
                     sample_execution_result, sample_rejected_decision, MockCrew)

        result = analyze_repository(temp_repo_with_script, "JOB-TEST")

        assert result.approved is False
        assert len(result.pii_details) > 0

    @patch("workers.ai_agent.analyzer.PolicyLoaderTool")
    def test_failure_returns_rejection(self, MockPolicyTool, temp_repo_with_script):
        """Returns conservative rejection when exception occurs."""
        MockPolicyTool.side_effect = Exception("LLM connection failed")

        result = analyze_repository(temp_repo_with_script, "JOB-FAIL")

        assert isinstance(result, AnalysisDecision)
        assert result.approved is False
        assert "analysis_error" in result.risks_identified
        assert "LLM connection failed" in result.reasoning

    @patch(_ANALYZER_PATCHES[0])
    @patch(_ANALYZER_PATCHES[1])
    @patch(_ANALYZER_PATCHES[2])
    @patch(_ANALYZER_PATCHES[3])
    @patch(_ANALYZER_PATCHES[4])
    @patch(_ANALYZER_PATCHES[5])
    @patch(_ANALYZER_PATCHES[6])
    @patch(_ANALYZER_PATCHES[7])
    @patch(_ANALYZER_PATCHES[8])
    @patch(_ANALYZER_PATCHES[9])
    @patch(_ANALYZER_PATCHES[10])
    def test_non_pydantic_output_returns_rejection(
        self, MockOutputChecker, MockASTScanner,
        MockPolicyTool, MockExecutorTool,
        mock_create_policy_agent, mock_create_analyzer_agent, mock_create_decision_agent,
        mock_create_policy_task, mock_create_analyzer_task, mock_create_decision_task,
        MockCrew,
        sample_execution_result, temp_repo_with_script,
    ):
        """Returns rejection when CrewAI output is not AnalysisDecision."""
        _setup_mocks(MockPolicyTool, MockExecutorTool, MockASTScanner, MockOutputChecker,
                     sample_execution_result, None, MockCrew)

        result = analyze_repository(temp_repo_with_script, "JOB-BAD")

        assert result.approved is False
        assert "structured AnalysisDecision" in result.reasoning
