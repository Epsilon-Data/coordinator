"""
Configuration settings for the enclave server
"""
import os

# Network settings
VSOCK_PORT = 5005
KMS_PROXY_PORT = 8000

# AWS settings
KMS_REGION = os.environ.get('KMS_REGION', 'ap-southeast-2')

# Paths
KMSTOOL_PATH = "/app/kmstool_enclave_cli"
TEMP_DIR_PREFIX = "enclave_bundle_"

# Timeouts
SCRIPT_EXECUTION_TIMEOUT = 60  # seconds
CONSOLE_READ_TIMEOUT = 5  # seconds

# Buffer sizes
MAX_REQUEST_SIZE = 65536  # 64KB
MAX_RESPONSE_SIZE = 4194304  # 4MB

# Logging
LOG_FORMAT = '[%(asctime)s] %(levelname)s: %(message)s'
LOG_LEVEL = 'INFO'