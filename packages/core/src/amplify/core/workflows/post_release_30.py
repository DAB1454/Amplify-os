"""Post-release 30-day workflow — sustain momentum after launch."""

from __future__ import annotations

from datetime import date, timedelta


class PostRelease30Workflow:
    """Generates a 30-day post-release marketing calendar."""

    phases: list[dict[str, object]] = [
        {
            "week": 1,
            "name": "Momentum",
            "actions": [
                "Share streaming milestones and stats",
                "Repost fan reactions and reviews",
                "Submit to editorial and user-curated playlists",
                "Post alternative visuals (lyric videos, visualizers)",
                "Engage heavily with every comment and share",
            ],
        },
        {
            "week": 2,
            "name": "UGC & Challenges",
            "actions": [
                "Launch a TikTok challenge or trend using the track",
                "Encourage fan-generated content with a hashtag",
                "Share and repost the best UGC",
                "Run a giveaway or contest tied to the release",
            ],
        },
        {
            "week": 3,
            "name": "Remixes & Acoustic",
            "actions": [
                "Release acoustic, remix, or live version",
                "Behind-the-scenes content on the remix/alternate process",
                "Collaborate with creators for reaction or cover videos",
                "Pitch to music blogs and media outlets",
            ],
        },
        {
            "week": 4,
            "name": "Recap & Analytics",
            "actions": [
                "Post 30-day recap with key stats and highlights",
                "Thank fans for their support with a personal message",
                "Analyze performance data and document learnings",
                "Tease what's coming next to maintain interest",
                "Update evergreen playlists and link pages",
            ],
        },
    ]

    @classmethod
    def generate_calendar(
        cls, release_date: date, channels: list[str]
    ) -> list[dict[str, object]]:
        """Generate a 30-day post-release calendar.

        Args:
            release_date: The release date (day 0).
            channels: List of active channel/platform names.

        Returns:
            List of weekly calendar entries.
        """
        calendar: list[dict[str, object]] = []

        for phase in cls.phases:
            week_num = phase["week"]
            week_start = release_date + timedelta(days=(week_num - 1) * 7 + 1)
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
