"""Alert notification job — dispatches failure and milestone alerts.

Payload schema:
{
    "alert_type": "publish_failed|approval_needed|milestone|anomaly|token_expired|campaign_complete|content_ready",
    "tenant_id": "uuid",
    "post_id": "uuid" (optional),
    "channel_id": "uuid" (optional),
    "campaign_id": "uuid" (optional),
    "message": "Human-readable alert message",
    "severity": "info|warning|critical",
    "metadata": {...}
}
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

# Map alert_type → (event_type, default title, default url, default severity)
_ALERT_DEFAULTS: dict[str, tuple[str, str, str, str]] = {
    "publish_failed": (
        "post.publish_failed",
        "Post failed to publish",
        "/posts",
        "error",
    ),
    "token_expired": (
        "channel.token_expired",
        "Channel needs reconnection",
        "/channels",
        "warning",
    ),
    "approval_needed": (
        "approval.needed",
        "Posts ready for review",
        "/approvals",
        "info",
    ),
    "campaign_complete": (
        "campaign.complete",
        "Campaign completed",
        "/campaigns",
        "success",
    ),
    "content_ready": (
        "content.ready",
        "AI content ready for review",
        "/campaigns",
        "info",
    ),
    "milestone": (
        "analytics.milestone",
        "Milestone reached",
        "/analytics",
        "success",
    ),
    "anomaly": (
        "analytics.anomaly",
        "Unusual activity detected",
        "/analytics",
        "warning",
    ),
}


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

    if not tenant_id:
        return {"status": "error", "error": "tenant_id required"}

    channels_sent: list[str] = []

    # In-app notification (always)
    try:
        await _send_in_app(tenant_id, alert_type, message, severity, metadata)
        channels_sent.append("in_app")
    except Exception as exc:
        logger.warning("In-app alert failed for tenant %s: %s", tenant_id, exc)

    # Email for warning+ severity
    if severity in ("warning", "critical"):
        try:
            await _send_email(tenant_id, alert_type, message, metadata)
            channels_sent.append("email")
        except Exception as exc:
            logger.warning("Email alert failed for tenant %s: %s", tenant_id, exc)

    # Push for critical only
    if severity == "critical":
        try:
            await _send_push(tenant_id, alert_type, message, metadata)
            channels_sent.append("push")
        except Exception as exc:
            logger.warning("Push alert failed for tenant %s: %s", tenant_id, exc)

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


async def _send_in_app(
    tenant_id: str,
    alert_type: str,
    message: str,
    severity: str,
    metadata: dict,
) -> None:
    """Write an in-app notification to the notifications table.

    Fans out to all tenant members using NotificationService.notify_tenant().
    """
    from amplify.db.session import get_async_session
    from amplify.core.services.notification_service import NotificationService
    from app.config import Settings

    settings = Settings()
    tid = uuid.UUID(tenant_id)

    # Resolve defaults from alert type
    defaults = _ALERT_DEFAULTS.get(alert_type, ("alert." + alert_type, alert_type.replace("_", " ").title(), "/", "info"))
    event_type, default_title, default_url, default_severity = defaults

    title = metadata.get("title", default_title)
    url = metadata.get("url", default_url)
    effective_severity = severity if severity in ("info", "success", "warning", "error") else default_severity

    # Restrict critical alerts to owner/admin roles
    roles = ("owner", "admin") if effective_severity in ("error", "warning") else None

    # Extract entity context if available
    entity_type = metadata.get("entity_type")
    entity_id = None
    if metadata.get("entity_id"):
        try:
            entity_id = uuid.UUID(str(metadata["entity_id"]))
        except (ValueError, TypeError):
            pass

    async with get_async_session(settings.database_url) as db:
        service = NotificationService(db, tid)
        rows = await service.notify_tenant(
            event_type=event_type,
            title=title,
            body=message,
            severity=effective_severity,
            url=url,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=metadata,
            roles=roles,
        )
        logger.info(
            "In-app alert sent to %d user(s) for tenant %s: [%s] %s",
            len(rows), tenant_id, alert_type, title,
        )


async def _send_email(
    tenant_id: str, alert_type: str, message: str, metadata: dict
) -> None:
    """Send an email alert via Resend to all tenant owner/admin members."""
    from amplify.db.session import get_async_session
    from amplify.db.models.membership import MembershipModel
    from amplify.db.models.user import UserModel
    from app.config import Settings

    settings = Settings()
    if not settings.resend_api_key:
        logger.debug("AMPLIFY_RESEND_API_KEY not set — skipping email alert")
        return

    defaults = _ALERT_DEFAULTS.get(alert_type, ("alert." + alert_type, alert_type.replace("_", " ").title(), "/", "info"))
    _, default_title, default_url, _ = defaults
    title = metadata.get("title", default_title)

    # Look up owner/admin email addresses for this tenant
    tid = uuid.UUID(tenant_id)
    recipients: list[str] = []

    async with get_async_session(settings.database_url) as db:
        from sqlalchemy import select as sa_select
        stmt = (
            sa_select(UserModel.email)
            .join(MembershipModel, MembershipModel.user_id == UserModel.id)
            .where(
                MembershipModel.tenant_id == tid,
                MembershipModel.role.in_(("owner", "admin")),
            )
        )
        result = await db.execute(stmt)
        recipients = [row[0] for row in result.all() if row[0]]

    if not recipients:
        logger.info("No owner/admin emails found for tenant %s — skipping email", tenant_id)
        return

    # Send via Resend API
    import httpx
    email_body = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 560px; margin: 0 auto;">
  <h2 style="color: #1a1a1a;">{title}</h2>
  <p style="color: #555; line-height: 1.6;">{message}</p>
  <p style="margin-top: 24px;">
    <a href="https://app.amplifyme.dev{default_url}"
       style="background: #6366F1; color: white; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: 500;">
      View in AmplifyMe
    </a>
  </p>
  <p style="color: #999; font-size: 12px; margin-top: 32px;">
    You're receiving this because you're an admin of this workspace.
  </p>
</div>
""".strip()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.resend_from_address,
                    "to": recipients,
                    "subject": f"[AmplifyMe] {title}",
                    "html": email_body,
                },
                timeout=15,
            )
            resp.raise_for_status()
            logger.info("Email alert sent to %d recipient(s) for tenant %s: %s",
                        len(recipients), tenant_id, title)
    except Exception:
        logger.exception("Failed to send email alert for tenant %s", tenant_id)


async def _send_push(
    tenant_id: str, alert_type: str, message: str, metadata: dict
) -> None:
    """Send a push notification.

    Currently logs the intent. Push notifications are planned for
    the mobile app phase.
    """
    # TODO: Phase F — integrate with FCM/APNS when mobile app ships
    logger.info(
        "Push alert queued for tenant %s: [%s] %s (push adapter not yet wired)",
        tenant_id, alert_type, message[:200],
    )
