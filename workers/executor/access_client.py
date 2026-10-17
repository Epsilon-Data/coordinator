"""
Project access check against the Epsilon API, made before any real data is requested.

The coordinator client gets a token, then asks whether the job's submitter (their
Keycloak sub) may analyse the job's platform project:

    POST {EPSILON_API_URL}/coordinator/auth  {"clientId", "clientSecret"}
         -> {"access_token", "expires_in", ...}
    GET  {EPSILON_API_URL}/coordinator/{projectId}?userId={sub}  (Bearer token)
         -> {"isApproved": bool}

A timeout, dropped connection or 502/503/504/52x answer is retried once after a
short jittered delay. Every failure to get a decision raises ProjectAccessError.
The client secret and the tokens never appear in logs or error messages.
"""
import logging
import random
import time
from typing import Optional

import requests

from workers.executor.exceptions import ProjectAccessError
from workers.executor.settings import ProjectAccessConfig

logger = logging.getLogger("epsilon.executor")

API_TIMEOUT_SECONDS = 10
# A cached token is replaced this long before it expires
TOKEN_EXPIRY_MARGIN_SECONDS = 30
# Transient failures are retried once: at most 2 x 10 s + 1.5 s per call
API_ATTEMPTS = 2
RETRY_DELAY_SECONDS = (0.5, 1.5)
# Gateway / Cloudflare answers that say nothing about the request itself
TRANSIENT_STATUS_CODES = frozenset({502, 503, 504, 520, 521, 522, 523, 524})


class ProjectAccessClient:
    """Asks the Epsilon API whether a user may analyse a platform project."""

    def __init__(self, config: ProjectAccessConfig):
        self._config = config
        self._token: Optional[str] = None
        self._token_valid_until = 0.0

    def check_project_access(self, project_id: str, user_sub: str) -> bool:
        """The API's isApproved decision for user_sub on project_id.

        Raises ProjectAccessError when no decision could be obtained.
        """
        if not self._config.is_configured:
            raise ProjectAccessError(
                "the check is not configured (EPSILON_API_URL and EPSILON_COORDINATOR_CLIENT_SECRET are required)"
            )

        response = self._get_decision(project_id, user_sub, self._get_token())
        if response.status_code == 401:
            # The cached token was revoked or expired early: retry once with a new one
            self._token = None
            response = self._get_decision(project_id, user_sub, self._get_token())

        if response.status_code != 200:
            raise ProjectAccessError(f"the Epsilon API answered HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise ProjectAccessError("the Epsilon API answered with invalid JSON")
        approved = body.get('isApproved') if isinstance(body, dict) else None
        if not isinstance(approved, bool):
            raise ProjectAccessError("the Epsilon API answered without an isApproved decision")

        logger.info(f"[ACCESS] Project {project_id}, user {user_sub}: {'approved' if approved else 'not approved'}")
        return approved

    def _get_decision(self, project_id: str, user_sub: str, token: str) -> requests.Response:
        return self._request(
            requests.get,
            f"{self._config.api_url}/coordinator/{project_id}",
            params={'userId': user_sub},
            headers={'Authorization': f'Bearer {token}'},
        )

    def _get_token(self) -> str:
        """A coordinator-client token, cached until shortly before it expires."""
        if self._token and time.monotonic() < self._token_valid_until:
            return self._token

        response = self._request(
            requests.post,
            f"{self._config.api_url}/coordinator/auth",
            json={'clientId': self._config.client_id, 'clientSecret': self._config.client_secret},
        )
        if response.status_code != 200:
            raise ProjectAccessError(f"the coordinator client could not get a token (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError:
            body = None
        token = body.get('access_token') if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ProjectAccessError("the Epsilon API answered the token request without an access token")
        try:
            expires_in = float(body.get('expires_in') or 0)
        except (TypeError, ValueError):
            expires_in = 0.0

        self._token = token
        self._token_valid_until = time.monotonic() + expires_in - TOKEN_EXPIRY_MARGIN_SECONDS
        return token

    @staticmethod
    def _request(method, url: str, **kwargs) -> requests.Response:
        """One API call; both calls are safe to repeat, so transient failures are retried."""
        for attempt in range(1, API_ATTEMPTS + 1):
            last = attempt == API_ATTEMPTS
            try:
                response = method(url, timeout=API_TIMEOUT_SECONDS, **kwargs)
            except requests.exceptions.Timeout:
                if last:
                    raise ProjectAccessError(f"the Epsilon API did not answer within {API_TIMEOUT_SECONDS}s")
                reason = "timeout"
            except requests.exceptions.SSLError as e:
                raise ProjectAccessError(f"the Epsilon API is unreachable ({type(e).__name__})")
            except requests.exceptions.ConnectionError as e:
                if last:
                    raise ProjectAccessError(f"the Epsilon API is unreachable ({type(e).__name__})")
                reason = type(e).__name__
            except requests.exceptions.RequestException as e:
                raise ProjectAccessError(f"the Epsilon API is unreachable ({type(e).__name__})")
            else:
                if last or response.status_code not in TRANSIENT_STATUS_CODES:
                    return response
                reason = f"HTTP {response.status_code}"
            logger.warning(f"[ACCESS] Epsilon API call failed ({reason}); retrying")
            time.sleep(random.uniform(*RETRY_DELAY_SECONDS))
