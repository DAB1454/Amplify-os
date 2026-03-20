"""Alert notification job — dispatches failure and milestone alerts.

Payload schema:
{
    "alert_type": "publish_failed|approval_needed|milestone|anomaly",
    "tenant_id": "uuid",
    "post_id": "uuid" (optional),
    "message": "Human-readable alert message",
    "severity": "info|warning|critical",
    "metadata": {...}
}
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_alerts(payload: dict) -> dict:
    """Send notifications for important events.

    Dispatches to the appropriate channel based on alert_type and severity:
    - critical: all channels (email, push, in-app)
    - warning: email + in-app
    - info: in-app only
    """
    alert_type = payload.get("alert_type", "unknown")
    severity = payload.get("severity", "info")
    message = payload.get("message", "")
    tenant_id = payload.get("tenant_id", "")
    post_id = payload.get("post_id")
    metadata = payload.get("metadata", {})

    channels_sent: list[str] = []

    # In-app notification (always)
    await _send_in_app(tenant_id, alert_type, message, metadata)
    channels_sent.append("in_app")

    # Email for warning+ severity
    if severity in ("warning", "critical"):
        await _send_email(tenant_id, alert_type, message, metadata)
        channels_sent.append("email")

    # Push for critical only
    if severity == "critical":
        await _send_push(tenant_id, alert_type, message, metadata)
        channels_sent.append("push")

    logger.info(
        "Alert dispatched: type=%s severity=%s tenant=%s post=%s channels=%s",
        alert_type, severity, tenant_id, post_id, channels_sent,
    )

    return {
        "status": "ok",
        "alerts_sent": len(channels_sent),
        "channels": channels_sent,
        "alert_type": alert_type,
        "severity": severity,
    }


async def _send_in_app(tenant_id: str, alert_type: str, message: str, metadata: dict) -> None:
    """Store an in-app notification."""
    # TODO: write to notifications table
    logger.debug("In-app alert for tenant %s: [%s] %s", tenant_id, alert_type, message)


async def _send_email(tenant_id: str, alert_type: str, message: str, metadata: dict) -> None:
    """Send an email alert."""
    # TODO: integrate with email adapter
    logger.debug("Email alert for tenant %s: [%s] %s", tenant_id, alert_type, message)


async def _send_push(tenant_id: str, alert_type: str, message: str, metadata: dict) -> None:
    """Send a push notification."""
    # TODO: integrate with push service
    logger.debug("Push alert for tenant %s: [%s] %s", tenant_id, alert_type, message)
