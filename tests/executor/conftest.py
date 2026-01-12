"""
Executor-specific pytest configuration.
Provides fixtures for executor tests using the flat module structure.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    """Ensure clean test environment."""
    yield
