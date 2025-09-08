FROM public.ecr.aws/amazonlinux/amazonlinux:2023

# Install Python and dependencies
RUN yum update -y && \
    yum install -y python3 python3-pip && \
    yum clean all

# Install Python packages required for bundle execution
RUN pip3 install cryptography

# Create app directory
WORKDIR /app

# Copy the enclave application modules
COPY *.py /app/

# Copy kmstool_enclave_cli and libnsm.so
COPY kmstool_enclave_cli /app/
COPY libnsm.so /app/
RUN chmod +x /app/kmstool_enclave_cli

# Set library path
ENV LD_LIBRARY_PATH=/app:$LD_LIBRARY_PATH

# Run the Python server
CMD ["python3", "/app/app.py"]