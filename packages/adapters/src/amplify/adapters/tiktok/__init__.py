"""TikTok adapter — Content Posting API integration."""

from amplify.adapters.tiktok.adapter import TikTokAdapter
from amplify.adapters.tiktok.analytics import TikTokAnalytics
from amplify.adapters.tiktok.auth import TikTokAuth
from amplify.adapters.tiktok.drafts import TikTokDrafts
from amplify.adapters.tiktok.publish import TikTokPublisher

__all__ = [
    "TikTokAdapter",
    "TikTokAnalytics",
    "TikTokAuth",
    "TikTokDrafts",
    "TikTokPublisher",
]
