"""
Abstract interfaces for the Executor
"""
from interfaces.enclave_client import IEnclaveClient
from interfaces.storage_manager import IStorageManager
from interfaces.executor import IExecutor

__all__ = ['IEnclaveClient', 'IStorageManager', 'IExecutor']