FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy shared modules first
COPY shared/ /app/shared/

# Copy worker-specific files
COPY workers/job-fetcher/ /app/workers/job-fetcher/

# Install Python dependencies
RUN pip install --no-cache-dir \
    pika==1.3.2 \
    psycopg2-binary==2.9.9

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV POLLING_INTERVAL=5

# Run the worker
CMD ["python", "workers/job-fetcher/job_fetcher.py"]