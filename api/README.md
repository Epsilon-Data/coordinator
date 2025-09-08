# Epsilon Coordinator API Server

REST API server for accessing job logs and result files from the Epsilon Coordinator system.

## Features

- 🔍 **Job Logs**: Access detailed logs for any job execution
- 📊 **Log Summaries**: Get summarized views of job execution steps
- 📈 **Timeline Views**: Visual timeline of job execution phases
- 📄 **Execution Results**: Fetch execution result JSON files
- 🤖 **AI Analysis Results**: Access AI analysis decisions and reasoning
- 🚀 **Fast & Lightweight**: Built with Flask for optimal performance
- 🔒 **File System Access**: Direct access to shared storage files

## Quick Start

### Development Mode (Local)

```bash
# Make the start script executable
chmod +x start.sh

# Start the API server
./start.sh
```

The API will be available at `http://localhost:8001`

### Docker Mode

```bash
# From the epsilon-coordinator root directory
docker-compose up api-server

# Or build and run specifically
docker-compose build api-server
docker-compose run --rm -p 8001:8001 api-server
```

### Production Mode

```bash
# Set production environment variables
export DEBUG=false
export API_HOST=0.0.0.0
export API_PORT=8001

# Start with gunicorn (install: pip install gunicorn)
gunicorn -w 4 -b 0.0.0.0:8001 server:app
```

## API Endpoints

### Health & Info
- `GET /health` - Health check
- `GET /api` - API information and endpoint list

### Job Logs
- `GET /api/jobs/{job_id}/logs` - Get detailed logs
  - Query params: `step_type`, `level`, `limit`, `offset`
- `GET /api/jobs/{job_id}/logs/summary` - Get log summary by step
- `GET /api/jobs/{job_id}/logs/timeline` - Get execution timeline
- `GET /api/jobs/{job_id}/logs/errors` - Get error logs only
- `GET /api/jobs/{job_id}/status` - Get current job status

### Job Files
- `GET /api/jobs/{job_id}/execution-result` - Get execution result JSON
- `GET /api/jobs/{job_id}/ai-analysis-result` - Get AI analysis JSON
- `GET /api/jobs/{job_id}/files` - List all available files for job

## Example Usage

```bash
# Check API health
curl http://localhost:8001/health

# Get job logs
curl http://localhost:8001/api/jobs/JOB-ABC123/logs

# Get execution result
curl http://localhost:8001/api/jobs/JOB-ABC123/execution-result

# Get AI analysis result
curl http://localhost:8001/api/jobs/JOB-ABC123/ai-analysis-result
```

## Response Format

All endpoints return JSON with a consistent structure:

```json
{
  "success": true,
  "job_id": "JOB-ABC123",
  "data": { ... },
  "message": "Optional message"
}
```

Error responses:

```json
{
  "success": false,
  "error": "Error description",
  "job_id": "JOB-ABC123"
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_PORT` | `8001` | Port for the API server |
| `API_HOST` | `0.0.0.0` | Host interface to bind to |
| `DEBUG` | `false` | Enable debug mode |
| `DATABASE_URL` | - | PostgreSQL connection string |
| `SHARED_STORAGE_PATH` | `./shared_storage` | Path to shared storage directory |

## File Structure

```
api/
├── server.py           # Main Flask application
├── files_api.py        # File access API logic
├── Dockerfile          # Docker container definition
├── requirements.txt    # Python dependencies
├── start.sh           # Development startup script
└── README.md          # This file
```

## Integration

The API server is designed to work with:

- **ResearchWorkspace**: Frontend UI for job visualization
- **Epsilon Coordinator Workers**: Backend job processing
- **Shared Storage**: File system access to results

## Development

### Adding New Endpoints

1. Add the endpoint logic to `files_api.py` or create a new API module
2. Register the endpoint in `server.py`
3. Update the API info endpoint with the new route
4. Add tests and documentation

### Testing

```bash
# Install test dependencies
pip install pytest requests

# Run tests (when available)
pytest tests/

# Manual testing
curl -X GET http://localhost:8001/api/jobs/JOB-TEST/logs
```

## Troubleshooting

### Common Issues

1. **Port already in use**: Change `API_PORT` environment variable
2. **File not found errors**: Check `SHARED_STORAGE_PATH` is correct
3. **Database connection**: Verify `DATABASE_URL` is set correctly

### Logs

The API server logs all requests and errors to stdout. In Docker mode:

```bash
# View API server logs
docker-compose logs api-server

# Follow logs in real-time
docker-compose logs -f api-server
```

## Security Notes

- The API currently has no authentication (suitable for internal networks)
- Files are served read-only from the shared storage
- Consider adding authentication for production deployments
- CORS is enabled for development (configure for production)