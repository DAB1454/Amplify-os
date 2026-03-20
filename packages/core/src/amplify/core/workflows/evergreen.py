"""Evergreen workflow — recurring activities for ongoing artist promotion."""

from __future__ import annotations


class EvergreenWorkflow:
    """Generates a recurring schedule of evergreen marketing activities."""

    activities: list[dict[str, object]] = [
        {
            "name": "Throwback Post",
            "description": "Share a throwback to a past release, show, or milestone.",
            "content_type": "image_or_video",
        },
        {
            "name": "Lyric Card",
            "description": "Post a visually designed lyric card from the catalog.",
            "content_type": "image",
        },
        {
            "name": "Fan Highlight",
            "description": "Feature a fan cover, reaction, or story.",
            "content_type": "repost",
        },
        {
            "name": "Playlist Pitching",
            "description": "Submit tracks to relevant playlists and curators.",
            "content_type": "outreach",
        },
        {
            "name": "Milestone Celebration",
            "description": "Celebrate streaming milestones, anniversaries, or fan counts.",
            "content_type": "image_or_video",
        },
        {
            "name": "Behind the Scenes",
            "description": "Share studio updates, creative process, or day-in-the-life content.",
            "content_type": "video",
        },
        {
            "name": "Engagement Post",
            "description": "Ask a question, run a poll, or start a conversation with fans.",
            "content_type": "text_or_image",
        },
    ]

    @classmethod
    def generate_schedule(cls, frequency_days: int = 3) -> list[dict[str, object]]:
        """Generate a recurring evergreen content schedule.

        Args:
            frequency_days: Number of days between each activity.

        Returns:
            List of scheduled activities with day offsets.
        """
        schedule: list[dict[str, object]] = []

        for i, activity in enumerate(cls.activities):
            schedule.append(
                {
                    "day_offset": i * frequency_days,
                    "name": activity["name"],
                    "description": activity["description"],
                    "content_type": activity["content_type"],
                    "recurring": True,
                    "frequency_days": frequency_days * len(cls.activities),
                }
            )

        return schedule
