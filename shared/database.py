import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional, List
import json
import time

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from .config import config

logger = logging.getLogger(__name__)


class Database:
    """Database connection manager with connection pooling"""
    
    def __init__(self, database_url: str, min_conn: int = 1, max_conn: int = 10):
        self.database_url = database_url
        self.pool = SimpleConnectionPool(min_conn, max_conn, database_url)
        
    @contextmanager
    def get_connection(self):
        """Get a database connection from the pool"""
        conn = self.pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)
            
    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor):
        """Get a database cursor"""
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=cursor_factory)
            try:
                yield cursor
            finally:
                cursor.close()
                
    def close(self):
        """Close all connections in the pool"""
        self.pool.closeall()


class JobRepository:
    """Repository for job-related database operations using existing job_requests table"""
    
    def __init__(self, db: Database):
        self.db = db
        
    def update_job_status(
        self, 
        job_id: str, 
        status: str, 
        **kwargs
    ) -> Dict[str, Any]:
        """Update job status and additional fields in job_requests table"""
        with self.db.get_cursor() as cursor:
            # Build dynamic update query
            fields = ["status = %s", "updated_at = %s"]
            values = [status, datetime.utcnow()]
            
            # Map common field names to job_requests schema
            field_mapping = {
                'repo_path': 'result_metadata',  # Store repo path in result_metadata
                'repo_metadata': 'logs',  # Store repo metadata in logs field
                'ai_confidence_score': 'validation_status',  # Store AI confidence as validation status
                'ai_reasoning': 'validation_decision',  # Store AI reasoning in validation_decision
                'ai_risks': 'ai_logs',  # Store AI risks in ai_logs
                'ai_result_path': 'result_metadata',  # Store AI result path
                'execution_result': 'execution_output',  # Store execution result
                'execution_result_path': 'result_metadata',  # Store execution result path
                'error_message': 'error_message'  # Direct mapping
            }
            
            # Add additional fields with mapping
            for key, value in kwargs.items():
                if key in field_mapping:
                    db_field = field_mapping[key]
                    fields.append(f"{db_field} = %s")
                    # Convert complex objects to string
                    if isinstance(value, (dict, list)):
                        import json
                        values.append(json.dumps(value))
                    else:
                        values.append(str(value))
                        
            # Add job_id for WHERE clause
            values.append(job_id)
            
            query = f"""
                UPDATE job_requests 
                SET {', '.join(fields)}
                WHERE job_id = %s
                RETURNING *
            """
            
            cursor.execute(query, values)
            result = cursor.fetchone()
            
            if not result:
                raise ValueError(f"Job {job_id} not found")
                
            logger.info(f"Updated job {job_id} status to {status}")
            return dict(result)
            
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by job_id"""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM job_requests WHERE job_id = %s",
                (job_id,)
            )
            result = cursor.fetchone()
            return dict(result) if result else None
            
    def add_job_log(
        self, 
        job_id: str, 
        worker_name: str, 
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Add a log entry for a job by appending to ai_logs field"""
        with self.db.get_cursor() as cursor:
            # Create log entry
            import json
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "worker": worker_name,
                "level": level,
                "message": message,
                "metadata": metadata or {}
            }
            
            # Get current logs
            cursor.execute(
                "SELECT ai_logs FROM job_requests WHERE job_id = %s",
                (job_id,)
            )
            result = cursor.fetchone()
            
            if result:
                current_logs = result['ai_logs'] or ""
                # Append new log entry
                new_logs = current_logs + "\n" + json.dumps(log_entry) if current_logs else json.dumps(log_entry)
                
                # Update logs
                cursor.execute(
                    "UPDATE job_requests SET ai_logs = %s, updated_at = %s WHERE job_id = %s",
                    (new_logs, datetime.utcnow(), job_id)
                )
                
                logger.debug(f"Added log entry for job {job_id}: {message}")
            else:
                logger.warning(f"Job {job_id} not found for logging")

    def get_jobs_by_status(self, statuses: list[str]) -> list[Dict[str, Any]]:
        """Get jobs with specific statuses"""
        with self.db.get_cursor() as cursor:
            placeholders = ', '.join(['%s'] * len(statuses))
            cursor.execute(
                f"SELECT * FROM job_requests WHERE status IN ({placeholders}) ORDER BY created_at ASC",
                tuple(statuses)
            )
            results = cursor.fetchall()
            return [dict(result) for result in results]
            
    def add_detailed_log(
        self,
        job_id: str,
        worker_name: str,
        step_name: str,
        step_type: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
        progress: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error_details: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Add a detailed log entry to the job_logs table"""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO job_logs (
                    job_id, worker_name, step_name, step_type, 
                    level, message, metadata, progress, 
                    duration_ms, error_details, parent_log_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                job_id, worker_name, step_name, step_type,
                level, message, 
                json.dumps(metadata) if metadata else None,
                progress, duration_ms,
                json.dumps(error_details) if error_details else None,
                parent_log_id
            ))
            result = cursor.fetchone()
            log_id = str(result['id'])
            logger.debug(f"Added detailed log {log_id} for job {job_id}: {step_type} - {message}")
            return log_id

    def get_job_logs(
        self, 
        job_id: str, 
        step_type: Optional[str] = None,
        level: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get detailed logs for a job with optional filtering"""
        with self.db.get_cursor() as cursor:
            query = "SELECT * FROM job_logs WHERE job_id = %s"
            params = [job_id]
            
            if step_type:
                query += " AND step_type = %s"
                params.append(step_type)
                
            if level:
                query += " AND level = %s"
                params.append(level)
                
            query += " ORDER BY created_at ASC"
            
            if limit:
                query += " LIMIT %s"
                params.append(limit)
                
            if offset:
                query += " OFFSET %s"
                params.append(offset)
                
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            # Convert metadata and error_details from JSON strings if needed
            logs = []
            for row in results:
                log_entry = dict(row)
                if isinstance(log_entry.get('metadata'), str):
                    try:
                        log_entry['metadata'] = json.loads(log_entry['metadata'])
                    except:
                        pass
                if isinstance(log_entry.get('error_details'), str):
                    try:
                        log_entry['error_details'] = json.loads(log_entry['error_details'])
                    except:
                        pass
                logs.append(log_entry)
                
            return logs
            
    def get_job_logs_summary(self, job_id: str) -> List[Dict[str, Any]]:
        """Get summarized logs for a job grouped by step type"""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM job_logs_summary 
                WHERE job_id = %s 
                ORDER BY step_started_at ASC
            """, (job_id,))
            results = cursor.fetchall()
            return [dict(result) for result in results]
            
    def get_latest_job_logs(self, job_id: str) -> List[Dict[str, Any]]:
        """Get the latest log for each step type for a job"""
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM job_latest_logs 
                WHERE job_id = %s 
                ORDER BY created_at DESC
            """, (job_id,))
            results = cursor.fetchall()
            
            # Convert metadata from JSON strings if needed
            logs = []
            for row in results:
                log_entry = dict(row)
                if isinstance(log_entry.get('metadata'), str):
                    try:
                        log_entry['metadata'] = json.loads(log_entry['metadata'])
                    except:
                        pass
                logs.append(log_entry)
                
            return logs


# Global instances
db = Database(config.database_url)
job_repository = JobRepository(db)