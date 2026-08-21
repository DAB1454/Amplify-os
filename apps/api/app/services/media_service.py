"""Backwards-compat shim — MediaService moved to amplify.media.storage
so the worker can upload media too. New code should import from there."""
from amplify.media.storage import *  # noqa: F401,F403
from amplify.media.storage import (  # noqa: F401
    MediaService,
    UPLOAD_DIR,
    MAX_FILE_SIZE,
    ALLOWED_TYPES,
)
