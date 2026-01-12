FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy package definition and install as editable
COPY pyproject.toml .
COPY shared ./shared/
COPY workers ./workers/

# Install package with job-fetcher worker dependencies
RUN pip install --no-cache-dir -e .[job-fetcher]

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV POLLING_INTERVAL=5
ENV BATCH_SIZE=10

# Run the worker
CMD ["python", "-m", "workers.job_fetcher.job_fetcher"]
