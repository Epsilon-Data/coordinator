FROM python:3.11-slim

# Install git and other dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy shared modules first
COPY shared /app/shared

# Copy worker-specific files with proper structure
COPY workers/clone /app/workers/clone

# Install Python dependencies
COPY workers/clone/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Set Python path to include worker directory
ENV PYTHONPATH=/app:/app/workers/clone

# Run the worker
CMD ["python", "-u", "workers/clone/clone_worker.py"]