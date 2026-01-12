FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package definition and install as editable
COPY pyproject.toml .
COPY shared ./shared/
COPY workers ./workers/

# Install package with ai-agent worker dependencies
RUN pip install --no-cache-dir -e .[ai-agent]

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the worker
CMD ["python", "-m", "workers.ai_agent.ai_agent_worker"]
