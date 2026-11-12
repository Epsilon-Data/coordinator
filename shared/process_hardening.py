"""Keep a worker's memory and environment away from other processes of the same user.

The AI review runs submitted code as a child process under the worker's own
user. On Linux a process can normally read /proc/<pid>/environ and
/proc/<pid>/mem of any process of the same user, so the child could read the
worker's secrets (DATABASE_URL, API keys) from its parent. Marking the worker
"not dumpable" makes those files unreadable to it and refuses ptrace.
"""
import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3


def deny_same_user_inspection() -> bool:
    """Mark this process not dumpable. Returns whether it now is (always False off Linux)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.error(f"prctl(PR_SET_DUMPABLE, 0) failed (errno {ctypes.get_errno()})")
            return False
        return libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError) as e:
        logger.error(f"Could not mark the process not dumpable: {type(e).__name__}")
        return False
