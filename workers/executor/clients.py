"""
Client implementations for executor worker: enclave and middleware clients.
"""
import hashlib
import hmac
import json
import logging
import os
import socket
import struct
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

try:
    from workers.executor.local_attestation import generate_local_attestation
except ImportError:
    generate_local_attestation = None

from workers.executor.interfaces import IEnclaveClient, IMiddlewareClient, MiddlewareRequest, MiddlewareResponse, ProxyResponse
from workers.executor.services import CryptoService
from workers.executor.exceptions import EnclaveConnectionError
from workers.executor.settings import get_settings, ProxyConfig
from workers.executor.constants import EnclaveOperations, MiddlewareModes

logger = logging.getLogger("epsilon.executor")


# Encryption constants
RSA_KEY_SIZE_BITS = 2048

def _safe_int_env(name: str, default: int, min_val: int = 1, max_val: int = 3600) -> int:
    """Parse an integer env var with bounds validation."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r}, using default {default}")
        return default
    if value < min_val or value > max_val:
        logger.warning(f"{name}={value} out of range [{min_val}, {max_val}], using default {default}")
        return default
    return value


# Network constants (configurable via env vars)
VSOCK_TIMEOUT_SECONDS = _safe_int_env('VSOCK_TIMEOUT_SECONDS', 300, min_val=1, max_val=600)
VSOCK_RECV_BUFFER_BYTES = 4 * 1024 * 1024
TD_AGENT_TIMEOUT_SECONDS = _safe_int_env('TD_AGENT_TIMEOUT_SECONDS', 300, min_val=1, max_val=600)
HEALTH_CHECK_TIMEOUT_SECONDS = _safe_int_env('HEALTH_CHECK_TIMEOUT_SECONDS', 5, min_val=1, max_val=60)
HTTP_ERROR_THRESHOLD = 400
HTTP_RETRY_STATUS_CODES = {502, 503, 504}
HTTP_MAX_RETRIES = _safe_int_env('HTTP_MAX_RETRIES', 3, min_val=0, max_val=10)
HTTP_RETRY_BASE_DELAY = 1.0  # seconds

# Script execution constants
SCRIPT_TIMEOUT_SECONDS = _safe_int_env('SCRIPT_TIMEOUT_SECONDS', 60, min_val=1, max_val=3600)
BUILD_YML_PATH = 'build.yml'
CSV_OUTPUT_PATH = 'generated/data.csv'


# =============================================================================
# Enclave Client (Production - Nitro VSock)
# =============================================================================
class EnclaveClient(IEnclaveClient):
    """Production client for communicating with Nitro Enclave via VSock."""

    def __init__(self, enclave_cid: int = None):
        self._settings = get_settings()
        self._crypto = CryptoService()
        # Use provided CID, or external CID from settings, or auto-detect
        self.enclave_cid = enclave_cid or self._settings.enclave.external_enclave_cid or self._get_enclave_cid()
        self._connected = False
        logger.info(f"[ENCLAVE] Initialized with CID: {self.enclave_cid}")

    def get_public_key(self, job_id: str) -> Tuple[str, str]:
        """Get public key from enclave for encryption."""
        try:
            request = {
                'operation': EnclaveOperations.GENERATE_KEYPAIR,
                'job_id': job_id,
                'key_size': RSA_KEY_SIZE_BITS
            }
            response = self._send_to_enclave(request)

            if not response.get('success'):
                raise EnclaveConnectionError(f"Enclave keypair generation failed: {response.get('error')}")

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
        encrypted_csv: Optional[str] = None,
        atl_nonce: Optional[bytes] = None,
        atl_context_hash: Optional[bytes] = None,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Send encrypted data to enclave for execution.

        When atl_nonce / atl_context_hash are provided (commitment-then-dispatch
        path), they travel in the request payload as hex strings so the enclave
        can bind them into the hardware-signed user_data. Hex avoids the binary-
        in-JSON problem on the VSock RPC.

        Returns:
            Tuple of (success, output, attestation_data)
        """
        try:
            logger.info(f"[ENCLAVE] Sending data to enclave, session: {session_id[:20]}...")

            request = {
                'operation': EnclaveOperations.EXECUTE_SCRIPT,
                'session_id': session_id,
                'encrypted_data': encrypted_zip
            }

            if encrypted_csv:
                request['encrypted_csv'] = encrypted_csv

            if atl_nonce is not None:
                request['atl_nonce'] = atl_nonce.hex()
            if atl_context_hash is not None:
                request['atl_context_hash'] = atl_context_hash.hex()

            response = self._send_to_enclave(request)

            if response.get('success'):
                logger.info(f"[ENCLAVE] Execution successful")

                # Extract attestation data
                attestation = response.get('attestation')
                enclave_proof = response.get('enclave_proof', {})

                if attestation:
                    logger.info(f"[ENCLAVE] Attestation received: {attestation.get('attestation', {}).get('attestation_document_length', 0)} bytes")
                    logger.info(f"[ENCLAVE] Enclave proof: ran_in_enclave={enclave_proof.get('ran_in_enclave')}, attestation_available={enclave_proof.get('attestation_available')}")
                else:
                    logger.warning("[ENCLAVE] No attestation in response")

                # Pass enclave-internal timing through in the attestation dict
                enclave_timing = response.get('timing')
                if enclave_timing and attestation is not None:
                    attestation['timing'] = enclave_timing

                return True, response.get('output', ''), attestation
            else:
                error_msg = response.get('error', 'Unknown error')
                logger.error(f"[ENCLAVE] Execution failed: {error_msg}")
                return False, error_msg, None

        except Exception as e:
            logger.error(f"[ENCLAVE] Failed to send: {str(e)}", exc_info=True)
            return False, str(e), None

    def send_encrypted_data_with_db_fetch(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_credentials: str,
        sql_query: str,
        atl_nonce: Optional[bytes] = None,
        atl_context_hash: Optional[bytes] = None,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Send encrypted data to enclave for execution with direct DB fetch.

        The enclave will decrypt credentials, connect to the DB, fetch data,
        generate CSV internally, and execute the script.

        When atl_nonce / atl_context_hash are provided (commitment-then-dispatch
        path), they travel in the request payload as hex strings so the enclave
        can bind them into the hardware-signed user_data.

        Returns:
            Tuple of (success, output, attestation_data)
        """
        try:
            logger.info(f"[ENCLAVE] Sending data for DB fetch execution, session: {session_id[:20]}...")

            request = {
                'operation': EnclaveOperations.EXECUTE_DB_FETCH,
                'session_id': session_id,
                'encrypted_data': encrypted_zip,
                'encrypted_credentials': encrypted_credentials,
                'sql_query': sql_query
            }

            if atl_nonce is not None:
                request['atl_nonce'] = atl_nonce.hex()
            if atl_context_hash is not None:
                request['atl_context_hash'] = atl_context_hash.hex()

            response = self._send_to_enclave(request)

            if response.get('success'):
                logger.info(f"[ENCLAVE] DB fetch execution successful")

                attestation = response.get('attestation')
                enclave_proof = response.get('enclave_proof', {})

                if enclave_proof.get('db_fetch_inside_enclave'):
                    logger.info("[ENCLAVE] Confirmed: data fetched inside enclave")

                if attestation:
                    logger.info(f"[ENCLAVE] Attestation received")

                enclave_timing = response.get('timing')
                if enclave_timing and attestation is not None:
                    attestation['timing'] = enclave_timing

                return True, response.get('output', ''), attestation
            else:
                error_msg = response.get('error', 'Unknown error')
                logger.error(f"[ENCLAVE] DB fetch execution failed: {error_msg}")
                return False, error_msg, None

        except Exception as e:
            logger.error(f"[ENCLAVE] Failed to send DB fetch request: {str(e)}", exc_info=True)
            return False, str(e), None

    def _send_to_enclave(self, request_data: dict) -> dict:
        """Send request to enclave via VSock."""
        operation = request_data.get('operation', 'unknown')
        logger.info(f"[VSOCK] Creating connection for {operation}...")

        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        try:
            sock.settimeout(VSOCK_TIMEOUT_SECONDS)

            logger.info(f"[VSOCK] Connecting to CID {self.enclave_cid}:{self._settings.enclave.vsock_port}")
            sock.connect((self.enclave_cid, self._settings.enclave.vsock_port))

            request_json = json.dumps(request_data)
            sock.sendall(request_json.encode())

            # Chunked receive to handle large responses
            response_data = self._recv_all(sock)
            response = json.loads(response_data.decode())

            return response

        except Exception as e:
            logger.error(f"[VSOCK] Failed: {str(e)}", exc_info=True)
            raise
        finally:
            sock.close()

    MAX_RESPONSE_SIZE = 100 * 1024 * 1024  # 100 MB

    def _recv_all(self, sock: socket.socket) -> bytes:
        """Receive complete response with chunked reading."""
        chunks = []
        total_size = 0
        while True:
            try:
                chunk = sock.recv(65536)  # 64KB chunks
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > self.MAX_RESPONSE_SIZE:
                    raise MemoryError(
                        f"Response exceeded max size ({self.MAX_RESPONSE_SIZE // (1024*1024)} MB)"
                    )
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
            response = self._send_to_enclave({'operation': EnclaveOperations.HEALTH_CHECK})
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
# Enclave Client TDX (Production - Intel TDX Confidential VM over TCP)
# =============================================================================
class EnclaveClientTDX(IEnclaveClient):
    """Client for the Epsilon TDX enclave agent (TcpEnclaveServer).

    A TDX Confidential VM is a whole trust domain, so there is no host<->enclave
    VSock channel; the coordinator reaches the in-TD agent over TCP. The request
    operations and the returned attestation dict are identical to the Nitro
    client -- only the transport (AF_INET + 4-byte length-prefix framing, which
    the agent's server speaks) and the attestation format (Intel TDX quote vs
    Nitro NSM document) differ.
    """

    HEADER_SIZE = 4  # 4-byte big-endian length prefix (see server.EnclaveServer)
    MAX_RESPONSE_SIZE = 100 * 1024 * 1024  # 100 MB

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self._settings = get_settings()
        self._crypto = CryptoService()
        self._host = host or self._settings.enclave.td_host
        self._port = port or self._settings.enclave.td_port
        self._connected = False
        logger.info(f"[TDX] Initialized agent client for tcp://{self._host}:{self._port}")

    def get_public_key(self, job_id: str) -> Tuple[str, str]:
        """Get a freshly generated public key from the TDX agent."""
        request = {
            'operation': EnclaveOperations.GENERATE_KEYPAIR,
            'job_id': job_id,
            'key_size': RSA_KEY_SIZE_BITS,
        }
        response = self._send_to_td(request)
        if not response.get('success'):
            raise EnclaveConnectionError(f"TDX agent keypair generation failed: {response.get('error')}")
        return response['public_key'], response['session_id']

    def encrypt_zip_data(self, zip_data: bytes, public_key: str) -> str:
        """Hybrid-encrypt the bundle. Substrate-independent (identical to Nitro)."""
        return self._crypto.encrypt(zip_data, public_key)

    def send_encrypted_data_to_enclave(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_csv: Optional[str] = None,
        atl_nonce: Optional[bytes] = None,
        atl_context_hash: Optional[bytes] = None,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Send encrypted data to the TDX agent for execution.

        Returns:
            Tuple of (success, output/error, attestation_data).
        """
        request = {
            'operation': EnclaveOperations.EXECUTE_SCRIPT,
            'session_id': session_id,
            'encrypted_data': encrypted_zip,
        }
        if encrypted_csv:
            request['encrypted_csv'] = encrypted_csv
        if atl_nonce is not None:
            request['atl_nonce'] = atl_nonce.hex()
        if atl_context_hash is not None:
            request['atl_context_hash'] = atl_context_hash.hex()
        return self._execute(request, session_id)

    def send_encrypted_data_with_db_fetch(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_credentials: str,
        sql_query: str,
        atl_nonce: Optional[bytes] = None,
        atl_context_hash: Optional[bytes] = None,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Send encrypted data to the TDX agent for execution with direct DB fetch."""
        request = {
            'operation': EnclaveOperations.EXECUTE_DB_FETCH,
            'session_id': session_id,
            'encrypted_data': encrypted_zip,
            'encrypted_credentials': encrypted_credentials,
            'sql_query': sql_query,
        }
        if atl_nonce is not None:
            request['atl_nonce'] = atl_nonce.hex()
        if atl_context_hash is not None:
            request['atl_context_hash'] = atl_context_hash.hex()
        return self._execute(request, session_id)

    def _execute(self, request: dict, session_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """Send an execution request and normalise the (success, output, attestation) triple."""
        try:
            logger.info(f"[TDX] Dispatching {request['operation']} (session: {session_id[:20]}...)")
            response = self._send_to_td(request)
            if not response.get('success'):
                error_msg = response.get('error', 'Unknown error')
                logger.error(f"[TDX] Execution failed: {error_msg}")
                return False, error_msg, None

            attestation = response.get('attestation')
            enclave_timing = response.get('timing')
            if enclave_timing and attestation is not None:
                attestation['timing'] = enclave_timing
            return True, response.get('output', ''), attestation
        except Exception as e:
            logger.error(f"[TDX] Failed to send request: {e}", exc_info=True)
            return False, str(e), None

    def health_check(self) -> bool:
        try:
            response = self._send_to_td({'operation': EnclaveOperations.HEALTH_CHECK})
            return response.get('success', False)
        except Exception:
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        """Probe the agent endpoint and mark the client connected."""
        try:
            with socket.create_connection((self._host, self._port), timeout=HEALTH_CHECK_TIMEOUT_SECONDS):
                pass
            self._connected = True
            logger.info(f"[TDX] Connected to agent tcp://{self._host}:{self._port}")
        except Exception as e:
            self._connected = False
            raise EnclaveConnectionError(f"Failed to connect to TDX agent: {e}")

    def disconnect(self) -> None:
        self._connected = False
        logger.info("[TDX] Disconnected from agent")

    # ------------------------------------------------------------------
    # Transport: TCP with 4-byte big-endian length-prefix framing
    # ------------------------------------------------------------------

    def _send_to_td(self, request_data: dict) -> dict:
        """Send one length-prefixed JSON request and read the framed response."""
        with socket.create_connection((self._host, self._port), timeout=TD_AGENT_TIMEOUT_SECONDS) as sock:
            payload = json.dumps(request_data).encode('utf-8')
            sock.sendall(struct.pack('!I', len(payload)) + payload)

            header = self._recv_exact(sock, self.HEADER_SIZE)
            if header is None:
                raise EnclaveConnectionError("TDX agent closed connection before response header")
            msg_len = struct.unpack('!I', header)[0]
            if msg_len == 0 or msg_len > self.MAX_RESPONSE_SIZE:
                raise EnclaveConnectionError(f"Invalid TDX agent response length: {msg_len}")

            body = self._recv_exact(sock, msg_len)
            if body is None:
                raise EnclaveConnectionError("TDX agent closed connection mid-response")
            return json.loads(body.decode('utf-8'))

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
        """Receive exactly n bytes, or None if the peer closes early."""
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)


# =============================================================================
# Enclave Client Local (Development/Testing)
# =============================================================================
class EnclaveClientLocal(IEnclaveClient):
    """Local enclave client for development and testing."""

    def __init__(self, enclave_cid: Optional[int] = None):
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
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Execute encrypted payloads with Zero Trust decryption.

        Returns:
            Tuple of (success, output, attestation_data) - attestation is None for local mode
        """
        logger.info(f"[ENCLAVE] Starting Zero Trust execution (session: {session_id[:20]}...)")

        if session_id not in self._sessions:
            return False, f"Session not found: {session_id}", None

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

            # Generate local attestation document for dev/testing
            attestation = self._generate_local_attestation(session_id, output, decrypted_zip, decrypted_csv)
            return True, output, attestation

        except Exception as e:
            logger.error(f"[ENCLAVE] Execution failed: {e}", exc_info=True)
            return False, str(e), None

        finally:
            self._sessions.pop(session_id, None)

    def _generate_local_attestation(
        self,
        session_id: str,
        output: str,
        decrypted_zip: Optional[bytes] = None,
        decrypted_csv: Optional[bytes] = None
    ) -> Optional[Dict]:
        """Generate a local attestation document after successful execution."""
        try:
            # Build user_data hash bundle (same structure as real enclave)
            script_hash = hashlib.sha256(decrypted_zip).hexdigest() if decrypted_zip else ""
            dataset_hash = hashlib.sha256(decrypted_csv).hexdigest() if decrypted_csv else ""
            output_hash = hashlib.sha256(output.encode()).hexdigest()

            user_data = json.dumps({
                "job_id": session_id.replace("local_session_", ""),
                "script_hash": script_hash,
                "dataset_hash": dataset_hash,
                "output_hash": output_hash,
                "timestamp": int(time.time()),
            }).encode()

            if generate_local_attestation is None:
                raise ImportError("local_attestation module not available (cbor2 not installed)")

            ok, result = generate_local_attestation(
                user_data=user_data,
                nonce=session_id.encode()[:64],
            )

            if ok:
                logger.info(f"[ATTESTATION-LOCAL] Generated local attestation: {len(result.get('document', ''))} chars")
                return {
                    "attestation": {
                        "attestation_document": result["document"],
                        "module_id": result.get("module_id", "local"),
                        "is_local": True,
                    }
                }
            else:
                logger.warning(f"[ATTESTATION-LOCAL] Generation failed: {result}")
                return None

        except Exception as e:
            logger.warning(f"[ATTESTATION-LOCAL] Could not generate: {e}")
            return None

    def send_encrypted_data_with_db_fetch(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_credentials: str,
        sql_query: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Local simulation of DB fetch execution.

        In local mode, we cannot actually connect to a DB via tap0.
        This decrypts the credentials and logs them, then falls back
        to executing without CSV data.
        """
        logger.warning("[ENCLAVE-LOCAL] direct_db mode not fully supported in local mode")
        logger.info(f"[ENCLAVE-LOCAL] Would execute SQL: {sql_query[:100]}...")

        if session_id not in self._sessions:
            return False, f"Session not found: {session_id}", None

        private_key = self._sessions[session_id]

        try:
            # Decrypt ZIP and execute without CSV (local simulation)
            decrypted_zip = self._crypto.decrypt(encrypted_zip, private_key)
            output = self._execute_script(decrypted_zip)
            return True, output, None
        except Exception as e:
            logger.error(f"[ENCLAVE-LOCAL] DB fetch execution failed: {e}", exc_info=True)
            return False, str(e), None
        finally:
            self._sessions.pop(session_id, None)

    def _execute_script(self, zip_data: bytes, csv_data: Optional[bytes] = None) -> str:
        """Extract ZIP, replace CSV, and execute script."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            zip_path = temp_path / "build.zip"
            zip_path.write_bytes(zip_data)

            with zipfile.ZipFile(zip_path, 'r') as zipf:
                for member in zipf.namelist():
                    member_path = (temp_path / member).resolve()
                    if not member_path.is_relative_to(temp_path.resolve()):
                        raise ValueError(f"Zip member escapes target dir: {member}")
                    zipf.extract(member, temp_path)

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
        logger.info(f"[MIDDLEWARE-AUTH] Starting SigV4 signing for {method} {url}")

        credentials = self._session.get_credentials()

        if not credentials:
            logger.error("[MIDDLEWARE-AUTH] No AWS credentials available in boto3 session")
            raise RuntimeError("No AWS credentials available")

        # Use frozen credentials for EC2 instance role
        frozen_credentials = credentials.get_frozen_credentials()

        # Log credential info (safely, DEBUG only)
        access_key = frozen_credentials.access_key
        has_secret = bool(frozen_credentials.secret_key)
        has_token = bool(frozen_credentials.token)
        logger.debug(f"[MIDDLEWARE-AUTH] Access Key: {access_key[:8]}...")
        logger.debug(f"[MIDDLEWARE-AUTH] Secret Key present: {has_secret}, Token present: {has_token}")
        logger.info(f"[MIDDLEWARE-AUTH] Signing region: {self._aws_region}")

        body = json.dumps(payload)
        logger.debug(f"[MIDDLEWARE-AUTH] Request body length: {len(body)} bytes")

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
        signed_headers = dict(prepared.headers)

        # Log signed headers (DEBUG only, partial for security)
        logger.debug("[MIDDLEWARE-AUTH] Signed request headers:")
        for key, value in signed_headers.items():
            if key.lower() in ('authorization', 'x-amz-security-token'):
                logger.debug(f"  {key}: {value[:20]}...")
            else:
                logger.debug(f"  {key}: {value}")

        return prepared.url, prepared.body, signed_headers

    def fetch_encrypted_csv(self, request: MiddlewareRequest, mode: str = MiddlewareModes.LEGACY) -> MiddlewareResponse:
        url = self._endpoint_url
        logger.info(f"[MIDDLEWARE] POST {url} (mode={mode})")
        logger.info(f"[MIDDLEWARE] Dataset: {request.dataset_id}, Archetype: {request.archetype_id}")

        payload = {
            'dataset_id': request.dataset_id,
            'archetype_id': request.archetype_id,
            'public_key': request.public_key or '',
            'mode': mode,
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
                    logger.error(f"[MIDDLEWARE-ERROR] HTTP {response.status_code} received")
                    logger.debug(f"[MIDDLEWARE-ERROR] Response body: {response.text[:500]}")

                    # Parse error if JSON
                    try:
                        error_data = response.json() if response.content else {}
                        error_msg = error_data.get('error') or error_data.get('message') or error_data.get('Message') or f"HTTP {response.status_code}"
                    except Exception:
                        error_msg = f"HTTP {response.status_code}"

                    # Specific 403 debugging (at debug level to avoid leaking config)
                    if response.status_code == 403:
                        logger.error(f"[MIDDLEWARE-ERROR] 403 Forbidden — check IAM role, resource policy, SigV4 config, or clock skew")
                        logger.debug(f"[MIDDLEWARE-ERROR] Mode: {self._mode}, Region: {self._aws_region}")

                    return MiddlewareResponse(success=False, error=error_msg)

                # Check for empty response body
                if not response.content:
                    logger.error(f"[MIDDLEWARE] Empty response body (HTTP {response.status_code})")
                    return MiddlewareResponse(success=False, error=f"Empty response from middleware (HTTP {response.status_code})")

                logger.info(f"[MIDDLEWARE] Response status: {response.status_code}, body length: {len(response.content)} bytes")

                data = response.json()

                if not data.get('success'):
                    return MiddlewareResponse(success=False, error=data.get('error', 'Unknown error'))

                response_mode = data.get('mode', MiddlewareModes.LEGACY)

                if response_mode == MiddlewareModes.DIRECT_DB:
                    # Direct DB mode: encrypted credentials + SQL query
                    encrypted_credentials = data.get('encrypted_credentials', '')
                    sql_query = data.get('sql_query', '')
                    logger.info(
                        f"[MIDDLEWARE] direct_db response: credentials={len(encrypted_credentials)} chars, "
                        f"sql_query={len(sql_query)} chars"
                    )
                    return MiddlewareResponse(
                        success=True,
                        mode=MiddlewareModes.DIRECT_DB,
                        encrypted_credentials=encrypted_credentials,
                        sql_query=sql_query,
                        csv_metadata=data.get('metadata', {}),
                        request_id=response.headers.get('X-Request-ID', 'http-request')
                    )
                elif response_mode == MiddlewareModes.PROXY:
                    # Proxy mode: proxy connection info
                    proxy_info = data.get('proxy_info', {})
                    logger.info(f"[MIDDLEWARE] Proxy mode: port={proxy_info.get('assignedPort')}, status={proxy_info.get('status')}")
                    return MiddlewareResponse(
                        success=True,
                        mode=MiddlewareModes.PROXY,
                        proxy_info=proxy_info,
                        csv_metadata=data.get('metadata', {}),
                        request_id=response.headers.get('X-Request-ID', 'http-request')
                    )
                else:
                    # Legacy mode: encrypted CSV
                    encrypted_csv = data.get('csv', '')
                    logger.info(f"[MIDDLEWARE] Received encrypted CSV ({len(encrypted_csv)} chars)")
                    return MiddlewareResponse(
                        success=True,
                        mode=MiddlewareModes.LEGACY,
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

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                # Retryable parsing errors
                last_error = f"Response parsing error: {e}"
                delay = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"[MIDDLEWARE] Parse error, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{HTTP_MAX_RETRIES}): {e}"
                )
                time.sleep(delay)

            except Exception as e:
                logger.error(f"[MIDDLEWARE] Unexpected error: {e}", exc_info=True)
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


# =============================================================================
# Proxy Client
# =============================================================================
class ProxyClient:
    """Client for communicating with data owner proxies via local tunnel."""

    def __init__(self, settings: ProxyConfig):
        self._settings = settings
        self._timeout = settings.request_timeout_seconds
        self._rathole_host = settings.rathole_host
        logger.info(f"[PROXY] Initialized (enabled={settings.enabled}, host={self._rathole_host}, timeout={self._timeout}s)")

    def fetch_encrypted_csv(
        self,
        request: MiddlewareRequest,
        public_key: str,
        attestation_doc: str,
        proxy_info: dict
    ) -> ProxyResponse:
        """Fetch encrypted CSV data through the proxy tunnel.

        Args:
            request: The middleware request containing job_id, dataset_id, etc.
            public_key: Enclave public key for encryption.
            attestation_doc: Enclave attestation document.
            proxy_info: Proxy tunnel info including 'assigned_port' and 'proxy_token'.

        Returns:
            ProxyResponse with encrypted_csv and metadata.
        """
        assigned_port = proxy_info['assigned_port']
        proxy_token = proxy_info.get('proxy_token', '')
        session_id = proxy_info.get('session_id', '')
        sql_query = proxy_info.get('sql_query', '')

        logger.info(f"[PROXY] proxy_info keys: {list(proxy_info.keys())}")
        logger.info(f"[PROXY] assigned_port={assigned_port}, has_token={bool(proxy_token)}, "
                     f"has_sql={bool(sql_query)}, sql_len={len(sql_query)}")

        if not sql_query:
            logger.warning("[PROXY] No SQL query in proxy_info — middleware may not have returned it")

        timestamp = str(int(time.time()))
        request_id = f"coord-{request.job_id}-{timestamp}"

        url = f"http://{self._rathole_host}:{assigned_port}/query"

        payload = {
            'request_id': request_id,
            'session_id': session_id,
            'sql_query': sql_query,
            'enclave_public_key': public_key,
            'attestation_document': attestation_doc,
            'timestamp': int(timestamp)
        }

        # HMAC-SHA256 signature: sign(proxy_token, request_id + session_id + timestamp)
        sign_data = request_id + session_id + timestamp
        signature = hmac.new(
            proxy_token.encode('utf-8'),
            sign_data.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        headers = {
            'Content-Type': 'application/json',
            'X-Signature': signature,
            'X-Request-ID': request_id
        }

        logger.info(f"[PROXY] POST {url} (request_id={request_id}, dataset={request.dataset_id})")

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._timeout
            )

            logger.info(f"[PROXY] Response: HTTP {response.status_code}, body_len={len(response.content)}")

            if response.status_code >= HTTP_ERROR_THRESHOLD:
                error_msg = f"Proxy returned HTTP {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', error_msg)
                    logger.error(f"[PROXY] Error response: {error_data}")
                except Exception:
                    logger.error(f"[PROXY] Raw error response: {response.text[:500]}")
                raise RuntimeError(f"[PROXY] Query failed: {error_msg}")

            data = response.json()

            if not data.get('success', False):
                logger.error(f"[PROXY] Query not successful: {data}")
                raise RuntimeError(f"[PROXY] Query failed: {data.get('error', 'Unknown error')}")

            encrypted_csv = data.get('encrypted_csv', '')
            metadata = data.get('metadata', {})

            logger.info(f"[PROXY] Success! Encrypted CSV: {len(encrypted_csv)} chars, "
                        f"metadata: {metadata}")

            return ProxyResponse(
                encrypted_csv=encrypted_csv,
                metadata=metadata
            )

        except requests.exceptions.Timeout:
            logger.error(f"[PROXY] Timeout after {self._timeout}s connecting to {url}")
            raise RuntimeError(f"[PROXY] Request timeout after {self._timeout}s")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[PROXY] Connection failed to {url}: {e}")
            raise RuntimeError(f"[PROXY] Cannot connect to proxy at {self._rathole_host}:{assigned_port}: {e}")

    def health_check(self, proxy_info: dict) -> bool:
        """Check if the proxy tunnel is healthy.

        Args:
            proxy_info: Proxy tunnel info including 'assigned_port'.

        Returns:
            True if healthy, False otherwise.
        """
        assigned_port = proxy_info['assigned_port']
        url = f"http://{self._rathole_host}:{assigned_port}/health"

        try:
            logger.info(f"[PROXY] Health check: GET {url}")
            response = requests.get(url, timeout=HEALTH_CHECK_TIMEOUT_SECONDS)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[PROXY] Health check failed for port {assigned_port}: {e}")
            return False
