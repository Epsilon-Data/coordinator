"""
Tests for shared/config.py
"""
import os
import sys
import importlib
import pytest
from unittest.mock import patch


class TestConfig:
    """Test cases for Config class."""

    def _reload_config(self):
        """Reload the config module to pick up new environment variables."""
        # Remove cached module
        if 'shared.config' in sys.modules:
            del sys.modules['shared.config']
        # Import fresh
        import shared.config
        return shared.config.Config

    def test_config_loads_database_url(self, clean_env):
        """Test that Config loads DATABASE_URL from environment."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'

        Config = self._reload_config()
        config = Config()

        assert config.database_url == 'postgresql://test:test@localhost:5432/testdb'

    def test_config_custom_shared_storage_path(self, clean_env):
        """Test custom shared_storage_path from environment."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ['SHARED_STORAGE_PATH'] = '/custom/path'

        Config = self._reload_config()
        config = Config()

        assert config.shared_storage_path == '/custom/path'

    def test_config_default_polling_interval(self, clean_env):
        """Test default polling_interval value."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ.pop('POLLING_INTERVAL', None)

        Config = self._reload_config()
        config = Config()

        assert config.polling_interval == 5

    def test_config_custom_polling_interval(self, clean_env):
        """Test custom polling_interval from environment."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ['POLLING_INTERVAL'] = '10'

        Config = self._reload_config()
        config = Config()

        assert config.polling_interval == 10

    def test_config_default_batch_size(self, clean_env):
        """Test default batch_size value."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ.pop('BATCH_SIZE', None)

        Config = self._reload_config()
        config = Config()

        assert config.batch_size == 10

    def test_config_ai_agent_enabled_default_false(self, clean_env):
        """Test ai_agent_enabled defaults to False."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ.pop('AI_AGENT_ENABLED', None)

        Config = self._reload_config()
        config = Config()

        assert config.ai_agent_enabled is False

    def test_config_ai_agent_enabled_true(self, clean_env):
        """Test ai_agent_enabled set to True."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ['AI_AGENT_ENABLED'] = 'true'

        Config = self._reload_config()
        config = Config()

        assert config.ai_agent_enabled is True

    def test_config_ai_agent_enabled_case_insensitive(self, clean_env):
        """Test ai_agent_enabled is case insensitive."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'
        os.environ['AI_AGENT_ENABLED'] = 'TRUE'

        Config = self._reload_config()
        config = Config()

        assert config.ai_agent_enabled is True

    def test_config_worker_concurrency(self, clean_env):
        """Test worker_concurrency default and custom values."""
        os.environ['DATABASE_URL'] = 'postgresql://test:test@localhost:5432/testdb'

        # Default
        os.environ.pop('WORKER_CONCURRENCY', None)
        Config = self._reload_config()
        config = Config()
        assert config.worker_concurrency == 1

        # Custom
        os.environ['WORKER_CONCURRENCY'] = '4'
        Config = self._reload_config()
        config = Config()
        assert config.worker_concurrency == 4

