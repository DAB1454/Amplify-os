"""Release day workflow — hour-by-hour checklist for launch day."""

from __future__ import annotations

from datetime import date


class ReleaseDayWorkflow:
    """Generates a release-day action checklist and calendar."""

    checklist: list[dict[str, object]] = [
        {
            "time": "06:00",
            "action": "Verify release is live on all streaming platforms",
            "priority": "critical",
        },
        {
            "time": "08:00",
            "action": "Post morning announcement with streaming links",
            "priority": "critical",
        },
        {
            "time": "09:00",
            "action": "Share Instagram/TikTok story with link sticker",
            "priority": "high",
        },
        {
            "time": "10:00",
            "action": "Send email blast to mailing list",
            "priority": "critical",
        },
        {
            "time": "12:00",
            "action": "Midday engagement — reply to comments, share fan posts",
            "priority": "high",
        },
        {
            "time": "14:00",
            "action": "Post behind-the-scenes or making-of content",
            "priority": "medium",
        },
        {
            "time": "16:00",
            "action": "Share stories throughout the day — reactions, milestones",
            "priority": "high",
        },
        {
            "time": "18:00",
            "action": "Evening push — reminder post for different time zones",
            "priority": "medium",
        },
        {
            "time": "20:00",
            "action": "Thank-you post to fans and collaborators",
            "priority": "high",
        },
        {
            "time": "22:00",
            "action": "End-of-day recap story with first-day stats",
            "priority": "medium",
        },
    ]

    @classmethod
    def generate_calendar(
        cls, release_date: date, channels: list[str]
    ) -> list[dict[str, object]]:
        """Generate a release-day schedule.

        Args:
            release_date: The release date.
            channels: List of active channel/platform names.

        Returns:
            List of timed actions for release day.
        """
        calendar: list[dict[str, object]] = []

        for item in cls.checklist:
            calendar.append(
                {
                    "date": release_date.isoformat(),
                    "time": item["time"],
                    "action": item["action"],
                    "priority": item["priority"],
                    "channels": channels,
                }
            )

        return calendar
