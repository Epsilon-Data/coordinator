import os
import subprocess
import tempfile
import shutil
import yaml
import time
from pathlib import Path
from typing import Dict, Any, List
from crewai.tools import BaseTool
from schemas import ExecutionResult


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
            
    def _prepare_dummy_data(self, execution_path: Path):
        """Copy dummy data to execution directory"""
        data_dir = execution_path / "data"
        data_dir.mkdir(exist_ok=True)
        
        # Copy dummy data files from archetypes
        archetypes_path = self._find_archetypes_path()
        if archetypes_path.exists():
            for archetype_dir in archetypes_path.iterdir():
                if archetype_dir.is_dir():
                    for file in archetype_dir.glob("*dummy*.csv"):
                        shutil.copy2(file, data_dir)
                        
    def _install_dependencies(self, execution_path: Path) -> bool:
        """Install Python requirements from yml config or requirements.txt"""
        # First try to get requirements from yml file
        requirements_file = self._find_requirements_from_yml(execution_path)
        
        # Fallback to standard requirements.txt
        if not requirements_file:
            requirements_file = execution_path / "requirements.txt"
            
        if requirements_file and requirements_file.exists():
            try:
                subprocess.run(
                    ["pip", "install", "-r", str(requirements_file)],
                    cwd=execution_path,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                return True
            except:
                pass
        return False
        
    def _find_requirements_from_yml(self, execution_path: Path) -> Path:
        """Find requirements file from yml configuration"""
        build_folder = execution_path / "build"
        
        if not build_folder.exists():
            return None
            
        # Look for build.yml file in build folder
        yml_files = []
        build_yml = build_folder / "build.yml"
        if build_yml.exists():
            yml_files = [build_yml]
        
        for yml_file in yml_files:
            try:
                with open(yml_file, 'r') as f:
                    config = yaml.safe_load(f)
                    
                # Get requirements from analysis section
                if config and 'analysis' in config and 'requirements' in config['analysis']:
                    requirements = config['analysis']['requirements']
                    
                    # Try build folder first
                    requirements_path = build_folder / requirements
                    if requirements_path.exists():
                        return requirements_path
                        
                    # Try repo root
                    requirements_path = execution_path / requirements
                    if requirements_path.exists():
                        return requirements_path
                        
            except Exception as e:
                print(f"Error reading yml file {yml_file}: {e}")
                continue
                
        return None
        
    def _execute_main_script(self, execution_path: Path, job_id: str) -> ExecutionResult:
        """Find and execute the main script from yml file"""
        # Look for yml file in build folder first
        script_file = self._find_script_from_yml(execution_path)
        
        if script_file:
            command = ["python", script_file]
        else:
            # Auto-detect main script as fallback
            main_files = ["main.py", "run.py", "app.py", "analysis.py", "example_analysis.py"]
            script = None
            
            for main_file in main_files:
                if (execution_path / main_file).exists():
                    script = main_file
                    break
                    
            if not script:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="No main script found in yml file or auto-detection",
                    return_code=-1,
                    execution_time=0,
                    output_files=[],
                    error_message="No main script found"
                )
                
            command = ["python", script]
            
        # Execute the command
        try:
            env = os.environ.copy()
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
            
    def _find_script_from_yml(self, execution_path: Path) -> str:
        """Find script file from yml configuration in build folder"""
        build_folder = execution_path / "build"
        
        if not build_folder.exists():
            return None
            
        # Look for build.yml file in build folder
        yml_files = []
        build_yml = build_folder / "build.yml"
        if build_yml.exists():
            yml_files = [build_yml]
        
        for yml_file in yml_files:
            try:
                with open(yml_file, 'r') as f:
                    config = yaml.safe_load(f)
                    
                # Get script_file from analysis section
                if config and 'analysis' in config and 'script_file' in config['analysis']:
                    script_file = config['analysis']['script_file']
                    
                    # Check if script exists in repo root
                    script_path = execution_path / script_file
                    if script_path.exists():
                        return script_file
                        
            except Exception as e:
                print(f"Error reading yml file {yml_file}: {e}")
                continue
                
        return None
        
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
                            except:
                                file_info["content"] = "[Binary or unreadable content]"
                        else:
                            file_info["content"] = "[File too large to read]"
                            
                        output_files.append(file_info)
                    except:
                        continue
                        
        return output_files
        
    def _save_ai_execution_result(self, job_id: str, execution_result: ExecutionResult):
        """Save AI execution results for debugging and analysis"""
        import json
        from datetime import datetime
        
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
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "purpose": "PII analysis execution - not production data"
        }
        
        result_file = ai_exec_path / "ai_execution_result.json"
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)
            
        print(f"Saved AI execution result to: {result_file}")