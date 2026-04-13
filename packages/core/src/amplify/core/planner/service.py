"""Headless campaign planner service.

Lifted out of ``apps/api/app/routes/ai.py::generate_plan`` so the
worker (``automation_tick``) can call the same code path the user
hits when they click "Generate plan" in the UI. The HTTP route now
just hands off to ``plan_campaign`` and serializes the result.

Why this lives in ``amplify.core``: the worker container does NOT
ship the API package, so anything the worker needs has to be in a
shared package. The four helpers this module pulls in
(``destination_service``, ``goal_mix``, ``prior_metrics``,
``bandit_service``) were moved to ``amplify.core.services`` for the
same reason.

Determinism boundary: this function reads from the DB, calls the
LLM via the injected runner, and writes calendar items + draft
posts. It does NOT log to the human audit log — that's still the
caller's job (the route does it; the worker writes its own
``automation_audits`` row instead).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.artist import ArtistModel
from amplify.db.models.asset import AssetModel
from amplify.db.models.calendar_item import CalendarItemModel
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.post import PostModel
from amplify.db.models.release import ReleaseModel
from amplify.db.models.track import TrackModel

logger = logging.getLogger(__name__)


# Local times the planner staggers posts across — must match the route's
# original list so plans generated via the worker look identical to plans
# generated via the UI.
_POST_TIMES_LOCAL = [
    time(9, 0),
    time(10, 30),
    time(12, 0),
    time(14, 0),
    time(16, 30),
    time(18, 0),
    time(19, 30),
    time(21, 0),
]


@dataclass
class PlanOverrides:
    """Optional caller-supplied overrides for one plan generation.

    All fields fall back to values stored on the campaign, the release,
    or sensible defaults. The worker (which has no user input) typically
    leaves these all unset; the HTTP route forwards what the user passed
    in the request body.
    """

    channels: list[str] | None = None
    posts_per_day: int | None = None
    focus: str | None = None
    content_notes: str | None = None
    genre: str | None = None
    track_listing: list[str] | None = None
    destination_urls: dict[str, str] | None = None
    budget: float | None = None
    timezone: str = "America/New_York"
    youtube_strategy: str | None = None
    goal_mix: dict[str, float] | None = None


@dataclass
class PlanGenerationResult:
    """What the headless planner returns to its caller."""

    campaign_name: str
    plan_start: str
    plan_end: str
    daily_actions: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    calendar_items_created: int = 0
    draft_posts_created: int = 0
    model: str = ""
    elapsed_ms: int = 0
    goal_mix: dict[str, float] = field(default_factory=dict)


def _build_default_runner():
    """Default AgentRunner factory — only imported when no runner is passed."""
    from amplify.agents.runtime.agent_runner import AgentRunner
    from amplify.agents.runtime.config import AgentConfig

    return AgentRunner(config=AgentConfig())


async def plan_campaign(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    overrides: PlanOverrides | None = None,
    runner: Any | None = None,
) -> PlanGenerationResult:
    """Generate an AI plan for ``campaign_id`` and persist its artifacts.

    Side effects:
      - Inserts ``CalendarItemModel`` rows (one per daily action).
      - Inserts ``PostModel`` draft rows (one per content action).
      - Mutates ``campaign.config['goal_mix']`` with the resolved mix.
      - Calls ``log_decision`` on the bandit service in shadow mode.

    Does NOT commit — the caller owns the session lifecycle.
    """
    overrides = overrides or PlanOverrides()

    # Lazy imports keep startup time tight and let the worker boot
    # without paying the agents-package import cost when it's not
    # actually planning. Same pattern the route used.
    from amplify.agents.subagents.planner_agent import (
        DailyAction,
        PlannerAgent,
        PlannerOutput,
    )
    from amplify.core.services.destination_service import (
        cta_for,
        inject_caption_cta,
    )
    from amplify.core.services.goal_mix import (
        default_goal_mix,
        normalize_mix,
        rebalance_goals,
    )
    from amplify.core.services.prior_metrics import (
        build_prior_metrics_for_planner,
    )

    # ── Load campaign ──────────────────────────────────────────
    campaign = (
        await db.execute(
            select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise ValueError(f"campaign {campaign_id} not found for tenant {tenant_id}")

    campaign_mode = getattr(campaign, "mode", "manual") or "manual"
    campaign_config = campaign.config or {}

    # Merge campaign config into overrides — keep caller intent if set,
    # otherwise fall back to what was saved on the campaign.
    if not overrides.channels and campaign_config.get("channels"):
        overrides.channels = campaign_config["channels"]
    if not overrides.posts_per_day and campaign_config.get("posts_per_day"):
        overrides.posts_per_day = campaign_config["posts_per_day"]
    if not overrides.focus and campaign_config.get("focus"):
        overrides.focus = campaign_config["focus"]
    if not overrides.content_notes and campaign_config.get("content_notes"):
        overrides.content_notes = campaign_config["content_notes"]
    if not overrides.genre and campaign_config.get("genre"):
        overrides.genre = campaign_config["genre"]

    # ── Artist + release + tracks ──────────────────────────────
    artist = None
    if campaign.artist_id:
        artist = (
            await db.execute(
                select(ArtistModel).where(ArtistModel.id == campaign.artist_id)
            )
        ).scalar_one_or_none()
    artist_name = artist.name if artist else "Unknown Artist"

    release = None
    release_title = ""
    release_date = ""
    track_listing = overrides.track_listing
    tracks_by_title: dict[str, TrackModel] = {}

    if campaign.release_id:
        release = (
            await db.execute(
                select(ReleaseModel).where(ReleaseModel.id == campaign.release_id)
            )
        ).scalar_one_or_none()
        if release:
            release_title = release.title or ""
            release_date = str(release.release_date) if release.release_date else ""
            tracks = list(
                (
                    await db.execute(
                        select(TrackModel)
                        .where(TrackModel.release_id == release.id)
                        .order_by(TrackModel.track_number)
                    )
                ).scalars().all()
            )
            tracks_by_title = {
                (t.title or "").strip().lower(): t for t in tracks if t.title
            }
            if not track_listing:
                track_listing = [t.title for t in tracks if t.title]

            if not overrides.destination_urls:
                dest: dict[str, str] = {}
                if release.apple_music_url:
                    dest["apple_music"] = release.apple_music_url
                if release.spotify_url:
                    dest["spotify"] = release.spotify_url
                if release.linktree_url:
                    dest["linktree"] = release.linktree_url
                if release.bandcamp_url:
                    dest["bandcamp"] = release.bandcamp_url
                if release.hyperfollow_url:
                    dest["hyperfollow"] = release.hyperfollow_url
                if release.youtube_url:
                    dest["youtube"] = release.youtube_url
                if release.tiktok_url:
                    dest["tiktok"] = release.tiktok_url
                if release.instagram_url:
                    dest["instagram"] = release.instagram_url
                if dest:
                    overrides.destination_urls = dest
                    logger.info(
                        "Auto-populated destination_urls from release: %s",
                        list(dest.keys()),
                    )

    # ── Channels — caller override → connected platforms → IG ──
    channels = overrides.channels or []
    if not channels:
        ch_rows = (
            await db.execute(
                select(ChannelConnectionModel.platform)
                .where(
                    ChannelConnectionModel.tenant_id == tenant_id,
                    ChannelConnectionModel.is_active == True,  # noqa: E712
                )
                .distinct()
            )
        ).all()
        channels = [r[0] for r in ch_rows]
    if not channels:
        channels = ["instagram"]

    # ── Asset summary so the planner knows what's possible ────
    available_assets: list[dict[str, str]] = []
    asset_type_summary: dict[str, int] = {}
    try:
        asset_q = select(AssetModel).where(AssetModel.tenant_id == tenant_id)
        asset_q = asset_q.where(
            or_(
                AssetModel.approval_status != "rejected",
                AssetModel.approval_status.is_(None),
            )
        )
        if campaign.artist_id:
            asset_q = asset_q.where(
                or_(
                    AssetModel.artist_id == campaign.artist_id,
                    AssetModel.release_id == campaign.release_id,
                    AssetModel.campaign_id == campaign.id,
                    AssetModel.artist_id.is_(None),
                )
            )
        asset_q = asset_q.order_by(AssetModel.created_at.desc()).limit(50)
        for a in (await db.execute(asset_q)).scalars().all():
            available_assets.append(
                {
                    "name": a.name,
                    "asset_type": a.asset_type,
                    "tags": ", ".join(a.tags) if a.tags else "",
                    "mime_type": a.mime_type or "",
                }
            )
            asset_type_summary[a.asset_type] = (
                asset_type_summary.get(a.asset_type, 0) + 1
            )
        logger.info(
            "Planner context: %d available assets — %s",
            len(available_assets),
            ", ".join(f"{v} {k}" for k, v in asset_type_summary.items()),
        )
    except Exception as exc:
        logger.warning("Failed to load assets for planner context: %s", exc)

    has_video = (
        asset_type_summary.get("video", 0)
        + asset_type_summary.get("lyric_video", 0)
        > 0
    )
    has_audio = asset_type_summary.get("audio", 0) > 0
    has_images = sum(
        asset_type_summary.get(t, 0) for t in ("image", "album_art", "promo_photo")
    ) > 0

    auto_notes: list[str] = []
    if not has_video:
        auto_notes.append(
            "NO VIDEO FILES in library. Do NOT plan video content (reels, "
            "shorts, stories with video). Use image posts with audio "
            "snippets instead."
        )
    if has_audio:
        auto_notes.append(
            f"HAS {asset_type_summary.get('audio', 0)} AUDIO TRACKS. "
            "For TikTok and YouTube, the system will auto-generate "
            "15-second videos (image + audio clip) from the artist's "
            "tracks. Plan TikTok/YouTube posts that feature specific "
            "tracks — each post will use a different track excerpt."
        )
    if has_images:
        img_count = sum(
            asset_type_summary.get(t, 0)
            for t in ("image", "album_art", "promo_photo")
        )
        auto_notes.append(
            f"HAS {img_count} IMAGES (album art, promo photos). "
            "Can do carousel posts on Instagram."
        )
    if overrides.posts_per_day and overrides.posts_per_day > 0:
        auto_notes.append(
            f"TARGET {overrides.posts_per_day} POST(S) PER CHANNEL PER DAY. "
            "Spread them across the day at different times."
        )
    if overrides.focus:
        focus_map = {
            "engagement": "Focus on ENGAGEMENT — interactive content, questions, polls, reply-bait, share-worthy posts.",
            "awareness": "Focus on AWARENESS — maximize reach, use trending formats, hashtag strategy, shareable content.",
            "conversions": "Focus on CONVERSIONS — strong CTAs, link in bio reminders, pre-save pushes, streaming link drops.",
            "community": "Focus on COMMUNITY BUILDING — behind-the-scenes, personal stories, fan interaction, user-generated content.",
        }
        auto_notes.append(
            focus_map.get(overrides.focus, f"Campaign focus: {overrides.focus}")
        )

    if auto_notes:
        combined_notes = "\n".join(auto_notes)
        if overrides.content_notes:
            combined_notes = overrides.content_notes + "\n\n" + combined_notes
        body_content_notes = combined_notes
    else:
        body_content_notes = overrides.content_notes

    # ── Prior metrics ──────────────────────────────────────────
    prior_metrics_payload: dict[str, Any] = {}
    try:
        prior_metrics_payload = await build_prior_metrics_for_planner(
            db, tenant_id, lookback_days=60
        )
        if prior_metrics_payload:
            logger.info(
                "Prior metrics for plan: %d posts scored, platforms=%s",
                prior_metrics_payload.get("total_scored_posts", 0),
                list(prior_metrics_payload.get("platform_avg_scores", {}).keys()),
            )
    except Exception as exc:
        logger.warning("Prior metrics build failed (continuing without): %s", exc)

    # ── Call the LLM ───────────────────────────────────────────
    runner = runner or _build_default_runner()
    agent = PlannerAgent(runner)

    result = await agent.plan_campaign(
        tenant_id=tenant_id,
        user_id=user_id,
        artist_name=artist_name,
        release_title=release_title,
        release_type="album",
        release_date=release_date or str(campaign.start_date or ""),
        genre=overrides.genre or "general",
        channels=channels,
        track_listing=track_listing,
        destination_urls=overrides.destination_urls,
        prior_metrics=prior_metrics_payload or None,
        budget=overrides.budget or campaign.budget,
        available_assets=available_assets if available_assets else None,
        content_notes=body_content_notes,
        campaign_start=str(campaign.start_date) if campaign.start_date else "",
        campaign_end=str(campaign.end_date) if campaign.end_date else "",
        campaign_phase=campaign.phase or "",
        timezone=overrides.timezone,
        youtube_strategy=overrides.youtube_strategy,
    )
    logger.info(
        "Plan generated for campaign %s: start=%s end=%s",
        campaign.id,
        campaign.start_date,
        campaign.end_date,
    )

    # ── Parse plan with raw-json fallback ──────────────────────
    plan = result.structured
    if not plan and result.text:
        logger.warning(
            "Structured parse failed, attempting raw JSON fallback (%d chars)",
            len(result.text),
        )
        raw = result.text
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            raw = raw[brace_start:brace_end + 1]
        try:
            parsed = json.loads(raw)
            plan = PlannerOutput.model_validate(parsed)
            logger.info(
                "Raw JSON fallback succeeded: %d daily actions",
                len(plan.daily_actions),
            )
        except Exception as fallback_err:
            logger.warning("Raw JSON fallback also failed: %s", fallback_err)
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "daily_actions" in parsed:
                    from pydantic import TypeAdapter

                    adapter = TypeAdapter(list[DailyAction])
                    actions = adapter.validate_python(parsed["daily_actions"])
                    plan = PlannerOutput(
                        campaign_name=parsed.get("campaign_name", ""),
                        plan_start=parsed.get("plan_start", ""),
                        plan_end=parsed.get("plan_end", ""),
                        daily_actions=actions,
                        notes=parsed.get("notes", ""),
                    )
                    logger.info(
                        "Partial JSON fallback succeeded: %d daily actions",
                        len(plan.daily_actions),
                    )
            except Exception as partial_err:
                logger.warning(
                    "Partial JSON fallback failed: %s — raw: %s",
                    partial_err,
                    result.text[:500],
                )

    daily_actions_out: list[dict[str, Any]] = []
    calendar_items_created = 0
    draft_posts_created = 0

    # ── Track-coverage rebalancing ─────────────────────────────
    if plan and hasattr(plan, "daily_actions") and track_listing and len(track_listing) > 1:
        def _norm(s: str) -> str:
            return s.lower().strip().replace("'", "").replace("\u2019", "")

        track_names = [_norm(t) for t in track_listing]

        def _match_track(ref: str) -> str | None:
            ref_n = _norm(ref)
            if not ref_n:
                return None
            for tn in track_names:
                if tn in ref_n or ref_n in tn:
                    return tn
            return None

        track_counts: Counter = Counter()
        for action in plan.daily_actions:
            tn = _match_track(action.track_reference or "")
            if tn:
                track_counts[tn] += 1

        zero_tracks = [t for t in track_names if track_counts[t] == 0]
        max_track = track_counts.most_common(1)[0] if track_counts else None

        if zero_tracks:
            logger.info(
                "Track coverage gap: %d/%d tracks have zero posts. Rebalancing...",
                len(zero_tracks),
                len(track_names),
            )
            zero_idx = 0
            for action in plan.daily_actions:
                if zero_idx >= len(zero_tracks):
                    break
                ref = _norm(action.track_reference or "")
                matched = _match_track(ref)
                reassign = not matched or (
                    max_track and matched == max_track[0] and max_track[1] > 2
                )
                if reassign:
                    original_track = track_listing[
                        track_names.index(zero_tracks[zero_idx])
                    ]
                    action.track_reference = original_track
                    if action.content_brief and original_track.lower() not in action.content_brief.lower():
                        brief = re.sub(r"\n*\U0001f3b5 Featuring:.*", "", action.content_brief).rstrip()
                        action.content_brief = (
                            brief.replace(release_title or "", original_track)
                            if (release_title and release_title.lower() in brief.lower())
                            else f"{brief}\n\n\U0001f3b5 Featuring: {original_track}"
                        )
                    zero_idx += 1
            logger.info("Rebalanced %d posts to cover missing tracks", zero_idx)

        # Per-channel dedup
        channel_actions: dict[str, list] = defaultdict(list)
        for action in plan.daily_actions:
            channel_actions[action.platform].append(action)

        total_reassigned = 0
        for platform, actions in channel_actions.items():
            seen_tracks: set[str] = set()
            unused_on_channel = list(track_names)
            for action in actions:
                matched = _match_track(action.track_reference or "")
                if matched and matched in seen_tracks:
                    available = [t for t in unused_on_channel if t not in seen_tracks]
                    if available:
                        new_track_norm = available[0]
                        new_track = track_listing[track_names.index(new_track_norm)]
                        action.track_reference = new_track
                        if action.content_brief and new_track.lower() not in action.content_brief.lower():
                            brief = re.sub(r"\n*\U0001f3b5 Featuring:.*", "", action.content_brief).rstrip()
                            action.content_brief = (
                                brief.replace(release_title or "", new_track)
                                if (release_title and release_title.lower() in brief.lower())
                                else f"{brief}\n\n\U0001f3b5 Featuring: {new_track}"
                            )
                        seen_tracks.add(new_track_norm)
                        total_reassigned += 1
                    else:
                        if matched:
                            seen_tracks.add(matched)
                elif matched:
                    seen_tracks.add(matched)
                    if matched in unused_on_channel:
                        unused_on_channel.remove(matched)

        if total_reassigned:
            logger.info(
                "Per-channel dedup: reassigned %d duplicate-track posts",
                total_reassigned,
            )

    # ── Goal mix rebalancing ───────────────────────────────────
    resolved_goal_mix: dict[str, float] = {}
    if plan and hasattr(plan, "daily_actions") and plan.daily_actions:
        days_to_release: int | None = None
        if release and getattr(release, "release_date", None):
            try:
                days_to_release = (release.release_date - date.today()).days
            except Exception:
                days_to_release = None

        if overrides.goal_mix:
            resolved_goal_mix = normalize_mix(overrides.goal_mix)
        else:
            resolved_goal_mix = default_goal_mix(
                phase=campaign.phase,
                days_to_release=days_to_release,
            )

        reassigned = rebalance_goals(plan.daily_actions, resolved_goal_mix)
        if reassigned:
            logger.info(
                "Goal rebalanced: %s (target=%s)",
                dict(reassigned),
                {k: round(v, 2) for k, v in resolved_goal_mix.items()},
            )

        try:
            campaign.config = {
                **(campaign.config or {}),
                "goal_mix": resolved_goal_mix,
            }
        except Exception as exc:
            logger.warning("Could not persist goal_mix on campaign: %s", exc)

    # ── Materialize calendar items + draft posts ───────────────
    if plan and hasattr(plan, "daily_actions"):
        for day_idx, action in enumerate(plan.daily_actions, 1):
            daily_actions_out.append(
                {
                    "day": action.day,
                    "platform": action.platform,
                    "action_type": action.action_type,
                    "content_brief": action.content_brief,
                    "cta_destination": action.cta_destination,
                    "priority": action.priority,
                    "track_reference": action.track_reference,
                    "goal": action.goal,
                }
            )

            post_time_local = _POST_TIMES_LOCAL[day_idx % len(_POST_TIMES_LOCAL)]

            try:
                action_date = (
                    date.fromisoformat(action.day) if action.day else None
                )
                if action_date:
                    cal_item = CalendarItemModel(
                        tenant_id=tenant_id,
                        campaign_id=campaign.id,
                        title=f"{action.action_type.replace('_', ' ').title()}: {action.content_brief[:80]}",
                        description=action.content_brief,
                        item_type=action.action_type or "post",
                        scheduled_date=action_date,
                        scheduled_time=post_time_local,
                    )
                    db.add(cal_item)
                    calendar_items_created += 1
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping calendar item for invalid date %s: %s",
                    action.day,
                    exc,
                )

            _non_post_types = {"live", "engagement", "email"}
            if action.action_type.lower().strip() in _non_post_types:
                continue

            try:
                channel = (
                    await db.execute(
                        select(ChannelConnectionModel)
                        .where(
                            ChannelConnectionModel.tenant_id == tenant_id,
                            ChannelConnectionModel.platform == action.platform,
                            ChannelConnectionModel.is_active == True,  # noqa: E712
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if channel is None:
                    continue

                if campaign_mode == "autopilot":
                    post_approval = "approved"
                    post_status = "draft"
                elif campaign_mode == "ai_plan":
                    post_approval = "pending_review"
                    post_status = "draft"
                else:
                    post_approval = None
                    post_status = "draft"

                # Compute scheduled_at in UTC, advancing past now if needed.
                scheduled_at: datetime | None = None
                try:
                    action_date = (
                        date.fromisoformat(action.day) if action.day else None
                    )
                    if action_date:
                        user_tz = ZoneInfo(overrides.timezone)
                        local_dt = datetime.combine(
                            action_date, post_time_local, tzinfo=user_tz
                        )
                        candidate_utc = local_dt.astimezone(
                            ZoneInfo("UTC")
                        ).replace(tzinfo=None)
                        now_naive = datetime.utcnow()
                        if candidate_utc < now_naive:
                            future_times = []
                            for t in _POST_TIMES_LOCAL:
                                lt = datetime.combine(
                                    action_date, t, tzinfo=user_tz
                                )
                                ut = lt.astimezone(
                                    ZoneInfo("UTC")
                                ).replace(tzinfo=None)
                                if ut > now_naive:
                                    future_times.append(ut)
                            candidate_utc = (
                                future_times[0]
                                if future_times
                                else now_naive + timedelta(minutes=5)
                            )
                        scheduled_at = candidate_utc
                except (ValueError, TypeError):
                    pass

                # Goal-aware CTA — system always wins. The planner used
                # to set cta_destination but it kept emitting bare labels
                # like "linktree" instead of URLs, which leaked through
                # as the literal post URL. cta_for() owns the answer.
                action_track = None
                if action.track_reference:
                    action_track = tracks_by_title.get(
                        action.track_reference.strip().lower()
                    )
                cta_pick = cta_for(
                    goal=action.goal or None,
                    platform=action.platform,
                    track=action_track,
                    release=release,
                    artist=artist,
                )
                resolved_cta = cta_pick.url
                caption_with_cta = inject_caption_cta(
                    action.content_brief, resolved_cta, action.platform
                )

                post = PostModel(
                    tenant_id=tenant_id,
                    campaign_id=campaign.id,
                    channel_id=channel.id,
                    platform=action.platform,
                    status=post_status,
                    content_text=caption_with_cta,
                    media_urls=[],
                    destination_url=resolved_cta,
                    approval_status=post_approval,
                    day_number=day_idx,
                    action_type_label=action.action_type,
                    scheduled_at=scheduled_at,
                    track_reference=action.track_reference or None,
                    goal=action.goal or None,
                )
                db.add(post)
                draft_posts_created += 1
            except Exception as exc:
                logger.warning("Skipping draft post creation: %s", exc)

        await db.flush()

    # ── Bandit shadow logging ──────────────────────────────────
    try:
        from amplify.core.services.bandit_service import load_bandit, log_decision
        from amplify.learning.ranking.post_ranker import CandidatePost

        bandit = await load_bandit(db, tenant_id)
        candidates = []
        for action in daily_actions_out:
            candidates.append(
                CandidatePost(
                    candidate_id=f"{action['day']}_{action['platform']}_{action['action_type']}",
                    platform=action["platform"],
                    content_type=action["action_type"],
                    campaign_phase=campaign.phase or "pre_release",
                )
            )
        if len(candidates) >= 2:
            decision = bandit.select(candidates)
            await log_decision(db, tenant_id, decision)
            logger.info("Bandit shadow decision logged for plan: %s", decision.policy_used)
    except Exception as bandit_exc:
        logger.debug("Bandit shadow logging skipped: %s", bandit_exc)

    return PlanGenerationResult(
        campaign_name=plan.campaign_name if plan else campaign.name,
        plan_start=plan.plan_start if plan else "",
        plan_end=plan.plan_end if plan else "",
        daily_actions=daily_actions_out,
        notes=plan.notes if plan else "",
        calendar_items_created=calendar_items_created,
        draft_posts_created=draft_posts_created,
        model=result.model,
        elapsed_ms=result.elapsed_ms,
        goal_mix=resolved_goal_mix,
    )
