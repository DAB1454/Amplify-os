"""Backwards-compat shim — the real content pipeline lives in
`amplify.agents.pipeline.content` so the worker can use it too.

Existing `from app.services.content_pipeline import ...` callers keep
working through this re-export. New code should import from
`amplify.agents.pipeline` directly.
"""

from amplify.agents.pipeline.content import (  # noqa: F401
    generate_content_for_post,
    generate_content_for_posts,
    _caption_mentions_other_tracks,
    _normalize_for_match,
    _extract_track_reference,
    _any_phrase_match,
    _find_matching_assets,
    _desired_media_count,
    _generate_caption_validated,
    _generate_caption,
    _strip_hashtags_and_links,
    _build_track_name_map,
)
