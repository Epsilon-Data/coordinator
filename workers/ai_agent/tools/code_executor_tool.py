import os
import subprocess
import tempfile
import shutil
import time
import logging
from pathlib import Path
from typing import Dict, Any, List

from crewai.tools import BaseTool

from workers.ai_agent.schemas import ExecutionResult
from workers.ai_agent.utils import find_main_script, find_requirements_path, MAIN_SCRIPT_FALLBACKS

logger = logging.getLogger(__name__)

# Only these env vars are passed to the subprocess
_ENV_WHITELIST = {
    "PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "TMPDIR",
    "VIRTUAL_ENV", "PYTHONUNBUFFERED", "SHARED_STORAGE_PATH", "EPSILON_JOB_ID",
}


class CodeExecutorTool(BaseTool):
    name: str = "Code Executor"
    description: str = "Execute repository code with dummy data and capture results"

    def _find_archetypes_path(self) -> Path:
        """Find archetypes folder for dummy data"""
        shared_storage_path = Path(os.environ.get('SHARED_STORAGE_PATH', '/shared/epsilon'))

        possible_paths = [
            shared_storage_path / "archetypes",
            Path("/app/sdk-epsilon/archetypes"),
            Path("/app/archetypes")
        ]

        for path in possible_paths:
            if path.exists():
                return path

        # Create empty dummy data directory as fallback
        dummy_path = shared_storage_path / "dummy_data"
        dummy_path.mkdir(exist_ok=True)
        return dummy_path

    def _run(self, repo_path: str, job_id: str) -> ExecutionResult:
        """Execute repository code and return results"""
        repo_path = Path(repo_path)

        # Create temporary execution directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            execution_path = temp_path / "execution"

            # Copy repository to temporary location
            shutil.copytree(repo_path, execution_path)

            # Install dependencies
            self._install_dependencies(execution_path)

            # Find and execute main script
            execution_result = self._execute_main_script(execution_path, job_id)

            # Save AI execution results for debugging/analysis
            self._save_ai_execution_result(job_id, execution_result)

            return execution_result

    def _install_dependencies(self, execution_path: Path) -> bool:
        """Install Python requirements from build.yml config or requirements.txt"""
        requirements_file = find_requirements_path(str(execution_path))

        if requirements_file and requirements_file.exists():
            try:
                # WARNING: installs packages from untrusted user repos.
                # Runs inside an isolated enclave container — no host impact.
                subprocess.run(
                    ["pip", "install", "-r", str(requirements_file)],
                    cwd=execution_path,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                return True
            except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
                logger.warning(f"Failed to install dependencies: {e}")
        return False

    def _execute_main_script(self, execution_path: Path, job_id: str) -> ExecutionResult:
        """Find and execute the main script"""
        # Use shared utility to find script from build.yml
        content, script_file = find_main_script(str(execution_path))

        if script_file == "No main script found":
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="No main script found in build.yml or auto-detection",
                return_code=-1,
                execution_time=0,
                output_files=[],
                error_message="No main script found"
            )

        # Path traversal check: ensure script resolves within execution_path
        resolved_script = (execution_path / script_file).resolve()
        if not str(resolved_script).startswith(str(execution_path.resolve())):
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Path traversal detected: {script_file}",
                return_code=-1,
                execution_time=0,
                output_files=[],
                error_message=f"Path traversal detected: {script_file}"
            )

        command = ["python", script_file]

        # Execute the command
        try:
            # Only pass whitelisted env vars to subprocess
            env = {k: v for k, v in os.environ.items() if k in _ENV_WHITELIST}
            env["EPSILON_JOB_ID"] = job_id

            start_time = time.time()
            result = subprocess.run(
                command,
                cwd=execution_path,
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )
            end_time = time.time()

            # Collect output files
            output_files = self._collect_output_files(execution_path)

            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
                execution_time=round(end_time - start_time, 2),
                output_files=output_files
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Execution timed out",
                return_code=-1,
                execution_time=60,
                output_files=[],
                error_message="Execution timed out"
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                return_code=-1,
                execution_time=0,
                output_files=[],
                error_message=str(e)
            )

    def _collect_output_files(self, execution_path: Path) -> List[Dict[str, Any]]:
        """Collect information about generated output files (excluding technical files)"""
        output_files = []
        output_extensions = [".csv", ".json", ".txt", ".xlsx"]

        # Technical files to exclude from PII analysis
        excluded_files = [
            "requirements.txt", "requirements-dev.txt", "setup.py", "setup.cfg",
            "pyproject.toml", "Pipfile", "Pipfile.lock", "poetry.lock",
            ".gitignore", "README.md", "LICENSE", "MANIFEST.in"
        ]

        for ext in output_extensions:
            for file_path in execution_path.glob(f"**/*{ext}"):
                # Skip symlinks
                if file_path.is_symlink():
                    logger.debug(f"Skipping symlink: {file_path}")
                    continue

                if file_path.is_file():
                    # Skip technical/config files that aren't script output
                    if file_path.name.lower() in [f.lower() for f in excluded_files]:
                        continue

                    try:
                        file_info = {
                            "name": file_path.name,
                            "path": str(file_path.relative_to(execution_path)),
                            "size": file_path.stat().st_size,
                            "extension": ext
                        }

                        # Read small files for content analysis
                        if file_path.stat().st_size < 10000:  # Less than 10KB
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    file_info["content"] = f.read()
                            except (UnicodeDecodeError, IOError):
                                file_info["content"] = "[Binary or unreadable content]"
                        else:
                            file_info["content"] = "[File too large to read]"

                        output_files.append(file_info)
                    except (OSError, IOError) as e:
                        logger.debug(f"Could not process file {file_path}: {e}")
                        continue

        return output_files

    def _save_ai_execution_result(self, job_id: str, execution_result: ExecutionResult):
        """Save AI execution results for debugging and analysis"""
        import json
        from datetime import datetime, timezone

        # Create AI execution results directory
        shared_storage_path = Path(os.environ.get('SHARED_STORAGE_PATH', '/shared/epsilon'))
        ai_exec_path = shared_storage_path / "ai_execution_results" / job_id
        ai_exec_path.mkdir(parents=True, exist_ok=True)

        # Save execution result
        result_data = {
            "execution_type": "ai_analysis",
            "success": execution_result.success,
            "stdout": execution_result.stdout,
            "stderr": execution_result.stderr,
            "return_code": execution_result.return_code,
            "execution_time": execution_result.execution_time,
            "output_files": execution_result.output_files,
            "error_message": execution_result.error_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "purpose": "PII analysis execution - not production data"
        }

        result_file = ai_exec_path / "ai_execution_result.json"
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)

        logger.info(f"Saved AI execution result to: {result_file}")
