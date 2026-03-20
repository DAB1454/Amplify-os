"""Pre-release workflow — 4-week countdown to release day."""

from __future__ import annotations

from datetime import date, timedelta


class PreReleaseWorkflow:
    """Generates a 4-week pre-release marketing calendar."""

    DURATION_WEEKS = 4

    phases: list[dict[str, object]] = [
        {
            "week": 1,
            "name": "Teaser & Announce",
            "actions": [
                "Post teaser visual/audio snippet",
                "Announce release date across all channels",
                "Update artist bio and links with upcoming release info",
                "Create pre-save/pre-order links",
            ],
        },
        {
            "week": 2,
            "name": "Behind the Scenes",
            "actions": [
                "Share behind-the-scenes studio content",
                "Post about the creative process and inspiration",
                "Launch pre-save link campaign",
                "Engage with fan comments and build anticipation",
            ],
        },
        {
            "week": 3,
            "name": "Single/Preview & Ramp",
            "actions": [
                "Release lead single or preview clip",
                "Ramp up posting frequency",
                "Share fan reactions and early feedback",
                "Collaborate with other artists or influencers for cross-promotion",
            ],
        },
        {
            "week": 4,
            "name": "Final Countdown",
            "actions": [
                "Daily countdown stories and posts",
                "Final pre-save push and reminders",
                "Share track listing or album details",
                "Prepare release-day content queue",
                "Send email blast to mailing list",
            ],
        },
    ]

    @classmethod
    def generate_calendar(
        cls, release_date: date, channels: list[str]
    ) -> list[dict[str, object]]:
        """Generate a week-by-week pre-release calendar.

        Args:
            release_date: The target release date.
            channels: List of active channel/platform names.

        Returns:
            List of calendar entries with dates, actions, and channels.
        """
        calendar: list[dict[str, object]] = []
        campaign_start = release_date - timedelta(weeks=cls.DURATION_WEEKS)

        for phase in cls.phases:
            week_num = phase["week"]
            week_start = campaign_start + timedelta(weeks=week_num - 1)
            week_end = week_start + timedelta(days=6)

            calendar.append(
                {
                    "week": week_num,
                    "name": phase["name"],
                    "start_date": week_start.isoformat(),
                    "end_date": week_end.isoformat(),
                    "channels": channels,
                    "actions": phase["actions"],
                }
            )

        return calendar
