import logging
import os
import sys
from pathlib import Path
from typing import Dict, Any

# Add paths for imports
sys.path.append('/app')
sys.path.append('/app/workers/ai-agent')

from shared import BaseWorker, config, job_repository
from analyzer import analyze_repository
from schemas import AnalysisDecision

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AIAgentWorker(BaseWorker):
    """Worker responsible for AI analysis of repositories using CrewAI"""
    
    def __init__(self):
        super().__init__(
            worker_name="AIAgentWorker",
            queue_name=config.ai_queue,
            routing_keys=[config.routing_key_cloned]
        )
        
        # Ensure AI analysis output directory exists
        self.analysis_path = Path(config.shared_storage_path) / "ai_analysis"
        self.analysis_path.mkdir(parents=True, exist_ok=True)
        
        # Set up environment for AI agent
        self._setup_environment()
        
    def _setup_environment(self):
        """Set up environment variables for CrewAI AI agent"""
        # OpenAI API key is required for CrewAI
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("OPENAI_API_KEY not set - CrewAI AI agent will not work")
            raise ValueError("OPENAI_API_KEY environment variable is required")
            
    def _get_dummy_data_path(self, repo_path: Path) -> Path:
        """Get path to dummy data for the repository"""
        # Check if repository has its own dummy data
        repo_dummy_data = repo_path / "dummy_data"
        if repo_dummy_data.exists():
            return repo_dummy_data
            
        # Use archetypes from sdk-epsilon if available
        possible_archetype_paths = [
            Path("/app/sdk-epsilon/archetypes"),
            Path("/app/archetypes"),
            Path(config.shared_storage_path) / "archetypes"
        ]
        
        for archetype_path in possible_archetype_paths:
            if archetype_path.exists():
                logger.info(f"Using archetypes from: {archetype_path}")
                return archetype_path
                
        # Create empty dummy data directory as fallback
        dummy_path = self.analysis_path / "dummy_data"
        dummy_path.mkdir(exist_ok=True)
        logger.warning(f"No archetypes found, using empty dummy data: {dummy_path}")
        return dummy_path
        
    def _save_analysis_result(self, job_id: str, decision: AnalysisDecision) -> Path:
        """Save analysis results to file"""
        import json
        
        result_path = self.analysis_path / job_id / "analysis_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        
        result_data = {
            "approved": decision.approved,
            "confidence_score": decision.confidence_score,
            "reasoning": decision.reasoning,
            "risks_identified": decision.risks_identified,
            "recommendations": decision.recommendations,
            "timestamp": os.popen('date -u +"%Y-%m-%dT%H:%M:%SZ"').read().strip(),
            "analysis_version": "crewai-epsilon-coordinator-1.0"
        }
        
        with open(result_path, 'w') as f:
            json.dump(result_data, f, indent=2)
            
        return result_path
        
    def process_message(self, message: Dict[str, Any]):
        """Process an AI analysis job message using CrewAI"""
        job_id = message['job_id']
        repo_path = Path(message['repo_path'])
        workspace_id = message.get('workspace_id')
        metadata = message.get('metadata', {})
        
        logger.info(f"Processing CrewAI analysis for job {job_id}")
        logger.info(f"Repository path: {repo_path}")
        
        try:
            # Update job status to analyzing
            job_repository.update_job_status(
                job_id=job_id,
                status='analyzing'
            )
            
            # Log analysis start
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message="Starting CrewAI analysis",
                metadata={
                    "repo_path": str(repo_path),
                    # "dummy_data_path": str(dummy_data_path),
                    "main_language": metadata.get("main_language"),
                    "crewai_version": "epsilon-coordinator-1.0"
                }
            )
            
            # Run CrewAI analysis using our implementation
            logger.info(f"Running CrewAI analyze_repository for {repo_path}")
            decision = analyze_repository(
                repo_path=str(repo_path),
                # dummy_data_path=str(dummy_data_path),
                job_id=job_id
            )
            
            logger.info(f"CrewAI analysis completed: {'APPROVED' if decision.approved else 'REJECTED'}")
            logger.info(f"Confidence: {decision.confidence_score}, Reasoning: {decision.reasoning}")
            
            # Save analysis results
            result_path = self._save_analysis_result(job_id, decision)
            
            # Log analysis results
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"CrewAI analysis completed: {'Approved' if decision.approved else 'Rejected'}",
                metadata={
                    "approved": decision.approved,
                    "confidence_score": decision.confidence_score,
                    "reasoning": decision.reasoning,
                    "risks_identified": decision.risks_identified,
                    "recommendations": decision.recommendations,
                    "result_path": str(result_path)
                }
            )
            
            if decision.approved:
                # Update job status to AI approved
                job_repository.update_job_status(
                    job_id=job_id,
                    status='ai_approved',
                    ai_confidence_score=decision.confidence_score,
                    ai_reasoning=decision.reasoning,
                    ai_result_path=str(result_path)
                )
                
                # Publish approval message
                self.publish_message(
                    routing_key=config.routing_key_approved,
                    message={
                        'job_id': job_id,
                        'repo_path': str(repo_path),
                        'workspace_id': workspace_id,
                        'ai_decision': {
                            'approved': True,
                            'confidence_score': decision.confidence_score,
                            'reasoning': decision.reasoning,
                            'analysis_method': 'crewai-epsilon-coordinator'
                        }
                    }
                )
                
                logger.info(f"Job {job_id} approved by CrewAI analysis")
                
            else:
                # Update job status to AI rejected
                job_repository.update_job_status(
                    job_id=job_id,
                    status='ai_rejected',
                    ai_confidence_score=decision.confidence_score,
                    ai_reasoning=decision.reasoning,
                    ai_risks=decision.risks_identified,
                    ai_result_path=str(result_path)
                )
                
                # Publish rejection message
                self.publish_message(
                    routing_key=config.routing_key_rejected,
                    message={
                        'job_id': job_id,
                        'reason': decision.reasoning,
                        'risks': decision.risks_identified,
                        'recommendations': decision.recommendations,
                        'confidence_score': decision.confidence_score,
                        'analysis_method': 'crewai-epsilon-coordinator'
                    }
                )
                
                logger.info(f"Job {job_id} rejected by CrewAI analysis: {decision.reasoning}")
                
        except Exception as e:
            logger.error(f"CrewAI analysis for job {job_id} failed: {e}")
            
            # Log the error with more details
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"CrewAI analysis failed: {str(e)}",
                level="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_details": str(e),
                    "repo_path": str(repo_path)
                }
            )
            
            raise  # Re-raise to let base worker handle the error


def main():
    """Main entry point"""
    worker = AIAgentWorker()
    worker.start()


if __name__ == "__main__":
    main()