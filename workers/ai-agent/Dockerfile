FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy shared modules
COPY shared /app/shared

# Copy worker-specific files with proper structure
COPY workers/ai-agent /app/workers/ai-agent

# Install Python dependencies
COPY workers/ai-agent/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# Set Python path to include worker directory
ENV PYTHONPATH=/app:/app/workers/ai-agent

# Run the worker
CMD ["python", "-u", "workers/ai-agent/ai_agent_worker.py"]