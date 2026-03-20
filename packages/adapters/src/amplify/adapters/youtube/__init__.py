"""YouTube adapter — Data API v3 integration."""

from amplify.adapters.youtube.adapter import YouTubeAdapter
from amplify.adapters.youtube.auth import YouTubeAuth
from amplify.adapters.youtube.comments import YouTubeComments
from amplify.adapters.youtube.metadata import YouTubeMetadata
from amplify.adapters.youtube.upload import YouTubeUploader

__all__ = [
    "YouTubeAdapter",
    "YouTubeAuth",
    "YouTubeComments",
    "YouTubeMetadata",
    "YouTubeUploader",
]
