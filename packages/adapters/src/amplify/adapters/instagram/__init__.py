"""Instagram adapter — Graph API integration."""

from amplify.adapters.instagram.adapter import InstagramAdapter
from amplify.adapters.instagram.auth import InstagramAuth
from amplify.adapters.instagram.comments import InstagramComments
from amplify.adapters.instagram.insights import InstagramInsights
from amplify.adapters.instagram.publish import InstagramPublisher

__all__ = [
    "InstagramAdapter",
    "InstagramAuth",
    "InstagramComments",
    "InstagramInsights",
    "InstagramPublisher",
]
