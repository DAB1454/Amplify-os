"""Alert manager for Amplify-OS.

Dispatches alerts to Slack webhooks (when configured) and always logs.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger("alerts")

SEVERITY_LEVELS = ("info", "warning", "critical")

_SEVERITY_EMOJI = {
    "info": "\u2139\ufe0f",
    "warning": "\u26a0\ufe0f",
    "critical": "\U0001f6a8",
}


class AlertManager:
    """Routes alerts to Slack and/or logs.

    Pass ``slack_webhook_url`` at init to enable Slack delivery for
    warning and critical alerts.
    """

    def __init__(self, slack_webhook_url: str = "") -> None:
        self._slack_url = slack_webhook_url

    async def send_alert(
        self,
        severity: str,
        title: str,
        message: str,
        channel: str = "default",
    ) -> None:
        """Send an alert notification.

        Args:
            severity: One of ``info``, ``warning``, ``critical``.
            title: Short summary of the alert.
            message: Detailed description.
            channel: Notification channel hint (currently unused for routing).
        """
        if severity not in SEVERITY_LEVELS:
            raise ValueError(
                f"Invalid severity '{severity}'. Must be one of {SEVERITY_LEVELS}"
            )

        log = logger.bind(severity=severity, title=title, channel=channel)

        if severity == "critical":
            log.critical("alert.fired", message=message)
        elif severity == "warning":
            log.warning("alert.fired", message=message)
        else:
            log.info("alert.fired", message=message)

        # Dispatch to Slack for warning+ severity
        if self._slack_url and severity in ("warning", "critical"):
            await self._send_slack(severity, title, message)

    async def _send_slack(self, severity: str, title: str, message: str) -> None:
        """Post a message to a Slack incoming webhook."""
        import httpx

        emoji = _SEVERITY_EMOJI.get(severity, "")
        payload = {
            "text": f"{emoji} *[{severity.upper()}] {title}*\n{message}",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self._slack_url, json=payload)
                if resp.status_code != 200:
                    logger.warning(
                        "slack.send_failed",
                        status=resp.status_code,
                        body=resp.text[:200],
                    )
        except Exception as exc:
            logger.warning("slack.send_error", error=str(exc))
