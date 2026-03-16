"""
Constants for the executor worker.
"""


class EnclaveOperations:
    """Operation names sent to the enclave via VSock."""
    GENERATE_KEYPAIR = "generate_rsa_keypair"
    EXECUTE_SCRIPT = "execute_script_rsa_hybrid"
    EXECUTE_DB_FETCH = "execute_with_db_fetch"
    HEALTH_CHECK = "health_check"
    GET_ATTESTATION = "get_attestation"


class MiddlewareModes:
    """Middleware response / request modes."""
    LEGACY = "legacy"
    DIRECT_DB = "direct_db"
    PROXY = "proxy"
