#!/usr/bin/env python3
"""
Main entry point for the Nitro Enclave server application
"""
import sys
import os
import logging

from config import KMSTOOL_PATH, KMS_REGION, KMS_PROXY_PORT
from server import EnclaveServer

logger = logging.getLogger(__name__)


def check_environment():
    """Verify the enclave environment is properly configured"""
    # Check if kmstool_enclave_cli exists
    if not os.path.exists(KMSTOOL_PATH):
        logger.error(f"kmstool_enclave_cli not found at {KMSTOOL_PATH}")
        logger.error("Make sure to include it in your Docker image")
        return False
        
    # Log configuration
    logger.info("🔧 Enclave Configuration:")
    logger.info(f"  KMS Region: {KMS_REGION}")
    logger.info(f"  KMS Proxy Port: {KMS_PROXY_PORT}")
    logger.info(f"  KMS Tool Path: {KMSTOOL_PATH}")
    
    return True


def main():
    """Main entry point"""
    logger.info("🚀 Starting Epsilon Executor Nitro Enclave")
    logger.info("=" * 50)
    
    # Check environment
    if not check_environment():
        sys.exit(1)
    
    # Start server
    server = EnclaveServer()
    server.start()


if __name__ == "__main__":
    main()