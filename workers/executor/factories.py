"""
Factories for creating executor components.
"""
import logging
from typing import Optional

from workers.executor.settings import Settings, get_settings
from workers.executor.interfaces import IExecutor, IEnclaveClient, IMiddlewareClient
from workers.executor.exceptions import ConfigurationError
from workers.executor.clients import EnclaveClient, EnclaveClientLocal, MiddlewareClient, ProxyClient
from workers.executor.executor import SecureExecutor

logger = logging.getLogger("epsilon.executor")


class EnclaveClientFactory:
    """Factory for creating enclave client instances."""

    @staticmethod
    def create_client(
        settings: Optional[Settings] = None,
    ) -> IEnclaveClient:
        """
        Create an enclave client based on configuration.

        Args:
            settings: Settings instance (uses default if None)

        Returns:
            Configured enclave client
        """
        if settings is None:
            settings = get_settings()

        try:
            if settings.enclave.use_local_client:
                logger.info("Creating local enclave client for testing")
                return EnclaveClientLocal()
            else:
                logger.info("Creating Nitro Enclave client")
                return EnclaveClient()

        except Exception as e:
            raise ConfigurationError(
                f"Failed to create enclave client: {e}",
                details={'use_local': settings.enclave.use_local_client}
            )


class MiddlewareClientFactory:
    """Factory for creating middleware client."""

    @staticmethod
    def create_client(settings: Optional[Settings] = None) -> IMiddlewareClient:
        """
        Create middleware client.

        MIDDLEWARE_ENDPOINT_URL is required.

        Args:
            settings: Optional settings object

        Returns:
            MiddlewareClient instance
        """
        settings = settings or get_settings()
        middleware_config = settings.middleware

        if not middleware_config.endpoint_url:
            raise ValueError(
                "MIDDLEWARE_ENDPOINT_URL is required. "
                "Set the endpoint URL (e.g., http://localhost:8001)"
            )

        logger.info(f"[MIDDLEWARE] Using endpoint: {middleware_config.endpoint_url}")
        logger.info(f"[MIDDLEWARE] Mode: {middleware_config.mode}")
        return MiddlewareClient(
            settings=settings,
            endpoint_url=middleware_config.endpoint_url,
            aws_region=middleware_config.aws_region,
            mode=middleware_config.mode
        )


class ProxyClientFactory:
    """Factory for creating proxy client."""

    @staticmethod
    def create_client(settings: Optional[Settings] = None) -> Optional[ProxyClient]:
        """
        Create proxy client if proxy is enabled.

        Args:
            settings: Optional settings object

        Returns:
            ProxyClient instance if enabled, None otherwise
        """
        settings = settings or get_settings()
        proxy_config = settings.proxy

        if not proxy_config.enabled:
            logger.info("[PROXY] Proxy is disabled")
            return None

        logger.info(f"[PROXY] Creating proxy client (timeout={proxy_config.request_timeout_seconds}s)")
        return ProxyClient(settings=proxy_config)


class ExecutorFactory:
    """Factory for creating job executor instances."""

    @staticmethod
    def create_executor(
        settings: Optional[Settings] = None,
        enclave_client: Optional[IEnclaveClient] = None,
        middleware_client: Optional[IMiddlewareClient] = None,
        proxy_client: Optional[ProxyClient] = None
    ) -> IExecutor:
        """
        Create a job executor with all dependencies.

        Args:
            settings: Settings instance (uses default if None)
            enclave_client: Pre-configured enclave client (creates new if None)
            middleware_client: Pre-configured middleware client (creates new if None)
            proxy_client: Pre-configured proxy client (creates new if None and proxy enabled)

        Returns:
            Configured job executor
        """
        if settings is None:
            settings = get_settings()

        try:
            if enclave_client is None:
                enclave_client = EnclaveClientFactory.create_client(settings)

            if middleware_client is None:
                middleware_client = MiddlewareClientFactory.create_client(settings)

            if proxy_client is None:
                proxy_client = ProxyClientFactory.create_client(settings)

            logger.info("Creating SecureExecutor with configured dependencies")
            logger.info(f"  - Enclave client: {type(enclave_client).__name__}")
            if middleware_client:
                logger.info(f"  - Middleware client: {type(middleware_client).__name__}")
            else:
                logger.info("  - Middleware client: disabled")
            if proxy_client:
                logger.info(f"  - Proxy client: {type(proxy_client).__name__}")
            else:
                logger.info("  - Proxy client: disabled")

            return SecureExecutor(
                enclave_client=enclave_client,
                settings=settings,
                middleware_client=middleware_client,
                proxy_client=proxy_client
            )

        except Exception as e:
            raise ConfigurationError(f"Failed to create executor: {e}")
