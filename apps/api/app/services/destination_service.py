"""Re-export shim — destination_service moved to amplify.core.services.

The implementation lives in ``amplify.core.services.destination_service``
so the worker (which has no access to the API ``app`` package) can
import it too. This shim keeps existing API callsites working without
edits.
"""

from amplify.core.services.destination_service import *  # noqa: F401,F403
from amplify.core.services.destination_service import (  # noqa: F401
    cta_for,
    inject_caption_cta,
)
