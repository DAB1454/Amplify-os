"""Notification dispatch — Slack, email, and custom webhook abstractions."""

from amplify.core.notifications.dispatcher import (
    NotificationChannel,
    NotificationDispatcher,
    NotificationPayload,
    SlackWebhookChannel,
    EmailWebhookChannel,
    GenericWebhookChannel,
    InAppChannel,
    create_default_dispatcher,
)

__all__ = [
    "NotificationChannel",
    "NotificationDispatcher",
    "NotificationPayload",
    "SlackWebhookChannel",
    "EmailWebhookChannel",
    "GenericWebhookChannel",
    "InAppChannel",
    "create_default_dispatcher",
]
