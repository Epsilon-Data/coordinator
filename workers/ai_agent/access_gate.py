"""Project access check made before the AI review runs any of a job's code.

The AI review executes the submitted script on the coordinator (with dummy
data) to look at what it prints. That must not happen for a submitter who is
not approved for the job's project: the same check the executor makes before
real data is requested is made here first, against the same Epsilon API.

The review runs only on an explicit approval. A refusal, an unreadable build
or an answer that could not be obtained all mean no code is executed.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from shared.db import job_repository
from workers.executor.access_client import ProjectAccessClient
from workers.executor.constants import EnclaveBackend
from workers.executor.exceptions import BuildValidationError, ProjectAccessError
from workers.executor.factories import resolve_enclave_backend
from workers.executor.services import BuildValidator
from workers.executor.settings import EnclaveConfig, ProjectAccessConfig
from workers.executor.utils import canonical_uuid

logger = logging.getLogger(__name__)


class ReviewAccess(str, Enum):
    ALLOWED = "allowed"
    # The submitter may not analyse the project, or the job cannot run at all: the executor rejects it too
    REFUSED = "refused"
    # No decision could be obtained right now; the review is tried again on a later poll
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class ReviewAccessDecision:
    status: ReviewAccess
    reason: str

    @property
    def allowed(self) -> bool:
        return self.status == ReviewAccess.ALLOWED


class ReviewAccessGate:
    """Decides whether the AI review may execute a job's code."""

    def __init__(
        self, access_client: Optional[ProjectAccessClient], check_disabled: bool = False, configured: bool = True,
    ):
        self._access_client = access_client
        self._check_disabled = check_disabled
        self._configured = configured

    @classmethod
    def from_environment(cls) -> "ReviewAccessGate":
        """Gate configured like the executor's check, from the same environment variables."""
        access_config = ProjectAccessConfig()
        # PROJECT_ACCESS_CHECK=disabled is honoured only with the local enclave (development)
        check_disabled = False
        if access_config.check == 'disabled':
            if resolve_enclave_backend(EnclaveConfig()) == EnclaveBackend.LOCAL:
                check_disabled = True
                logger.warning("AI review: project access check disabled (PROJECT_ACCESS_CHECK=disabled, local enclave)")
            else:
                logger.warning(
                    "AI review: PROJECT_ACCESS_CHECK=disabled ignored: it is honoured only when the resolved "
                    "enclave backend is local"
                )
        if not check_disabled and not access_config.is_configured:
            logger.warning(
                "AI review: the project access check is not configured (EPSILON_API_URL and "
                "EPSILON_COORDINATOR_CLIENT_SECRET): no job's code is executed for review until it is"
            )
        return cls(
            ProjectAccessClient(access_config), check_disabled=check_disabled, configured=access_config.is_configured,
        )

    def decide(self, job_id: str, repo_path: str) -> ReviewAccessDecision:
        """Whether job_id's submitter is approved for the project its build.yml names."""
        if self._check_disabled:
            return ReviewAccessDecision(ReviewAccess.ALLOWED, "project access check disabled (local enclave)")

        # An operator's omission, not a transient failure: recorded, not retried on every poll
        if not self._configured or self._access_client is None:
            return ReviewAccessDecision(ReviewAccess.REFUSED, "the project access check is not configured")

        try:
            project_id = BuildValidator(repo_path).validate().datasets[0].dataset_id
        except (BuildValidationError, IndexError) as e:
            return ReviewAccessDecision(ReviewAccess.REFUSED, f"the job's build is not valid ({type(e).__name__})")

        try:
            submitter_sub = canonical_uuid(job_repository.get_submitter_sub(job_id))
        except Exception as e:
            return ReviewAccessDecision(ReviewAccess.UNDECIDED, f"the job's submitter could not be read ({type(e).__name__})")
        if submitter_sub is None:
            return ReviewAccessDecision(ReviewAccess.REFUSED, "the submitter is not linked to a platform identity")

        try:
            approved = self._access_client.check_project_access(project_id, submitter_sub)
        except ProjectAccessError as e:
            logger.warning(f"[ACCESS] AI review of job {job_id}, project {project_id}: access could not be checked: {e}")
            return ReviewAccessDecision(ReviewAccess.UNDECIDED, f"access to project {project_id} could not be checked")
        if not approved:
            return ReviewAccessDecision(ReviewAccess.REFUSED, f"the submitter is not approved for project {project_id}")
        return ReviewAccessDecision(ReviewAccess.ALLOWED, f"the submitter is approved for project {project_id}")
