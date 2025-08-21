import logging
import os
import sys
from pathlib import Path
from typing import Dict, Any

sys.path.append('/app')

from shared import BaseWorker, config, job_repository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ExecuteWorker(BaseWorker):
    """Worker responsible for executing approved jobs in secure environment"""
    
    def __init__(self):
        super().__init__(
            worker_name="ExecuteWorker",
            queue_name=config.execute_queue,
            routing_keys=[config.routing_key_approved]
        )
        
        # Ensure enclave execution output directory exists
        self.output_path = Path(config.shared_storage_path) / "enclave_execution_results"
        self.output_path.mkdir(parents=True, exist_ok=True)
        
    def _execute_job(self, job_id: str, repo_path: Path) -> Dict[str, Any]:
        """
        Execute the job in a secure environment
        
        TODO: Implement actual execution logic
        - Set up secure execution environment (container/VM)
        - Copy repository to execution environment
        - Install dependencies
        - Execute the code
        - Collect results
        - Clean up environment
        """
        logger.info(f"Executing job {job_id} from {repo_path}")
        
        # Placeholder implementation for secure enclave execution
        result = {
            "execution_type": "secure_enclave",
            "status": "completed",
            "execution_time": "0.0s",
            "output": "Placeholder enclave execution result",
            "artifacts": [],
            "logs": "Execution logs would go here"
        }
        
        # Save result to file
        result_path = self.output_path / job_id / "execution_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        
        import json
        with open(result_path, 'w') as f:
            json.dump(result, f, indent=2)
            
        result["result_path"] = str(result_path)
        
        return result
        
    def process_message(self, message: Dict[str, Any]):
        """Process an execution job message"""
        job_id = message['job_id']
        repo_path = Path(message['repo_path'])
        workspace_id = message.get('workspace_id')
        ai_decision = message.get('ai_decision', {})
        
        logger.info(f"Processing execution for job {job_id}")
        
        try:
            # Update job status to executing
            job_repository.update_job_status(
                job_id=job_id,
                status='executing'
            )
            
            # Log execution start
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message="Starting job execution",
                metadata={
                    "repo_path": str(repo_path),
                    "ai_confidence": ai_decision.get('confidence_score')
                }
            )
            
            # Execute the job
            result = self._execute_job(job_id, repo_path)
            
            # Update job status to completed
            job_repository.update_job_status(
                job_id=job_id,
                status='completed',
                execution_result=result,
                execution_result_path=result.get('result_path')
            )
            
            # Log execution completion
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message="Job execution completed successfully",
                metadata=result
            )
            
            # Publish completion message
            self.publish_message(
                routing_key=config.routing_key_completed,
                message={
                    'job_id': job_id,
                    'workspace_id': workspace_id,
                    'result': result,
                    'status': 'completed'
                }
            )
            
            logger.info(f"Job {job_id} execution completed")
            
        except Exception as e:
            logger.error(f"Execution for job {job_id} failed: {e}")
            
            # Log the error
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"Execution failed: {str(e)}",
                level="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_details": str(e)
                }
            )
            
            raise  # Re-raise to let base worker handle the error


def main():
    """Main entry point"""
    worker = ExecuteWorker()
    worker.start()


if __name__ == "__main__":
    main()