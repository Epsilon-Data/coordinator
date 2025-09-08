"""
Epsilon Coordinator API Server
Provides REST API endpoints for accessing job logs and result files
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import sys

# Add the parent directory to the Python path so we can import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.logs_api import JobLogsAPI
from api.files_api import JobFilesAPI

app = Flask(__name__)
CORS(app)  # Enable CORS for cross-origin requests from ResearchWorkspace

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy", 
        "service": "epsilon-coordinator-api",
        "version": "1.0.0"
    })

# API Info endpoint
@app.route('/api', methods=['GET'])
def api_info():
    return jsonify({
        "name": "Epsilon Coordinator API",
        "version": "1.0.0",
        "description": "REST API for accessing job logs and result files",
        "endpoints": {
            "health": "GET /health",
            "job_logs": "GET /api/jobs/<job_id>/logs",
            "job_logs_summary": "GET /api/jobs/<job_id>/logs/summary", 
            "job_timeline": "GET /api/jobs/<job_id>/logs/timeline",
            "job_errors": "GET /api/jobs/<job_id>/logs/errors",
            "job_status": "GET /api/jobs/<job_id>/status",
            "execution_result": "GET /api/jobs/<job_id>/execution-result",
            "ai_analysis_result": "GET /api/jobs/<job_id>/ai-analysis-result",
            "job_files": "GET /api/jobs/<job_id>/files"
        }
    })

# Job Logs endpoints
@app.route('/api/jobs/<job_id>/logs', methods=['GET'])
def get_job_logs(job_id):
    """Get detailed logs for a job"""
    step_type = request.args.get('step_type')
    level = request.args.get('level')
    limit = int(request.args.get('limit', 100))
    offset = int(request.args.get('offset', 0))
    
    result = JobLogsAPI.get_job_logs(job_id, step_type, level, limit, offset)
    return jsonify(result)

@app.route('/api/jobs/<job_id>/logs/summary', methods=['GET'])
def get_job_logs_summary(job_id):
    """Get summarized logs for a job"""
    result = JobLogsAPI.get_job_logs_summary(job_id)
    return jsonify(result)

@app.route('/api/jobs/<job_id>/logs/timeline', methods=['GET'])
def get_job_timeline(job_id):
    """Get timeline view of job execution"""
    result = JobLogsAPI.get_job_timeline(job_id)
    return jsonify(result)

@app.route('/api/jobs/<job_id>/logs/errors', methods=['GET'])
def get_job_errors(job_id):
    """Get all errors for a job"""
    result = JobLogsAPI.get_job_errors(job_id)
    return jsonify(result)

@app.route('/api/jobs/<job_id>/status', methods=['GET'])
def get_job_status(job_id):
    """Get latest status for each step of a job"""
    result = JobLogsAPI.get_latest_job_status(job_id)
    return jsonify(result)

# Job Files endpoints
@app.route('/api/jobs/<job_id>/execution-result', methods=['GET'])
def get_execution_result(job_id):
    """Get execution result file for a job"""
    result = JobFilesAPI.get_execution_result(job_id)
    return jsonify(result)

@app.route('/api/jobs/<job_id>/ai-analysis-result', methods=['GET'])
def get_ai_analysis_result(job_id):
    """Get AI analysis result file for a job"""
    result = JobFilesAPI.get_ai_analysis_result(job_id)
    return jsonify(result)

@app.route('/api/jobs/<job_id>/files', methods=['GET'])
def get_job_files_summary(job_id):
    """Get summary of all available files for a job"""
    result = JobFilesAPI.get_job_files_summary(job_id)
    return jsonify(result)

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "message": "The requested endpoint does not exist",
        "available_endpoints": "/api for endpoint list"
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Internal server error",
        "message": "An internal error occurred while processing the request"
    }), 500

if __name__ == '__main__':
    port = int(os.environ.get('API_PORT', 8001))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    host = os.environ.get('API_HOST', '0.0.0.0')
    print("Epsilon Coordinator API Server")
    print()
    
    app.run(host=host, port=port, debug=debug)