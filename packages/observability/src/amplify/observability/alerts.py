"""Alert manager stub for Amplify-OS.

TODO: Integrate with Slack webhooks, PagerDuty, and email (SES/SendGrid).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger("alerts")

SEVERITY_LEVELS = ("info", "warning", "critical")


class AlertManager:
    """Routes alerts to the appropriate notification channel.

    Currently a stub that logs alerts.  Future implementations will
    dispatch to Slack, PagerDuty, or email based on severity and channel.
    """

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
            channel: Notification channel (e.g. ``"slack"``, ``"pagerduty"``,
                ``"email"``).  Defaults to ``"default"`` which logs only.
        """
        if severity not in SEVERITY_LEVELS:
            raise ValueError(
                f"Invalid severity '{severity}'. Must be one of {SEVERITY_LEVELS}"
            )

        log = logger.bind(
            severity=severity,
            title=title,
            channel=channel,
        )

        # TODO: dispatch to real backends based on channel/severity
        if severity == "critical":
            log.critical("alert.fired", message=message)
        elif severity == "warning":
            log.warning("alert.fired", message=message)
        else:
            log.info("alert.fired", message=message)
