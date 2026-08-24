"""Backwards-compat shim — the job queue moved to amplify.core.queue so the
API can enqueue too (the API image doesn't include the worker package). The
worker keeps importing `from app.queue import ...`; new code should import
from amplify.core.queue directly.
"""
from amplify.core.queue import *  # noqa: F401,F403
from amplify.core.queue import (  # noqa: F401
    JobQueue,
    JobEnvelope,
    QUEUE_KEY,
    PROCESSING_KEY,
    JOB_PREFIX,
    DLQ_KEY,
    DEDUP_PREFIX,
    METRICS_KEY,
    DEFAULT_MAX_RETRIES,
    DEFAULT_BACKOFF_BASE,
    DEFAULT_BACKOFF_CAP,
    DEFAULT_JOB_TIMEOUT,
)
