"""
Tests for workers/ai_agent/ai_agent_worker.py
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from workers.ai_agent.ai_agent_worker import AIAgentWorker, main
from workers.ai_agent.schemas import AnalysisDecision


class TestAIAgentWorker:
    """Test cases for AIAgentWorker class."""

    @pytest.fixture
    def mock_config(self):
        with patch("workers.ai_agent.ai_agent_worker.config") as mock_cfg:
            mock_cfg.shared_storage_path = "/tmp/test-storage"
            mock_cfg.database_url = "postgresql://test"
            yield mock_cfg

    @pytest.fixture
    def mock_job_repository(self):
        with patch("workers.ai_agent.ai_agent_worker.job_repository") as mock_repo, \
             patch("shared.base_worker.job_repository", mock_repo):
            mock_repo.update_job_status = MagicMock(return_value=True)
            mock_repo.add_job_log = MagicMock(return_value=True)
            yield mock_repo

    @pytest.fixture
    def mock_job_logger(self):
        with patch("workers.ai_agent.ai_agent_worker.JobLogger") as MockLogger:
            mock_logger = MagicMock()
            MockLogger.return_value = mock_logger
            yield mock_logger

    @pytest.fixture
    def worker(self, mock_config, mock_job_repository, mock_job_logger, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        w = AIAgentWorker()
        w.analysis_path = tmp_path / "ai_analysis"
        w.analysis_path.mkdir(parents=True, exist_ok=True)
        w.job_logger = mock_job_logger
        return w

    @pytest.fixture
    def sample_job(self):
        return {
            "job_id": "JOB-AI-001",
            "workspace_id": "ws-001",
            "github_repo": "user/repo",
            "github_branch": "main",
            "commit_sha": "abc123",
        }

    def test_initialization(self, worker):
        """Worker initializes correctly."""
        assert worker.worker_name == "AIAgentWorker"
        assert worker.analysis_path.exists()

    def test_initialization_requires_openai_key(self, mock_config, mock_job_repository, mock_job_logger, monkeypatch):
        """Raises when OPENAI_API_KEY is missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            AIAgentWorker()

    @patch("workers.ai_agent.ai_agent_worker.analyze_repository")
    def test_process_job_approved(self, mock_analyze, worker, sample_job, mock_job_repository, sample_analysis_decision):
        """Approved decision stores ai_approved via update_job_ai_results."""
        mock_analyze.return_value = sample_analysis_decision

        result = worker.process_job(sample_job)

        assert result is True
        mock_job_repository.update_job_ai_results.assert_called_once()
        call_kwargs = mock_job_repository.update_job_ai_results.call_args[1]
        assert call_kwargs["ai_decision"] == "ai_approved"

    @patch("workers.ai_agent.ai_agent_worker.analyze_repository")
    def test_process_job_rejected(self, mock_analyze, worker, sample_job, mock_job_repository, sample_rejected_decision):
        """Rejected decision stores ai_rejected via update_job_ai_results."""
        mock_analyze.return_value = sample_rejected_decision

        result = worker.process_job(sample_job)

        assert result is True
        mock_job_repository.update_job_ai_results.assert_called_once()
        call_kwargs = mock_job_repository.update_job_ai_results.call_args[1]
        assert call_kwargs["ai_decision"] == "ai_rejected"

    @patch("workers.ai_agent.ai_agent_worker.analyze_repository")
    def test_process_job_failure(self, mock_analyze, worker, sample_job, mock_job_repository):
        """Exception during analysis stores error as AI result."""
        mock_analyze.side_effect = Exception("Analysis crashed")

        result = worker.process_job(sample_job)

        # process_message catches the exception and stores error via update_job_ai_results
        assert result is True
        mock_job_repository.update_job_ai_results.assert_called()
        call_kwargs = mock_job_repository.update_job_ai_results.call_args[1]
        assert call_kwargs["ai_decision"] == "ai_error"

    def test_save_analysis_result(self, worker, sample_analysis_decision):
        """Analysis results are saved as JSON."""
        path = worker._save_analysis_result("JOB-SAVE", sample_analysis_decision)

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["approved"] is True
        assert data["confidence_score"] == 0.95
        assert "main.py" in data["analyzed_files"]

    def test_main_function_exists(self):
        """Main entry point is callable."""
        assert callable(main)
