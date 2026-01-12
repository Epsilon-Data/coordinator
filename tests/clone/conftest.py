"""
Clone worker pytest configuration.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    """Ensure clean test environment."""
    yield
