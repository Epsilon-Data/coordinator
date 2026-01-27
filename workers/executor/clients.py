"""
Client implementations for executor worker: enclave and middleware clients.
"""
import json
import logging
import socket
import subprocess
import sys
import tempfile
import time
import yaml
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from workers.executor.interfaces import IEnclaveClient, IMiddlewareClient, MiddlewareRequest, MiddlewareResponse
from workers.executor.exceptions import EnclaveConnectionError
from workers.executor.settings import get_settings

logger = logging.getLogger("epsilon.executor")


# Encryption constants
RSA_KEY_SIZE_BITS = 2048

# Network constants
VSOCK_TIMEOUT_SECONDS = 300
VSOCK_RECV_BUFFER_BYTES = 4 * 1024 * 1024
HEALTH_CHECK_TIMEOUT_SECONDS = 5
HTTP_ERROR_THRESHOLD = 400
HTTP_RETRY_STATUS_CODES = {502, 503, 504}
HTTP_MAX_RETRIES = 3
HTTP_RETRY_BASE_DELAY = 1.0  # seconds

# Script execution constants
SCRIPT_TIMEOUT_SECONDS = 60
BUILD_YML_PATH = 'build.yml'
CSV_OUTPUT_PATH = 'generated/data.csv'


# =============================================================================
# Enclave Client (Production - Nitro VSock)
# =============================================================================
class EnclaveClient(IEnclaveClient):
    """Production client for communicating with Nitro Enclave via VSock."""

    def __init__(self, enclave_cid: int = None):
        from workers.executor.services import CryptoService

        self._settings = get_settings()
        self._crypto = CryptoService()
        self.enclave_cid = enclave_cid or self._get_enclave_cid()
        self._connected = True

    def get_public_key(self, job_id: str) -> Tuple[str, str]:
        """Get public key from enclave for encryption."""
        try:
            request = {
                'operation': 'generate_rsa_keypair',
                'job_id': job_id,
                'key_size': RSA_KEY_SIZE_BITS
            }
            response = self._send_to_enclave(request)

            if not response.get('success'):
                raise Exception(f"Enclave keypair generation failed: {response.get('error')}")

            return response['public_key'], response['session_id']
        except Exception as e:
            logger.error(f"Failed to get enclave public key: {str(e)}")
            raise

    def encrypt_zip_data(self, zip_data: bytes, public_key: str) -> str:
        """Encrypt zip file data with hybrid encryption (RSA + AES)."""
        try:
            logger.info(f"[ENCRYPT] Starting hybrid encryption, {len(zip_data)} bytes")
            result = self._crypto.encrypt(zip_data, public_key)
            logger.info(f"[ENCRYPT] Completed, {len(result)} characters")
            return result
        except Exception as e:
            logger.error(f"[ENCRYPT] Failed: {str(e)}", exc_info=True)
            raise

    def send_encrypted_data_to_enclave(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_csv: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Send encrypted data to enclave for execution."""
        try:
            logger.info(f"[ENCLAVE] Sending data to enclave, session: {session_id[:20]}...")

            request = {
                'operation': 'execute_script_rsa_hybrid',
                'session_id': session_id,
                'encrypted_data': encrypted_zip
            }

            if encrypted_csv:
                request['encrypted_csv'] = encrypted_csv

            response = self._send_to_enclave(request)

            if response.get('success'):
                logger.info(f"[ENCLAVE] Execution successful")
                return True, response.get('output', '')
            else:
                error_msg = response.get('error', 'Unknown error')
                logger.error(f"[ENCLAVE] Execution failed: {error_msg}")
                return False, error_msg

        except Exception as e:
            logger.error(f"[ENCLAVE] Failed to send: {str(e)}", exc_info=True)
            return False, str(e)

    def _send_to_enclave(self, request_data: dict) -> dict:
        """Send request to enclave via VSock."""
        try:
            operation = request_data.get('operation', 'unknown')
            logger.info(f"[VSOCK] Creating connection for {operation}...")

            sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            sock.settimeout(VSOCK_TIMEOUT_SECONDS)

            logger.info(f"[VSOCK] Connecting to CID {self.enclave_cid}:{self._settings.enclave.vsock_port}")
            sock.connect((self.enclave_cid, self._settings.enclave.vsock_port))

            request_json = json.dumps(request_data)
            sock.sendall(request_json.encode())

            # Chunked receive to handle large responses
            response_data = self._recv_all(sock)
            response = json.loads(response_data.decode())

            sock.close()
            return response

        except Exception as e:
            logger.error(f"[VSOCK] Failed: {str(e)}", exc_info=True)
            raise

    def _recv_all(self, sock: socket.socket) -> bytes:
        """Receive complete response with chunked reading."""
        chunks = []
        while True:
            try:
                chunk = sock.recv(65536)  # 64KB chunks
                if not chunk:
                    break
                chunks.append(chunk)
            except socket.timeout:
                # If we have data and timeout, assume complete
                if chunks:
                    break
                raise
        return b''.join(chunks)

    def _get_enclave_cid(self) -> int:
        """Get the CID of the running enclave."""
        try:
            result = subprocess.run(
                ['nitro-cli', 'describe-enclaves'],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                enclaves = json.loads(result.stdout)
                if enclaves and len(enclaves) > 0:
                    cid = enclaves[0]['EnclaveCID']
                    logger.info(f"Found enclave with CID: {cid}")
                    return cid

            logger.warning("No running enclaves found")
            raise RuntimeError("No running enclaves found")

        except Exception as e:
            logger.error(f"Could not get enclave CID: {str(e)}")
            raise

    def health_check(self) -> bool:
        try:
            response = self._send_to_enclave({'operation': 'health_check'})
            return response.get('success', False)
        except Exception:
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        try:
            self.enclave_cid = self._get_enclave_cid()
            self._connected = True
            logger.info(f"Connected to enclave CID: {self.enclave_cid}")
        except Exception as e:
            self._connected = False
            raise EnclaveConnectionError(f"Failed to connect to enclave: {e}")

    def disconnect(self) -> None:
        self._connected = False
        logger.info("Disconnected from enclave")


# =============================================================================
# Enclave Client Local (Development/Testing)
# =============================================================================
class EnclaveClientLocal(IEnclaveClient):
    """Local enclave client for development and testing."""

    def __init__(self, enclave_cid: Optional[int] = None):
        from workers.executor.services import CryptoService

        self._settings = get_settings()
        self._crypto = CryptoService()
        self._sessions: Dict[str, RSAPrivateKey] = {}
        self._connected = True
        self.enclave_cid = enclave_cid or 999

    def get_public_key(self, job_id: str) -> Tuple[str, str]:
        """Generate RSA keypair and return public key."""
        private_key, public_key_pem = self._crypto.generate_keypair()
        session_id = f"local_session_{job_id}"
        self._sessions[session_id] = private_key
        logger.info(f"Generated keypair for job {job_id}")
        return public_key_pem, session_id

    def encrypt_zip_data(self, zip_data: bytes, public_key: str) -> str:
        """Encrypt zip data using hybrid encryption."""
        logger.debug(f"Encrypting {len(zip_data)} bytes")
        return self._crypto.encrypt(zip_data, public_key)

    def send_encrypted_data_to_enclave(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_csv: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Execute encrypted payloads with Zero Trust decryption."""
        logger.info(f"[ENCLAVE] Starting Zero Trust execution (session: {session_id[:20]}...)")

        if session_id not in self._sessions:
            return False, f"Session not found: {session_id}"

        private_key = self._sessions[session_id]

        try:
            decrypted_zip = self._crypto.decrypt(encrypted_zip, private_key)
            logger.debug(f"ZIP decrypted: {len(decrypted_zip)} bytes")

            decrypted_csv = None
            if encrypted_csv:
                decrypted_csv = self._crypto.decrypt(encrypted_csv, private_key)
                logger.debug(f"CSV decrypted: {len(decrypted_csv)} bytes")

            output = self._execute_script(decrypted_zip, decrypted_csv)
            logger.info("[ENCLAVE] Execution completed successfully")

            return True, output

        except Exception as e:
            logger.error(f"[ENCLAVE] Execution failed: {e}", exc_info=True)
            return False, str(e)

        finally:
            self._sessions.pop(session_id, None)

    def _execute_script(self, zip_data: bytes, csv_data: Optional[bytes] = None) -> str:
        """Extract ZIP, replace CSV, and execute script."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            zip_path = temp_path / "build.zip"
            zip_path.write_bytes(zip_data)

            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zipf.extractall(temp_path)

            if csv_data:
                csv_path = temp_path / CSV_OUTPUT_PATH
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                csv_path.write_bytes(csv_data)
                logger.debug(f"Replaced CSV: {len(csv_data)} bytes")

            script_file = self._get_script_from_build_yml(temp_path)
            if not script_file:
                return "No build.yml found or script_file not specified"

            script_path = temp_path / script_file
            if not script_path.exists():
                return f"Script file not found: {script_file}"

            return self._run_script(script_path, temp_path)

    def _get_script_from_build_yml(self, base_path: Path) -> Optional[str]:
        """Read script_file from build.yml configuration."""
        build_yml_path = base_path / BUILD_YML_PATH

        if not build_yml_path.exists():
            logger.error(f"build.yml not found at {build_yml_path}")
            return None

        try:
            with open(build_yml_path, 'r', encoding='utf-8') as f:
                build_config = yaml.safe_load(f)

            if not isinstance(build_config, dict):
                return None

            analysis = build_config.get('analysis', {})
            script_file = analysis.get('script_file')

            if script_file:
                logger.info(f"Using script from build.yml: {script_file}")

            return script_file

        except Exception as e:
            logger.error(f"Failed to read build.yml: {e}")
            return None

    def _run_script(self, script_path: Path, cwd: Path) -> str:
        """Execute Python script and capture output."""
        logger.info(f"Executing: {script_path.name}")

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=SCRIPT_TIMEOUT_SECONDS,
                cwd=str(cwd)
            )

            if result.returncode == 0:
                output = result.stdout
                if result.stderr:
                    output += f"\n--- STDERR ---\n{result.stderr}"
                return output

            return (
                f"Script failed (exit code {result.returncode})\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )

        except subprocess.TimeoutExpired:
            return f"Script timed out after {SCRIPT_TIMEOUT_SECONDS} seconds"

    def health_check(self) -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self._connected = True
        logger.debug("Connected to local enclave simulation")

    def disconnect(self) -> None:
        self._connected = False
        self._sessions.clear()
        logger.debug("Disconnected from local enclave simulation")


# =============================================================================
# Middleware Client
# =============================================================================
class MiddlewareClient(IMiddlewareClient):
    def __init__(
        self,
        settings=None,
        endpoint_url: Optional[str] = None,
        aws_region: str = "ap-southeast-2",
        mode: str = "local"  # "aws" or "local"
    ):
        self._settings = settings or get_settings()
        self._endpoint_url = endpoint_url or self._settings.middleware.endpoint_url
        self._timeout = self._settings.middleware.timeout_seconds
        self._aws_region = aws_region
        self._mode = mode

        if self._endpoint_url:
            self._endpoint_url = self._endpoint_url.rstrip('/')

        # Only initialize boto3 session for AWS mode
        self._session = boto3.Session() if self._mode == "aws" else None

        logger.info(f"[MIDDLEWARE] Initialized with endpoint: {self._endpoint_url}")
        logger.info(f"[MIDDLEWARE] Mode: {self._mode}")
        if self._mode == "aws":
            logger.info(f"[MIDDLEWARE] AWS Region: {self._aws_region}")

    def _sign_request(self, method: str, url: str, payload: dict) -> tuple:

        credentials = self._session.get_credentials()

        if not credentials:
            raise RuntimeError("No AWS credentials available")

        # Use frozen credentials for EC2 instance role
        frozen_credentials = credentials.get_frozen_credentials()
        body = json.dumps(payload)

        # Create AWS request
        aws_request = AWSRequest(
            method=method,
            url=url,
            data=body,
            headers={"Content-Type": "application/json"}
        )

        # Sign with SigV4 for Lambda service
        SigV4Auth(frozen_credentials, "lambda", self._aws_region).add_auth(aws_request)

        prepared = aws_request.prepare()
        return prepared.url, prepared.body, dict(prepared.headers)

    def fetch_encrypted_csv(self, request: MiddlewareRequest) -> MiddlewareResponse:
        url = self._endpoint_url
        logger.info(f"[MIDDLEWARE] POST {url}")
        logger.info(f"[MIDDLEWARE] Dataset: {request.dataset_id}, Archetype: {request.archetype_id}")

        payload = {
            'dataset_id': request.dataset_id,
            'archetype_id': request.archetype_id,
            'public_key': request.public_key or '',
        }

        last_error = None

        for attempt in range(HTTP_MAX_RETRIES):
            try:
                if self._mode == "aws":
                    # Sign the request with AWS SigV4
                    signed_url, signed_body, signed_headers = self._sign_request("POST", url, payload)
                    logger.debug(f"[MIDDLEWARE] Request signed with SigV4")
                    response = requests.post(
                        signed_url,
                        data=signed_body,
                        timeout=self._timeout,
                        headers=signed_headers
                    )
                else:
                    # Local mode: simple HTTP POST
                    logger.debug(f"[MIDDLEWARE] Local mode request")
                    response = requests.post(
                        url,
                        json=payload,
                        timeout=self._timeout,
                        headers={"Content-Type": "application/json"}
                    )

                # Check for retryable status codes (502, 503, 504)
                if response.status_code in HTTP_RETRY_STATUS_CODES:
                    delay = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"[MIDDLEWARE] HTTP {response.status_code}, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{HTTP_MAX_RETRIES})"
                    )
                    time.sleep(delay)
                    continue

                if response.status_code >= HTTP_ERROR_THRESHOLD:
                    error_data = response.json() if response.content else {}
                    error_msg = error_data.get('error', f"HTTP {response.status_code}")
                    logger.error(f"[MIDDLEWARE] Error: {error_msg}")
                    return MiddlewareResponse(success=False, error=error_msg)

                data = response.json()

                if not data.get('success'):
                    return MiddlewareResponse(success=False, error=data.get('error', 'Unknown error'))

                encrypted_csv = data.get('csv', '')
                logger.info(f"[MIDDLEWARE] Received encrypted CSV ({len(encrypted_csv)} chars)")

                return MiddlewareResponse(
                    success=True,
                    encrypted_csv=encrypted_csv,
                    csv_metadata=data.get('metadata', {}),
                    request_id=response.headers.get('X-Request-ID', 'http-request')
                )

            except requests.exceptions.Timeout:
                last_error = f"Request timeout after {self._timeout}s"
                logger.warning(f"[MIDDLEWARE] Timeout (attempt {attempt + 1}/{HTTP_MAX_RETRIES})")

            except requests.exceptions.ConnectionError as e:
                last_error = f"Cannot connect to middleware at {self._endpoint_url}"
                delay = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"[MIDDLEWARE] Connection error, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{HTTP_MAX_RETRIES}): {e}"
                )
                time.sleep(delay)

            except Exception as e:
                logger.error(f"[MIDDLEWARE] Error: {e}", exc_info=True)
                return MiddlewareResponse(success=False, error=str(e))

        # All retries exhausted
        logger.error(f"[MIDDLEWARE] All {HTTP_MAX_RETRIES} retries exhausted")
        return MiddlewareResponse(success=False, error=last_error or "Request failed after retries")

    def health_check(self) -> bool:
        try:
            url = self._endpoint_url

            if self._mode == "aws":
                credentials = self._session.get_credentials()
                if not credentials:
                    return False

                frozen_credentials = credentials.get_frozen_credentials()
                aws_request = AWSRequest(method="GET", url=url, headers={})
                SigV4Auth(frozen_credentials, "lambda", self._aws_region).add_auth(aws_request)

                prepared = aws_request.prepare()
                response = requests.get(
                    prepared.url,
                    timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
                    headers=dict(prepared.headers)
                )
            else:
                # Local mode: simple HTTP GET
                response = requests.get(url, timeout=HEALTH_CHECK_TIMEOUT_SECONDS)

            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[MIDDLEWARE] Health check failed: {e}")
            return False

    @property
    def is_enabled(self) -> bool:
        return bool(self._endpoint_url)
