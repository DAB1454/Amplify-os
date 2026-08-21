"""Backwards-compat shim — video budget moved to amplify.media.video_budget."""
from amplify.media.video_budget import *  # noqa: F401,F403
from amplify.media.video_budget import (  # noqa: F401
    can_generate_ai_video,
    record_spend,
    get_video_settings,
    update_video_settings,
)
