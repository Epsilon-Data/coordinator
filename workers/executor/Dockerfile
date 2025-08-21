FROM python:3.11-slim

# Install system dependencies for execution
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy shared modules
COPY shared /app/shared

# Copy worker-specific files
COPY workers/executor /app/workers/executor

# Install Python dependencies
COPY workers/executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set Python path
ENV PYTHONPATH=/app

# Run the worker
CMD ["python", "-u", "workers/executor/execute_worker.py"]