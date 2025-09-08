FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy main requirements first
COPY requirements.txt .
# Copy API requirements
COPY api/requirements.txt api/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r api/requirements.txt

# Copy the shared modules first
COPY shared/ ./shared/

# Copy the API application code
COPY api/ ./api/

# Create a non-root user
RUN useradd --create-home --shell /bin/bash apiuser && \
    chown -R apiuser:apiuser /app

USER apiuser

# Expose the API port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Set environment variables
ENV PYTHONPATH=/app:/app/api
ENV API_PORT=8001
ENV DEBUG=false

# Change to API directory and start the server
WORKDIR /app/api
CMD ["python", "server.py"]