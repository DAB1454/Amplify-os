"""Tests for campaign workflow generators."""

from __future__ import annotations

from datetime import date

from amplify.core.workflows.pre_release import PreReleaseWorkflow
from amplify.core.workflows.release_day import ReleaseDayWorkflow
from amplify.core.workflows.post_release_30 import PostRelease30Workflow
from amplify.core.workflows.evergreen import EvergreenWorkflow


# ── Pre-Release Workflow ─────────────────────────────────────────────


class TestPreReleaseWorkflow:
    def test_generates_four_weeks(self):
        cal = PreReleaseWorkflow.generate_calendar(
            release_date=date(2026, 4, 15),
            channels=["instagram", "tiktok"],
        )
        assert len(cal) == 4
        weeks = [entry["week"] for entry in cal]
        assert weeks == [1, 2, 3, 4]

    def test_weeks_have_correct_names(self):
        cal = PreReleaseWorkflow.generate_calendar(date(2026, 4, 15), ["instagram"])
        names = [entry["name"] for entry in cal]
        assert names == ["Teaser & Announce", "Behind the Scenes", "Single/Preview & Ramp", "Final Countdown"]

    def test_start_date_is_four_weeks_before_release(self):
        release = date(2026, 4, 15)
        cal = PreReleaseWorkflow.generate_calendar(release, ["instagram"])
        # Week 1 starts 4 weeks before release
        assert cal[0]["start_date"] == "2026-03-18"

    def test_channels_propagated(self):
        channels = ["instagram", "tiktok", "youtube"]
        cal = PreReleaseWorkflow.generate_calendar(date(2026, 5, 1), channels)
        for entry in cal:
            assert entry["channels"] == channels

    def test_each_week_has_actions(self):
        cal = PreReleaseWorkflow.generate_calendar(date(2026, 5, 1), ["ig"])
        for entry in cal:
            assert len(entry["actions"]) >= 3

    def test_entry_shape(self):
        cal = PreReleaseWorkflow.generate_calendar(date(2026, 5, 1), ["ig"])
        entry = cal[0]
        assert "week" in entry
        assert "name" in entry
        assert "start_date" in entry
        assert "end_date" in entry
        assert "channels" in entry
        assert "actions" in entry

    def test_duration_weeks_class_attribute(self):
        assert PreReleaseWorkflow.DURATION_WEEKS == 4


# ── Release Day Workflow ─────────────────────────────────────────────


class TestReleaseDayWorkflow:
    def test_generates_ten_timed_actions(self):
        cal = ReleaseDayWorkflow.generate_calendar(date(2026, 4, 15), ["ig"])
        assert len(cal) == 10

    def test_all_on_release_date(self):
        rd = date(2026, 4, 15)
        cal = ReleaseDayWorkflow.generate_calendar(rd, ["ig"])
        for entry in cal:
            assert entry["date"] == "2026-04-15"

    def test_starts_at_0600(self):
        cal = ReleaseDayWorkflow.generate_calendar(date(2026, 4, 15), ["ig"])
        assert cal[0]["time"] == "06:00"

    def test_ends_at_2200(self):
        cal = ReleaseDayWorkflow.generate_calendar(date(2026, 4, 15), ["ig"])
        assert cal[-1]["time"] == "22:00"

    def test_priorities_are_valid(self):
        cal = ReleaseDayWorkflow.generate_calendar(date(2026, 4, 15), ["ig"])
        valid = {"critical", "high", "medium"}
        for entry in cal:
            assert entry["priority"] in valid

    def test_has_critical_actions(self):
        cal = ReleaseDayWorkflow.generate_calendar(date(2026, 4, 15), ["ig"])
        critical = [e for e in cal if e["priority"] == "critical"]
        assert len(critical) >= 2

    def test_channels_on_every_entry(self):
        channels = ["ig", "tt"]
        cal = ReleaseDayWorkflow.generate_calendar(date(2026, 4, 15), channels)
        for entry in cal:
            assert entry["channels"] == channels


# ── Post-Release 30-Day Workflow ─────────────────────────────────────


class TestPostRelease30Workflow:
    def test_generates_four_weeks(self):
        cal = PostRelease30Workflow.generate_calendar(date(2026, 4, 15), ["ig"])
        assert len(cal) == 4

    def test_starts_after_release_date(self):
        release = date(2026, 4, 15)
        cal = PostRelease30Workflow.generate_calendar(release, ["ig"])
        # Week 1 starts the day after release
        assert cal[0]["start_date"] == "2026-04-16"

    def test_phase_names(self):
        cal = PostRelease30Workflow.generate_calendar(date(2026, 4, 15), ["ig"])
        names = [e["name"] for e in cal]
        assert names == ["Momentum", "UGC & Challenges", "Remixes & Acoustic", "Recap & Analytics"]

    def test_each_phase_has_actions(self):
        cal = PostRelease30Workflow.generate_calendar(date(2026, 4, 15), ["ig"])
        for entry in cal:
            assert len(entry["actions"]) >= 4

    def test_weeks_are_sequential(self):
        cal = PostRelease30Workflow.generate_calendar(date(2026, 4, 15), ["ig"])
        for i, entry in enumerate(cal):
            assert entry["week"] == i + 1


# ── Evergreen Workflow ───────────────────────────────────────────────


class TestEvergreenWorkflow:
    def test_generates_seven_activities(self):
        schedule = EvergreenWorkflow.generate_schedule()
        assert len(schedule) == 7

    def test_default_frequency_is_three_days(self):
        schedule = EvergreenWorkflow.generate_schedule()
        offsets = [s["day_offset"] for s in schedule]
        assert offsets == [0, 3, 6, 9, 12, 15, 18]

    def test_custom_frequency(self):
        schedule = EvergreenWorkflow.generate_schedule(frequency_days=7)
        offsets = [s["day_offset"] for s in schedule]
        assert offsets == [0, 7, 14, 21, 28, 35, 42]

    def test_all_marked_recurring(self):
        schedule = EvergreenWorkflow.generate_schedule()
        for s in schedule:
            assert s["recurring"] is True

    def test_cycle_period_covers_all_activities(self):
        schedule = EvergreenWorkflow.generate_schedule(frequency_days=3)
        # Full cycle = frequency * num_activities
        for s in schedule:
            assert s["frequency_days"] == 3 * 7  # 21 days

    def test_each_has_name_and_description(self):
        schedule = EvergreenWorkflow.generate_schedule()
        for s in schedule:
            assert "name" in s
            assert "description" in s
            assert "content_type" in s
            assert len(s["name"]) > 0

    def test_activity_names(self):
        schedule = EvergreenWorkflow.generate_schedule()
        names = [s["name"] for s in schedule]
        assert "Throwback Post" in names
        assert "Lyric Card" in names
        assert "Engagement Post" in names
