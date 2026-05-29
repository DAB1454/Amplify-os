"""Content generation pipeline — shared between the API and the worker.

This module composes the ContentAgent with the asset/clip libraries and
the caption validator. Both `apps/api` (synchronous endpoint, repair sweeps)
and `apps/worker` (autopilot, generate_content job) import from here so
the pipeline behaves identically regardless of entry point.
"""

from amplify.agents.pipeline.content import (
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
from amplify.agents.pipeline.clips import _find_clip_for_post

__all__ = [
    "generate_content_for_post",
    "generate_content_for_posts",
    "_caption_mentions_other_tracks",
    "_normalize_for_match",
    "_extract_track_reference",
    "_any_phrase_match",
    "_find_matching_assets",
    "_desired_media_count",
    "_generate_caption_validated",
    "_generate_caption",
    "_strip_hashtags_and_links",
    "_build_track_name_map",
    "_find_clip_for_post",
]
