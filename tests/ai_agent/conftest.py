"""
AI Agent test fixtures.
"""
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from workers.ai_agent.schemas import AnalysisDecision, ExecutionResult, CodeViolation


@pytest.fixture
def sample_execution_result():
    """Sample successful execution result."""
    return ExecutionResult(
        success=True,
        stdout="Processing complete. Average heart rate: 72.3\n",
        stderr="",
        return_code=0,
        execution_time=1.5,
        output_files=[
            {
                "name": "output.csv",
                "path": "output.csv",
                "size": 256,
                "extension": ".csv",
                "content": "metric,value\nheart_rate_avg,72.3\n"
            }
        ],
    )


@pytest.fixture
def sample_analysis_decision():
    """Sample approved analysis decision."""
    return AnalysisDecision(
        approved=True,
        confidence_score=0.95,
        reasoning="Code only accesses non-PII fields (heart_rate, age). No PII exposure detected.",
        risks_identified=[],
        recommendations=["Continue monitoring"],
        pii_details=[],
        analyzed_files=["main.py"],
    )


@pytest.fixture
def sample_rejected_decision():
    """Sample rejected analysis decision."""
    return AnalysisDecision(
        approved=False,
        confidence_score=0.9,
        reasoning="Script directly accesses patient_id and email fields.",
        risks_identified=["pii_access", "data_leakage"],
        recommendations=["Remove PII field access", "Use anonymized identifiers"],
        pii_details=[
            CodeViolation(
                file="main.py",
                line=15,
                field="patient_id",
                code="df['patient_id']",
                type="dictionary_access",
            )
        ],
        analyzed_files=["main.py"],
    )


@pytest.fixture
def mock_crew_output(sample_analysis_decision):
    """Mock CrewAI CrewOutput with pydantic field."""
    mock = MagicMock()
    mock.pydantic = sample_analysis_decision
    return mock


@pytest.fixture
def temp_repo_with_script():
    """Create a temporary repo directory with a main script and build.yml."""
    tmpdir = tempfile.mkdtemp()
    repo_path = Path(tmpdir)
    build_dir = repo_path / "build"
    build_dir.mkdir()

    # Create build.yml
    (build_dir / "build.yml").write_text(
        "analysis:\n"
        "  script_file: main.py\n"
        "  requirements: requirements.txt\n"
        "  epsilon: 1.0\n"
    )

    # Create main.py
    (repo_path / "main.py").write_text(
        "import sys\n"
        "print('Hello from test')\n"
        "sys.exit(0)\n"
    )

    # Create requirements.txt
    (repo_path / "requirements.txt").write_text("")

    yield str(repo_path)
    shutil.rmtree(tmpdir, ignore_errors=True)
