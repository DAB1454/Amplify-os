"""Notification dispatch with Slack, email, and webhook channel abstractions.

Each channel implements the NotificationChannel protocol.  The dispatcher
fans out a NotificationPayload to all registered channels that match the
event's severity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Payload ──────────────────────────────────────────────────────


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class NotificationPayload:
    """Event data sent to notification channels."""

    event_type: str  # e.g. "approval.created", "approval.approved", "publish.failed"
    severity: Severity = Severity.INFO
    title: str = ""
    body: str = ""
    tenant_id: str = ""
    entity_type: str = ""
    entity_id: str = ""
    actor_id: str = ""
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


# ── Channel protocol ────────────────────────────────────────────


class NotificationChannel(Protocol):
    """Interface for a notification delivery channel."""

    name: str
    min_severity: Severity

    async def send(self, payload: NotificationPayload) -> bool:
        """Deliver the notification.  Return True on success."""
        ...


# ── Slack ────────────────────────────────────────────────────────


class SlackWebhookChannel:
    """Sends notifications to a Slack incoming webhook URL."""

    name = "slack"

    def __init__(
        self,
        webhook_url: str = "",
        min_severity: Severity = Severity.INFO,
    ) -> None:
        self.webhook_url = webhook_url
        self.min_severity = min_severity

    async def send(self, payload: NotificationPayload) -> bool:
        if not self.webhook_url:
            logger.debug("Slack webhook URL not configured, skipping")
            return False

        severity_icon = {
            Severity.INFO: ":information_source:",
            Severity.WARNING: ":warning:",
            Severity.CRITICAL: ":rotating_light:",
        }
        icon = severity_icon.get(payload.severity, "")

        slack_payload = {
            "text": f"{icon} *{payload.title}*",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{icon} *{payload.title}*\n{payload.body}",
                    },
                },
            ],
        }

        if payload.url:
            slack_payload["blocks"].append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View"},
                        "url": payload.url,
                    }
                ],
            })

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.webhook_url, json=slack_payload, timeout=10)
                resp.raise_for_status()
                logger.info("Slack notification sent: %s", payload.title)
                return True
        except Exception:
            logger.exception("Failed to send Slack notification")
            return False


# ── Email ────────────────────────────────────────────────────────


class EmailWebhookChannel:
    """Sends notifications via an email webhook (e.g. SendGrid, Mailgun, Resend)."""

    name = "email"

    def __init__(
        self,
        webhook_url: str = "",
        from_address: str = "notifications@amplify-os.dev",
        min_severity: Severity = Severity.WARNING,
    ) -> None:
        self.webhook_url = webhook_url
        self.from_address = from_address
        self.min_severity = min_severity

    async def send(self, payload: NotificationPayload) -> bool:
        if not self.webhook_url:
            logger.debug("Email webhook URL not configured, skipping")
            return False

        email_payload = {
            "from": self.from_address,
            "subject": f"[{payload.severity.value.upper()}] {payload.title}",
            "body": payload.body,
            "metadata": {
                "event_type": payload.event_type,
                "entity_type": payload.entity_type,
                "entity_id": payload.entity_id,
                "tenant_id": payload.tenant_id,
            },
        }
        if payload.url:
            email_payload["action_url"] = payload.url

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.webhook_url, json=email_payload, timeout=10)
                resp.raise_for_status()
                logger.info("Email notification sent: %s", payload.title)
                return True
        except Exception:
            logger.exception("Failed to send email notification")
            return False


# ── Generic webhook ──────────────────────────────────────────────


class GenericWebhookChannel:
    """POST the notification payload to a custom webhook URL."""

    name = "webhook"

    def __init__(
        self,
        webhook_url: str = "",
        headers: dict[str, str] | None = None,
        min_severity: Severity = Severity.INFO,
    ) -> None:
        self.webhook_url = webhook_url
        self.headers = headers or {}
        self.min_severity = min_severity

    async def send(self, payload: NotificationPayload) -> bool:
        if not self.webhook_url:
            logger.debug("Generic webhook URL not configured, skipping")
            return False

        body = {
            "event_type": payload.event_type,
            "severity": payload.severity.value,
            "title": payload.title,
            "body": payload.body,
            "tenant_id": payload.tenant_id,
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
            "actor_id": payload.actor_id,
            "url": payload.url,
            "metadata": payload.metadata,
            "timestamp": payload.timestamp.isoformat(),
        }

        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.webhook_url,
                    json=body,
                    headers=self.headers,
                    timeout=10,
                )
                resp.raise_for_status()
                logger.info("Webhook notification sent: %s", payload.title)
                return True
        except Exception:
            logger.exception("Failed to send webhook notification")
            return False


# ── In-app (log-based stub) ──────────────────────────────────────


class InAppChannel:
    """In-app notification channel — stores to DB or logs."""

    name = "in_app"

    def __init__(self, min_severity: Severity = Severity.INFO) -> None:
        self.min_severity = min_severity
        self.sent: list[NotificationPayload] = []  # in-memory for testing

    async def send(self, payload: NotificationPayload) -> bool:
        self.sent.append(payload)
        logger.info(
            "In-app notification: [%s] %s — %s",
            payload.severity.value,
            payload.title,
            payload.body,
        )
        return True


# ── Dispatcher ───────────────────────────────────────────────────


SEVERITY_ORDER = [Severity.INFO, Severity.WARNING, Severity.CRITICAL]


class NotificationDispatcher:
    """Fan-out notifications to all registered channels at or above min_severity."""

    def __init__(self) -> None:
        self._channels: list[NotificationChannel] = []

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels.append(channel)

    @property
    def channels(self) -> list[NotificationChannel]:
        return list(self._channels)

    async def dispatch(self, payload: NotificationPayload) -> dict[str, bool]:
        """Send to all channels whose min_severity <= payload severity.

        Returns {channel_name: success_bool}.
        """
        results: dict[str, bool] = {}
        payload_idx = SEVERITY_ORDER.index(payload.severity)

        for channel in self._channels:
            channel_idx = SEVERITY_ORDER.index(channel.min_severity)
            if payload_idx >= channel_idx:
                try:
                    ok = await channel.send(payload)
                    results[channel.name] = ok
                except Exception:
                    logger.exception("Channel %s failed", channel.name)
                    results[channel.name] = False

        return results


def create_default_dispatcher(
    *,
    slack_url: str = "",
    email_url: str = "",
    webhook_url: str = "",
) -> NotificationDispatcher:
    """Create a dispatcher with standard channels."""
    dispatcher = NotificationDispatcher()
    dispatcher.add_channel(InAppChannel())
    if slack_url:
        dispatcher.add_channel(SlackWebhookChannel(webhook_url=slack_url))
    if email_url:
        dispatcher.add_channel(EmailWebhookChannel(webhook_url=email_url))
    if webhook_url:
        dispatcher.add_channel(GenericWebhookChannel(webhook_url=webhook_url))
    return dispatcher
