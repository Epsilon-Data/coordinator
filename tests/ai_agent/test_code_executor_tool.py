"""
Tests for workers/ai_agent/tools/code_executor_tool.py
"""
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from workers.ai_agent.tools.code_executor_tool import CodeExecutorTool, _ENV_WHITELIST


class TestCodeExecutorTool:
    """Tests for CodeExecutorTool."""

    @pytest.fixture
    def tool(self):
        return CodeExecutorTool()

    @pytest.fixture
    def exec_repo(self, temp_repo_with_script):
        """Repo with a script that produces output."""
        repo = Path(temp_repo_with_script)
        # Overwrite main.py to produce an output file
        (repo / "main.py").write_text(
            "import json, pathlib\n"
            "pathlib.Path('output.json').write_text(json.dumps({'result': 'ok'}))\n"
            "print('done')\n"
        )
        return str(repo)

    def test_execute_success(self, tool, exec_repo, tmp_path, monkeypatch):
        """Successful execution returns success=True and captures stdout."""
        monkeypatch.setenv("SHARED_STORAGE_PATH", str(tmp_path))
        result = tool._run(exec_repo, "JOB-EXEC-001")

        assert result.success is True
        assert result.return_code == 0
        assert "done" in result.stdout
        assert result.execution_time > 0

    def test_execute_timeout(self, tool, temp_repo_with_script, tmp_path, monkeypatch):
        """Timeout returns appropriate error result."""
        monkeypatch.setenv("SHARED_STORAGE_PATH", str(tmp_path))
        repo = Path(temp_repo_with_script)
        (repo / "main.py").write_text("import time; time.sleep(999)")

        with patch("workers.ai_agent.tools.code_executor_tool.subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=60)
            result = tool._run(str(repo), "JOB-TIMEOUT")

        assert result.success is False
        assert "timed out" in result.error_message.lower()

    def test_collect_output_files(self, tool):
        """Output files are collected correctly."""
        tmpdir = tempfile.mkdtemp()
        try:
            exec_path = Path(tmpdir)
            (exec_path / "output.csv").write_text("a,b\n1,2\n")
            (exec_path / "result.json").write_text('{"x": 1}')
            # Should be excluded
            (exec_path / "requirements.txt").write_text("pandas")

            files = tool._collect_output_files(exec_path)
            names = [f["name"] for f in files]

            assert "output.csv" in names
            assert "result.json" in names
            assert "requirements.txt" not in names
        finally:
            shutil.rmtree(tmpdir)

    def test_env_whitelist(self, tool, exec_repo, tmp_path, monkeypatch):
        """Only whitelisted env vars are passed to subprocess."""
        monkeypatch.setenv("SHARED_STORAGE_PATH", str(tmp_path))
        monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql://secret")

        with patch("workers.ai_agent.tools.code_executor_tool.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            tool._run(exec_repo, "JOB-ENV")

            # Find the call to subprocess.run that executes the script (not pip install)
            for call in mock_run.call_args_list:
                args, kwargs = call
                if "python" in args[0][0]:
                    env = kwargs.get("env", {})
                    assert "OPENAI_API_KEY" not in env
                    assert "DATABASE_URL" not in env
                    assert "EPSILON_JOB_ID" in env
                    # All keys should be whitelisted or EPSILON_JOB_ID
                    for key in env:
                        assert key in _ENV_WHITELIST or key == "EPSILON_JOB_ID"

    def test_path_traversal_blocked(self, tool, tmp_path, monkeypatch):
        """Path traversal in script_file is blocked."""
        monkeypatch.setenv("SHARED_STORAGE_PATH", str(tmp_path))
        repo = tmp_path / "repo"
        repo.mkdir()
        build_dir = repo / "build"
        build_dir.mkdir()
        (build_dir / "build.yml").write_text(
            "analysis:\n  script_file: ../../../etc/passwd\n"
        )

        result = tool._run(str(repo), "JOB-TRAVERSAL")

        assert result.success is False
        assert "traversal" in result.error_message.lower() or "No main script" in result.error_message

    def test_symlink_skipped_in_output(self, tool):
        """Symlinks are skipped when collecting output files."""
        tmpdir = tempfile.mkdtemp()
        try:
            exec_path = Path(tmpdir)
            real_file = exec_path / "real.csv"
            real_file.write_text("a,b\n1,2\n")
            symlink = exec_path / "link.csv"
            symlink.symlink_to(real_file)

            files = tool._collect_output_files(exec_path)
            names = [f["name"] for f in files]

            assert "real.csv" in names
            assert "link.csv" not in names
        finally:
            shutil.rmtree(tmpdir)
