"""AI content generation routes — wires ContentAgent and PlannerAgent to API."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import get_db, get_settings, get_tenant_id, get_user_id, get_audit_service
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Request / Response schemas ───────────────────────────────────


class GenerateCaptionRequest(BaseModel):
    platform: str
    artist_name: str
    release_title: str = ""
    key_message: str = ""
    tone: str = "authentic"
    brand_voice: str = ""
    hashtags: list[str] = Field(default_factory=list)


class CaptionVariant(BaseModel):
    variant_id: str = ""
    headline: str = ""
    body: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""


class GenerateCaptionResponse(BaseModel):
    variants: list[CaptionVariant] = Field(default_factory=list)
    model: str = ""
    elapsed_ms: int = 0


class GeneratePlanRequest(BaseModel):
    campaign_id: str
    genre: str = ""
    channels: list[str] = Field(default_factory=list)
    track_listing: list[str] = Field(default_factory=list)
    destination_urls: dict[str, str] = Field(default_factory=dict)
    budget: float | None = None
    content_notes: str = ""  # e.g. "AI-generated music, no video footage, no personal photos"
    posts_per_day: int = 0  # 0 = let AI decide, 1-3 = target per channel per day
    focus: str = ""  # e.g. "engagement", "awareness", "conversions"


class DailyActionResponse(BaseModel):
    day: str = ""
    platform: str = ""
    action_type: str = ""
    content_brief: str = ""
    cta_destination: str = ""
    priority: str = "medium"
    track_reference: str = ""


class GeneratePlanResponse(BaseModel):
    campaign_name: str = ""
    plan_start: str = ""
    plan_end: str = ""
    daily_actions: list[DailyActionResponse] = Field(default_factory=list)
    notes: str = ""
    calendar_items_created: int = 0
    draft_posts_created: int = 0
    model: str = ""
    elapsed_ms: int = 0


# ── Helpers ──────────────────────────────────────────────────────


async def _auto_generate_post_video(
    *,
    db: "AsyncSession",
    tenant_id: uuid.UUID,
    image_url: str,
    artist_id: uuid.UUID | None,
    release_id: uuid.UUID | None,
    content_hint: str,
    day_number: int,
    settings: Settings,
    duration: int = 15,
) -> str | None:
    """Find the best audio asset and generate image+audio→video.

    Returns the uploaded video URL, or None if no audio available.
    """
    from amplify.db.models.asset import AssetModel
    from sqlalchemy import select, or_
    from app.services.media_service import MediaService
    from app.services.video_generator import create_post_video

    # Find audio assets for this artist/release
    q = select(AssetModel).where(
        AssetModel.tenant_id == tenant_id,
        AssetModel.asset_type == "audio",
        or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)),
    )
    if release_id:
        q = q.where(AssetModel.release_id == release_id)
    elif artist_id:
        q = q.where(AssetModel.artist_id == artist_id)
    q = q.order_by(AssetModel.created_at.desc()).limit(20)
    result = await db.execute(q)
    audio_assets = list(result.scalars().all())

    if not audio_assets:
        return None

    # Content-aware audio matching: pick the track mentioned in the caption
    from app.services.content_pipeline import _normalize_for_match, _any_phrase_match

    # Load release name to debias title-track matching
    release_name_clean = ""
    if release_id:
        try:
            from amplify.db.models.release import ReleaseModel
            rq = select(ReleaseModel).where(
                ReleaseModel.id == release_id,
                ReleaseModel.tenant_id == tenant_id,
            )
            rr = await db.execute(rq)
            rel = rr.scalar_one_or_none()
            if rel and rel.name:
                release_name_clean = _normalize_for_match(rel.name.lower())
        except Exception:
            pass

    hint_lower = content_hint.lower()
    hint_clean = _normalize_for_match(hint_lower)
    scored_audio = []
    for asset in audio_assets:
        score = 0
        name_lower = (asset.name or "").lower()
        name_clean = _normalize_for_match(name_lower)

        # Debias: if track name ≈ album name, the match is about the album, not the track
        is_title_track = (
            release_name_clean
            and name_clean
            and (name_clean == release_name_clean
                 or name_clean in release_name_clean
                 or release_name_clean in name_clean)
        )

        # Strong match: track name appears in caption
        if name_clean and len(name_clean) > 3 and name_clean in hint_clean:
            score += 10 if is_title_track else 50
        elif _any_phrase_match(hint_lower, name_lower):
            score += 8 if is_title_track else 40

        # Word-level matches
        for word in set(name_clean.split()):
            if len(word) > 3 and word in hint_clean:
                score += 5

        scored_audio.append((score, asset))

    scored_audio.sort(key=lambda x: x[0], reverse=True)

    # If there's a clear winner, use it. Otherwise rotate for variety.
    top_score = scored_audio[0][0] if scored_audio else 0
    if top_score > 0:
        close = [a for s, a in scored_audio if s >= top_score - 10]
        audio = close[day_number % len(close)] if len(close) > 1 else close[0]
    else:
        # No content match — rotate through all tracks
        audio = audio_assets[day_number % len(audio_assets)]

    # Pick a semi-random start point in the song for variety
    # Skip the first 15-30 seconds to get past intros
    audio_start = 15 + (day_number * 7) % 30  # varies between 15-44s

    svc = MediaService(
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
    )

    video_url = await create_post_video(
        image_url=image_url,
        audio_url=audio.file_url,
        tenant_id=tenant_id,
        media_service=svc,
        aspect_ratio="9:16",
        duration_seconds=duration,
        audio_start_seconds=audio_start,
    )

    # Save as an asset in the library for reuse
    asset = AssetModel(
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        asset_type="video",
        name=f"Auto clip - {audio.name or 'track'} ({duration}s)",
        file_url=video_url,
        mime_type="video/mp4",
        tags=["auto_generated", "post_video"],
        source="ai_generated",
        approval_status="approved",  # Auto-approve since it uses existing approved assets
    )
    db.add(asset)

    return video_url


def _build_runner():
    """Create AgentRunner with default config."""
    from amplify.agents.runtime.agent_runner import AgentRunner
    from amplify.agents.runtime.config import AgentConfig

    config = AgentConfig()
    return AgentRunner(config=config)


# ── Routes ───────────────────────────────────────────────────────


@router.post("/generate-caption", response_model=GenerateCaptionResponse)
async def generate_caption(
    body: GenerateCaptionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate AI caption variants for a post."""
    from amplify.agents.subagents.content_agent import ContentAgent

    try:
        runner = _build_runner()
        agent = ContentAgent(runner)

        result = await agent.generate_caption(
            tenant_id=tenant_id,
            user_id=user_id,
            artist_name=body.artist_name,
            platform=body.platform,
            content_type="caption",
            release_title=body.release_title,
            key_message=body.key_message,
            cta_destination="",
            tone=body.tone,
            brand_voice=body.brand_voice,
            hashtags=body.hashtags or None,
        )

        variants = []
        if result.structured and hasattr(result.structured, "variants"):
            for v in result.structured.variants:
                variants.append(CaptionVariant(
                    variant_id=v.variant_id,
                    headline=v.headline,
                    body=v.body,
                    hashtags=v.hashtags,
                    cta=v.cta,
                ))

        # If structured parsing failed, try to extract from raw text
        if not variants and result.text:
            variants.append(CaptionVariant(
                variant_id="A",
                body=result.text,
            ))

        try:
            await audit.log(
                action="ai.caption_generated",
                entity_type="ai_generation",
                user_id=user_id,
                changes={
                    "platform": body.platform,
                    "artist_name": body.artist_name,
                    "variants_count": len(variants),
                    "model": result.model,
                },
            )
        except Exception:
            pass

        return GenerateCaptionResponse(
            variants=variants,
            model=result.model,
            elapsed_ms=result.elapsed_ms,
        )

    except Exception as exc:
        logger.exception("Caption generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {exc}")


@router.post("/generate-plan", response_model=GeneratePlanResponse)
async def generate_plan(
    body: GeneratePlanRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
    settings_obj: Settings = Depends(get_settings),
):
    """Generate a campaign plan and create calendar items + draft posts."""
    from amplify.agents.subagents.planner_agent import PlannerAgent
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.calendar_item import CalendarItemModel
    from amplify.db.models.post import PostModel
    from amplify.db.repository import BaseRepository
    from sqlalchemy import select

    # Load campaign with artist and release
    campaign_repo = BaseRepository(db, CampaignModel, tenant_id)
    campaign = await campaign_repo.get(uuid.UUID(body.campaign_id))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign_mode = campaign.mode if hasattr(campaign, 'mode') else "manual"

    # Merge campaign config into request (config saved at campaign creation)
    campaign_config = campaign.config or {}
    if not body.channels and campaign_config.get("channels"):
        body.channels = campaign_config["channels"]
    if not body.posts_per_day and campaign_config.get("posts_per_day"):
        body.posts_per_day = campaign_config["posts_per_day"]
    if not body.focus and campaign_config.get("focus"):
        body.focus = campaign_config["focus"]
    if not body.content_notes and campaign_config.get("content_notes"):
        body.content_notes = campaign_config["content_notes"]
    if not body.genre and campaign_config.get("genre"):
        body.genre = campaign_config["genre"]

    # Load artist name
    from amplify.db.models.artist import ArtistModel
    artist_result = await db.execute(
        select(ArtistModel).where(ArtistModel.id == campaign.artist_id)
    )
    artist = artist_result.scalar_one_or_none()
    artist_name = artist.name if artist else "Unknown Artist"

    # Load release info if linked
    release_title = ""
    release_date = ""
    release = None
    track_listing = body.track_listing
    if campaign.release_id:
        from amplify.db.models.release import ReleaseModel
        release_result = await db.execute(
            select(ReleaseModel).where(ReleaseModel.id == campaign.release_id)
        )
        release = release_result.scalar_one_or_none()
        if release:
            release_title = release.title or ""
            release_date = str(release.release_date) if release.release_date else ""
            if not track_listing:
                from amplify.db.models.track import TrackModel
                tracks_result = await db.execute(
                    select(TrackModel).where(TrackModel.release_id == release.id).order_by(TrackModel.track_number)
                )
                track_listing = [t.title for t in tracks_result.scalars().all() if t.title]

            # Auto-populate destination_urls from release if not provided
            if not body.destination_urls:
                dest = {}
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
                    body.destination_urls = dest
                    logger.info("Auto-populated destination_urls from release: %s", list(dest.keys()))

    # Channels — use provided or default to connected platforms
    channels = body.channels
    if not channels:
        from amplify.db.models.channel import ChannelConnectionModel
        ch_result = await db.execute(
            select(ChannelConnectionModel.platform).where(
                ChannelConnectionModel.tenant_id == tenant_id,
                ChannelConnectionModel.is_active == True,
            ).distinct()
        )
        channels = [row[0] for row in ch_result.all()]

    if not channels:
        channels = ["instagram"]

    # Query available assets for this artist/release to inform the planner
    available_assets: list[dict[str, str]] = []
    asset_type_summary: dict[str, int] = {}
    try:
        from amplify.db.models.asset import AssetModel
        from sqlalchemy import or_
        asset_q = select(AssetModel).where(AssetModel.tenant_id == tenant_id)
        # Exclude rejected assets
        asset_q = asset_q.where(
            or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None))
        )
        # Prefer assets linked to this artist or release
        if campaign.artist_id:
            asset_q = asset_q.where(
                or_(
                    AssetModel.artist_id == campaign.artist_id,
                    AssetModel.release_id == campaign.release_id,
                    AssetModel.campaign_id == campaign.id,
                    AssetModel.artist_id.is_(None),  # unlinked tenant assets
                )
            )
        asset_q = asset_q.order_by(AssetModel.created_at.desc()).limit(50)
        asset_result = await db.execute(asset_q)
        for a in asset_result.scalars().all():
            available_assets.append({
                "name": a.name,
                "asset_type": a.asset_type,
                "tags": ", ".join(a.tags) if a.tags else "",
                "mime_type": a.mime_type or "",
            })
            asset_type_summary[a.asset_type] = asset_type_summary.get(a.asset_type, 0) + 1
        logger.info(
            "Planner context: %d available assets — %s",
            len(available_assets),
            ", ".join(f"{v} {k}" for k, v in asset_type_summary.items()),
        )
    except Exception as exc:
        logger.warning("Failed to load assets for planner context: %s", exc)

    # Build content notes with asset summary so planner knows what's possible
    has_video = asset_type_summary.get("video", 0) + asset_type_summary.get("lyric_video", 0) > 0
    has_audio = asset_type_summary.get("audio", 0) > 0
    has_images = sum(asset_type_summary.get(t, 0) for t in ("image", "album_art", "promo_photo")) > 0
    auto_notes = []
    if not has_video:
        auto_notes.append(
            "NO VIDEO FILES in library. Do NOT plan video content (reels, shorts, stories with video). "
            "Use image posts with audio snippets instead."
        )
    if has_audio:
        auto_notes.append(
            f"HAS {asset_type_summary.get('audio', 0)} AUDIO TRACKS. "
            "For TikTok and YouTube, the system will auto-generate 15-second videos "
            "(image + audio clip) from the artist's tracks. Plan TikTok/YouTube posts "
            "that feature specific tracks — each post will use a different track excerpt."
        )
    if has_images:
        img_count = sum(asset_type_summary.get(t, 0) for t in ("image", "album_art", "promo_photo"))
        auto_notes.append(f"HAS {img_count} IMAGES (album art, promo photos). Can do carousel posts on Instagram.")
    # Add posts_per_day and focus as planner directives
    if body.posts_per_day and body.posts_per_day > 0:
        auto_notes.append(
            f"TARGET {body.posts_per_day} POST(S) PER CHANNEL PER DAY. "
            "Spread them across the day at different times."
        )
    if body.focus:
        focus_map = {
            "engagement": "Focus on ENGAGEMENT — interactive content, questions, polls, reply-bait, share-worthy posts.",
            "awareness": "Focus on AWARENESS — maximize reach, use trending formats, hashtag strategy, shareable content.",
            "conversions": "Focus on CONVERSIONS — strong CTAs, link in bio reminders, pre-save pushes, streaming link drops.",
            "community": "Focus on COMMUNITY BUILDING — behind-the-scenes, personal stories, fan interaction, user-generated content.",
        }
        focus_note = focus_map.get(body.focus, f"Campaign focus: {body.focus}")
        auto_notes.append(focus_note)

    if auto_notes:
        combined_notes = "\n".join(auto_notes)
        if body.content_notes:
            combined_notes = body.content_notes + "\n\n" + combined_notes
        body_content_notes = combined_notes
    else:
        body_content_notes = body.content_notes

    try:
        runner = _build_runner()
        agent = PlannerAgent(runner)

        result = await agent.plan_campaign(
            tenant_id=tenant_id,
            user_id=user_id,
            artist_name=artist_name,
            release_title=release_title,
            release_type="album",
            release_date=release_date or str(campaign.start_date or ""),
            genre=body.genre or "general",
            channels=channels,
            track_listing=track_listing,
            destination_urls=body.destination_urls,
            budget=body.budget or campaign.budget,
            available_assets=available_assets if available_assets else None,
            content_notes=body_content_notes,
            campaign_start=str(campaign.start_date) if campaign.start_date else "",
            campaign_end=str(campaign.end_date) if campaign.end_date else "",
            campaign_phase=campaign.phase or "",
        )
        logger.info(
            "Plan generated for campaign %s: start=%s end=%s",
            campaign.id, campaign.start_date, campaign.end_date,
        )
    except Exception as exc:
        logger.exception("Plan generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI plan generation failed: {exc}")

    # Parse the plan output — try structured first, then fall back to raw JSON
    plan = result.structured

    if not plan and result.text:
        logger.warning("Structured parse failed, attempting raw JSON fallback from text (%d chars)", len(result.text))
        import json as _json
        import re as _re
        from amplify.agents.subagents.planner_agent import PlannerOutput

        # Try to extract JSON from the raw text
        raw = result.text

        # Safely extract from markdown fences if present
        fence_match = _re.search(r"```(?:json)?\s*\n?(.*?)```", raw, _re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()

        # Also try finding the outermost { ... }
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            raw = raw[brace_start:brace_end + 1]

        try:
            parsed = _json.loads(raw)
            plan = PlannerOutput.model_validate(parsed)
            logger.info("Raw JSON fallback succeeded: %d daily actions", len(plan.daily_actions))
        except Exception as fallback_err:
            logger.warning("Raw JSON fallback also failed: %s", fallback_err)
            # Last resort: try to extract just daily_actions
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, dict) and "daily_actions" in parsed:
                    from pydantic import TypeAdapter
                    from amplify.agents.subagents.planner_agent import DailyAction
                    adapter = TypeAdapter(list[DailyAction])
                    actions = adapter.validate_python(parsed["daily_actions"])
                    plan = PlannerOutput(
                        campaign_name=parsed.get("campaign_name", ""),
                        plan_start=parsed.get("plan_start", ""),
                        plan_end=parsed.get("plan_end", ""),
                        daily_actions=actions,
                        notes=parsed.get("notes", ""),
                    )
                    logger.info("Partial JSON fallback succeeded: %d daily actions", len(plan.daily_actions))
            except Exception as partial_err:
                logger.warning("Partial JSON fallback failed: %s — raw text: %s", partial_err, result.text[:500])

    daily_actions = []
    calendar_items_created = 0
    draft_posts_created = 0

    if plan and hasattr(plan, "daily_actions"):
        for day_idx, action in enumerate(plan.daily_actions, 1):
            daily_actions.append(DailyActionResponse(
                day=action.day,
                platform=action.platform,
                action_type=action.action_type,
                content_brief=action.content_brief,
                cta_destination=action.cta_destination,
                priority=action.priority,
                track_reference=action.track_reference,
            ))

            # Create calendar item for each action
            # Stagger times throughout the day for variety
            from datetime import date as date_type, time as time_type
            _post_times = [
                time_type(9, 0), time_type(10, 30), time_type(12, 0),
                time_type(14, 0), time_type(16, 30), time_type(18, 0),
                time_type(19, 30), time_type(21, 0),
            ]
            post_time = _post_times[day_idx % len(_post_times)]

            try:
                action_date = date_type.fromisoformat(action.day) if action.day else None
                if action_date:
                    cal_item = CalendarItemModel(
                        tenant_id=tenant_id,
                        campaign_id=campaign.id,
                        title=f"{action.action_type.replace('_', ' ').title()}: {action.content_brief[:80]}",
                        description=action.content_brief,
                        item_type=action.action_type or "post",
                        scheduled_date=action_date,
                        scheduled_time=post_time,
                    )
                    db.add(cal_item)
                    calendar_items_created += 1
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping calendar item for invalid date %s: %s", action.day, exc)

            # Create draft post for all content actions (anything except pure engagement/live)
            _non_post_types = {"live", "engagement", "email"}
            action_lower = action.action_type.lower().strip()
            if action_lower not in _non_post_types:
                try:
                    # Find a matching channel
                    from amplify.db.models.channel import ChannelConnectionModel
                    ch_result = await db.execute(
                        select(ChannelConnectionModel).where(
                            ChannelConnectionModel.tenant_id == tenant_id,
                            ChannelConnectionModel.platform == action.platform,
                            ChannelConnectionModel.is_active == True,
                        ).limit(1)
                    )
                    channel = ch_result.scalar_one_or_none()
                    if channel:
                        # Determine approval status based on campaign mode
                        if campaign_mode == "autopilot":
                            post_approval = "approved"
                            post_status = "draft"  # will be scheduled later
                        elif campaign_mode == "ai_plan":
                            post_approval = "pending_review"
                            post_status = "draft"
                        else:
                            post_approval = None
                            post_status = "draft"

                        # Set scheduled_at from the action's day with staggered time
                        # If the time is in the past (today), push to next available slot
                        scheduled_at = None
                        try:
                            from datetime import datetime as dt_type
                            action_date = date_type.fromisoformat(action.day) if action.day else None
                            if action_date:
                                candidate = dt_type.combine(action_date, post_time)
                                now = dt_type.utcnow()
                                if candidate < now:
                                    # Time already passed — find the next future slot today
                                    future_times = [
                                        dt_type.combine(action_date, t)
                                        for t in _post_times
                                        if dt_type.combine(action_date, t) > now
                                    ]
                                    if future_times:
                                        candidate = future_times[0]
                                    else:
                                        # All today's slots passed — schedule 5 min from now
                                        from datetime import timedelta
                                        candidate = now + timedelta(minutes=5)
                                scheduled_at = candidate
                        except (ValueError, TypeError):
                            pass

                        # Plan creation only sets caption + channel + date.
                        # Media is generated per-post via the "Generate" button.
                        post = PostModel(
                            tenant_id=tenant_id,
                            campaign_id=campaign.id,
                            channel_id=channel.id,
                            platform=action.platform,
                            status=post_status,
                            content_text=action.content_brief,
                            media_urls=[],
                            destination_url=action.cta_destination or (release.linktree_url if release and hasattr(release, 'linktree_url') and release.linktree_url else ""),
                            approval_status=post_approval,
                            day_number=day_idx,
                            action_type_label=action.action_type,
                            scheduled_at=scheduled_at,
                        )
                        db.add(post)
                        draft_posts_created += 1
                except Exception as exc:
                    logger.warning("Skipping draft post creation: %s", exc)

        await db.flush()

    try:
        await audit.log(
            action="ai.plan_generated",
            entity_type="campaign",
            entity_id=campaign.id,
            user_id=user_id,
            changes={
                "daily_actions": len(daily_actions),
                "calendar_items_created": calendar_items_created,
                "draft_posts_created": draft_posts_created,
                "model": result.model,
            },
        )
    except Exception:
        pass

    return GeneratePlanResponse(
        campaign_name=plan.campaign_name if plan else campaign.name,
        plan_start=plan.plan_start if plan else "",
        plan_end=plan.plan_end if plan else "",
        daily_actions=daily_actions,
        notes=plan.notes if plan else "",
        calendar_items_created=calendar_items_created,
        draft_posts_created=draft_posts_created,
        model=result.model,
        elapsed_ms=result.elapsed_ms,
    )


# ── Static Video (Tier 1) Generation ──────────────────────────


class GenerateStaticVideoRequest(BaseModel):
    post_id: str
    duration_seconds: int = 15
    aspect_ratio: str = "9:16"


class GenerateStaticVideoResponse(BaseModel):
    video_url: str = ""
    asset_id: str | None = None
    elapsed_ms: int = 0


@router.post("/generate-static-video", response_model=GenerateStaticVideoResponse)
async def generate_static_video_endpoint(
    body: GenerateStaticVideoRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings_obj: Settings = Depends(get_settings),
):
    """Generate a static image+audio video for a post (Tier 1).

    Picks the best image and audio from the asset library,
    combines into a short video clip via FFmpeg, and attaches to the post.
    """
    import time
    from sqlalchemy import select
    from amplify.db.models.post import PostModel
    from amplify.db.models.campaign import CampaignModel

    start_time = time.time()

    # Load the post
    post_result = await db.execute(
        select(PostModel).where(PostModel.id == uuid.UUID(body.post_id), PostModel.tenant_id == tenant_id)
    )
    post = post_result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Resolve artist/release from campaign
    artist_id = None
    release_id = None
    if post.campaign_id:
        camp_result = await db.execute(select(CampaignModel).where(CampaignModel.id == post.campaign_id))
        campaign = camp_result.scalar_one_or_none()
        if campaign:
            artist_id = campaign.artist_id
            release_id = campaign.release_id

    # Find an image — use existing post media or find from library
    from app.services.content_pipeline import _find_matching_assets
    image_urls = await _find_matching_assets(
        db=db,
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        campaign_id=post.campaign_id,
        platform=post.platform,
        action_type=post.action_type_label,
        content_hint=post.content_text or "",
        day_number=post.day_number,
        max_results=1,
    )
    if not image_urls:
        raise HTTPException(status_code=400, detail="No images in asset library. Upload album art or promo photos first.")

    # Generate the video
    video_url = await _auto_generate_post_video(
        db=db,
        tenant_id=tenant_id,
        image_url=image_urls[0],
        artist_id=artist_id,
        release_id=release_id,
        content_hint=post.content_text or "",
        day_number=post.day_number or 1,
        settings=settings_obj,
        duration=min(max(body.duration_seconds, 10), 60),
    )

    if not video_url:
        raise HTTPException(status_code=400, detail="No audio tracks in asset library. Upload audio files first.")

    # Replace media on the post
    post.media_urls = [video_url]
    await db.flush()

    elapsed = int((time.time() - start_time) * 1000)
    logger.info("Static video generated for post %s in %dms", body.post_id, elapsed)

    return GenerateStaticVideoResponse(
        video_url=video_url,
        elapsed_ms=elapsed,
    )


# ── Lyric Video (Tier 2) Generation ──────────────────────────


class GenerateLyricVideoRequest(BaseModel):
    post_id: str | None = None  # Auto-attach result to this post
    image_url: str = ""  # S3 URL of background image
    audio_url: str = ""  # S3 URL of audio
    lyrics: str = ""  # Raw lyrics text
    aspect_ratio: str = "9:16"  # 9:16, 1:1, 16:9
    duration_seconds: int = 30  # 15, 30, or 60
    artist_name: str = ""
    track_title: str = ""
    # Convenience: auto-resolve from track/release
    track_id: str | None = None
    release_id: str | None = None


class GenerateLyricVideoResponse(BaseModel):
    video_url: str = ""
    duration_seconds: int = 0
    aspect_ratio: str = ""
    asset_id: str | None = None
    elapsed_ms: int = 0


@router.post("/generate-lyric-video", response_model=GenerateLyricVideoResponse)
async def generate_lyric_video(
    body: GenerateLyricVideoRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate a lyric video from image + audio + lyrics using FFmpeg."""
    import time
    import tempfile
    from pathlib import Path
    from sqlalchemy import select
    start_time = time.time()

    image_url = body.image_url
    audio_url = body.audio_url
    lyrics = body.lyrics
    artist_name = body.artist_name
    track_title = body.track_title

    # Auto-resolve from track if track_id provided
    if body.track_id:
        from amplify.db.models.track import TrackModel
        track_result = await db.execute(
            select(TrackModel).where(TrackModel.id == uuid.UUID(body.track_id))
        )
        track = track_result.scalar_one_or_none()
        if track:
            if not lyrics and track.lyrics:
                lyrics = track.lyrics
            if not audio_url and track.audio_url:
                audio_url = track.audio_url
            if not track_title and track.title:
                track_title = track.title

    # Auto-resolve track from post content or release tracks when no track_id
    if not body.track_id and body.post_id:
        from amplify.db.models.post import PostModel
        from amplify.db.models.track import TrackModel as _TrackModel
        post_result = await db.execute(
            select(PostModel).where(PostModel.id == uuid.UUID(body.post_id))
        )
        post = post_result.scalar_one_or_none()
        post_text = (post.content_text or "").lower() if post else ""

        # Find release_id from post's campaign
        resolve_release_id = body.release_id
        if not resolve_release_id and post and post.campaign_id:
            from amplify.db.models.campaign import CampaignModel
            camp_result = await db.execute(
                select(CampaignModel).where(CampaignModel.id == post.campaign_id)
            )
            camp = camp_result.scalar_one_or_none()
            if camp and camp.release_id:
                resolve_release_id = str(camp.release_id)

        if resolve_release_id:
            tracks_result = await db.execute(
                select(_TrackModel).where(
                    _TrackModel.release_id == uuid.UUID(resolve_release_id)
                ).order_by(_TrackModel.track_number)
            )
            all_tracks = list(tracks_result.scalars().all())

            if all_tracks and post_text:
                # Match track name to post content
                from app.services.content_pipeline import _normalize_for_match, _any_phrase_match
                best_track = None
                best_score = 0
                for t in all_tracks:
                    t_name = _normalize_for_match((t.title or "").lower())
                    p_text = _normalize_for_match(post_text)
                    score = 0
                    if t_name and len(t_name) > 3 and t_name in p_text:
                        score = 50
                    elif _any_phrase_match(post_text, (t.title or "").lower()):
                        score = 40
                    if score > best_score:
                        best_track, best_score = t, score

                if best_track:
                    if not lyrics and best_track.lyrics:
                        lyrics = best_track.lyrics
                    if not audio_url and best_track.audio_url:
                        audio_url = best_track.audio_url
                    if not track_title and best_track.title:
                        track_title = best_track.title
                    logger.info("Auto-matched track '%s' (score=%d) for lyric video from post content", best_track.title, best_score)

    # Auto-resolve image from release artwork
    if body.release_id and not image_url:
        from amplify.db.models.release import ReleaseModel
        release_result = await db.execute(
            select(ReleaseModel).where(ReleaseModel.id == uuid.UUID(body.release_id))
        )
        release = release_result.scalar_one_or_none()
        if release and release.artwork_url:
            image_url = release.artwork_url

    # Fall back: find an image asset from the library
    if not image_url:
        from amplify.db.models.asset import AssetModel
        asset_result = await db.execute(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.asset_type.in_(["image", "album_art", "promo_photo"]),
            ).order_by(AssetModel.created_at.desc()).limit(1)
        )
        asset = asset_result.scalar_one_or_none()
        if asset:
            image_url = asset.file_url

    # Fall back: find audio from asset library matched to post content
    if not audio_url:
        from amplify.db.models.asset import AssetModel as _AssetModel
        from sqlalchemy import or_ as _or
        audio_q = select(_AssetModel).where(
            _AssetModel.tenant_id == tenant_id,
            _AssetModel.asset_type == "audio",
            _or(_AssetModel.approval_status != "rejected", _AssetModel.approval_status.is_(None)),
        ).order_by(_AssetModel.created_at.desc()).limit(20)
        audio_result = await db.execute(audio_q)
        audio_assets = list(audio_result.scalars().all())
        if audio_assets:
            # Content-match: pick the track mentioned in the post/lyrics
            from app.services.content_pipeline import _normalize_for_match, _any_phrase_match
            hint = (lyrics or track_title or "").lower()
            best, best_score = audio_assets[0], 0
            for a in audio_assets:
                n = _normalize_for_match((a.name or "").lower())
                h = _normalize_for_match(hint)
                s = 50 if (n and len(n) > 3 and n in h) else (40 if _any_phrase_match(hint, (a.name or "").lower()) else 0)
                if s > best_score:
                    best, best_score = a, s
            audio_url = best.file_url

    # Validate inputs
    if not image_url:
        raise HTTPException(status_code=400, detail="No image available. Upload album art or provide an image URL.")
    if not audio_url:
        raise HTTPException(status_code=400, detail="No audio available. Upload a track or provide an audio URL.")
    if not lyrics:
        raise HTTPException(status_code=400, detail="No lyrics available. Add lyrics to the track first.")

    # Validate duration
    duration = min(max(body.duration_seconds, 10), 90)

    try:
        from app.services.video_generator import generate_lyric_video as gen_video, download_url_to_file

        with tempfile.TemporaryDirectory(prefix="lyricvid_") as tmp_dir:
            # Determine file extensions from URLs
            img_ext = Path(image_url.split("?")[0]).suffix or ".jpg"
            aud_ext = Path(audio_url.split("?")[0]).suffix or ".mp3"

            img_path = str(Path(tmp_dir) / f"input{img_ext}")
            aud_path = str(Path(tmp_dir) / f"input{aud_ext}")
            out_path = str(Path(tmp_dir) / "output.mp4")

            # Download inputs
            await download_url_to_file(image_url, img_path)
            await download_url_to_file(audio_url, aud_path)

            # Generate video
            await gen_video(
                image_path=img_path,
                audio_path=aud_path,
                lyrics=lyrics,
                output_path=out_path,
                aspect_ratio=body.aspect_ratio,
                duration_seconds=duration,
                artist_name=artist_name,
                track_title=track_title,
            )

            # Upload to S3
            from app.services.media_service import MediaService
            from app.config import Settings
            settings = Settings()
            media_svc = MediaService(
                s3_bucket=settings.s3_bucket,
                s3_region=settings.s3_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                media_base_url=settings.media_base_url,
            )
            with open(out_path, "rb") as f:
                video_url = await media_svc.upload(
                    tenant_id, f, f"lyric-video-{uuid.uuid4()}.mp4", "video/mp4"
                )

        # Resolve artist/release/campaign from the post for asset linking
        asset_artist_id = None
        asset_release_id = None
        asset_campaign_id = None
        if body.post_id:
            from amplify.db.models.post import PostModel
            from amplify.db.models.campaign import CampaignModel
            post_q = await db.execute(
                select(PostModel).where(PostModel.id == uuid.UUID(body.post_id))
            )
            linked_post = post_q.scalar_one_or_none()
            if linked_post and linked_post.campaign_id:
                asset_campaign_id = linked_post.campaign_id
                camp_q = await db.execute(
                    select(CampaignModel).where(CampaignModel.id == linked_post.campaign_id)
                )
                linked_camp = camp_q.scalar_one_or_none()
                if linked_camp:
                    asset_artist_id = linked_camp.artist_id
                    asset_release_id = linked_camp.release_id
                    if not artist_name and linked_camp.artist_id:
                        from amplify.db.models.artist import ArtistModel
                        art_q = await db.execute(
                            select(ArtistModel).where(ArtistModel.id == linked_camp.artist_id)
                        )
                        art = art_q.scalar_one_or_none()
                        if art:
                            artist_name = art.name

        # Create asset record
        from amplify.db.models.asset import AssetModel
        asset = AssetModel(
            tenant_id=tenant_id,
            artist_id=asset_artist_id,
            release_id=asset_release_id,
            campaign_id=asset_campaign_id,
            asset_type="lyric_video",
            name=f"Lyric Video — {track_title or 'Untitled'}",
            description=f"Generated lyric video for {artist_name} — {track_title}",
            file_url=video_url,
            mime_type="video/mp4",
            tags=["lyric_video", "generated"],
            source="ai_generated",
        )
        db.add(asset)
        await db.flush()
        await db.refresh(asset)

        # Auto-attach to post — replaces existing media (upgrade from static image/video)
        if body.post_id:
            from amplify.db.models.post import PostModel
            post_result = await db.execute(
                select(PostModel).where(
                    PostModel.id == uuid.UUID(body.post_id),
                    PostModel.tenant_id == tenant_id,
                )
            )
            post = post_result.scalar_one_or_none()
            if post:
                post.media_urls = [video_url]
                await db.flush()
                logger.info("Replaced media on post %s with lyric video", body.post_id)

        elapsed = int((time.time() - start_time) * 1000)

        try:
            await audit.log(
                action="ai.lyric_video_generated",
                entity_type="asset",
                entity_id=asset.id,
                user_id=user_id,
                changes={
                    "track_title": track_title,
                    "duration": duration,
                    "aspect_ratio": body.aspect_ratio,
                },
            )
        except Exception:
            pass

        return GenerateLyricVideoResponse(
            video_url=video_url,
            duration_seconds=duration,
            aspect_ratio=body.aspect_ratio,
            asset_id=str(asset.id),
            elapsed_ms=elapsed,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Lyric video generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Video generation failed: {exc}")


# ── AI Video Generation (Replicate) ────────────────────────────


class GenerateAIVideoRequest(BaseModel):
    prompt: str = ""  # Scene description or let AI generate from lyrics
    lyrics: str = ""  # Auto-generate scene prompts from lyrics
    image_url: str = ""  # Starting image (album art)
    audio_url: str = ""  # Audio to sync with
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30
    num_scenes: int = 6  # Number of AI-generated clips
    artist_name: str = ""
    track_title: str = ""
    track_id: str | None = None
    release_id: str | None = None
    post_id: str | None = None


class GenerateAIVideoResponse(BaseModel):
    video_url: str = ""
    asset_id: str | None = None
    approval_status: str = "pending_review"
    estimated_cost: float = 0.0
    clips_generated: int = 0
    elapsed_ms: int = 0


class EstimateAIVideoCostRequest(BaseModel):
    num_scenes: int = 6
    duration_seconds: int = 30


class EstimateAIVideoCostResponse(BaseModel):
    estimated_cost: float = 0.0
    num_clips: int = 0
    cost_per_clip: float = 0.0


@router.post("/estimate-video-cost", response_model=EstimateAIVideoCostResponse)
async def estimate_video_cost(body: EstimateAIVideoCostRequest):
    """Estimate the cost of AI video generation before committing."""
    from app.services.replicate_video import ESTIMATED_COST_PER_CLIP
    return EstimateAIVideoCostResponse(
        estimated_cost=body.num_scenes * ESTIMATED_COST_PER_CLIP,
        num_clips=body.num_scenes,
        cost_per_clip=ESTIMATED_COST_PER_CLIP,
    )


@router.post("/generate-ai-video", response_model=GenerateAIVideoResponse)
async def generate_ai_video(
    body: GenerateAIVideoRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate AI video clips from prompts, stitch with audio, save to library.

    Generated assets start with approval_status='pending_review' and must be
    approved before they enter the active asset library.
    """
    import time
    import tempfile
    from pathlib import Path
    from sqlalchemy import select
    from app.config import Settings

    start_time = time.time()
    settings = Settings()

    if not settings.replicate_api_token:
        raise HTTPException(status_code=503, detail="AI video generation not configured. Set REPLICATE_API_TOKEN.")

    # Resolve inputs from track/release if needed
    image_url = body.image_url
    audio_url = body.audio_url
    lyrics = body.lyrics
    artist_name = body.artist_name
    track_title = body.track_title

    if body.track_id:
        from amplify.db.models.track import TrackModel
        track_result = await db.execute(
            select(TrackModel).where(TrackModel.id == uuid.UUID(body.track_id))
        )
        track = track_result.scalar_one_or_none()
        if track:
            if not lyrics and track.lyrics:
                lyrics = track.lyrics
            if not audio_url and track.audio_url:
                audio_url = track.audio_url
            if not track_title and track.title:
                track_title = track.title

    if body.release_id and not image_url:
        from amplify.db.models.release import ReleaseModel
        release_result = await db.execute(
            select(ReleaseModel).where(ReleaseModel.id == uuid.UUID(body.release_id))
        )
        release = release_result.scalar_one_or_none()
        if release and release.artwork_url:
            image_url = release.artwork_url

    # Fall back to asset library image
    if not image_url:
        from amplify.db.models.asset import AssetModel
        asset_result = await db.execute(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.asset_type.in_(["image", "album_art", "promo_photo"]),
            ).order_by(AssetModel.created_at.desc()).limit(1)
        )
        asset_img = asset_result.scalar_one_or_none()
        if asset_img:
            image_url = asset_img.file_url

    # Fall back: find audio from asset library matched to post content
    if not audio_url:
        from amplify.db.models.asset import AssetModel as _AssetModel2
        from sqlalchemy import or_ as _or2
        audio_q2 = select(_AssetModel2).where(
            _AssetModel2.tenant_id == tenant_id,
            _AssetModel2.asset_type == "audio",
            _or2(_AssetModel2.approval_status != "rejected", _AssetModel2.approval_status.is_(None)),
        ).order_by(_AssetModel2.created_at.desc()).limit(20)
        audio_result2 = await db.execute(audio_q2)
        audio_assets2 = list(audio_result2.scalars().all())
        if audio_assets2:
            from app.services.content_pipeline import _normalize_for_match, _any_phrase_match
            hint2 = (lyrics or body.prompt or track_title or "").lower()
            best2, best_score2 = audio_assets2[0], 0
            for a2 in audio_assets2:
                n2 = _normalize_for_match((a2.name or "").lower())
                h2 = _normalize_for_match(hint2)
                s2 = 50 if (n2 and len(n2) > 3 and n2 in h2) else (40 if _any_phrase_match(hint2, (a2.name or "").lower()) else 0)
                if s2 > best_score2:
                    best2, best_score2 = a2, s2
            audio_url = best2.file_url

    if not audio_url:
        raise HTTPException(status_code=400, detail="No audio available. Upload a track or provide an audio URL.")

    # Generate scene prompts from lyrics if no explicit prompt
    from app.services.replicate_video import (
        generate_scene_prompts_from_lyrics,
        generate_music_video,
        ESTIMATED_COST_PER_CLIP,
    )

    if body.prompt:
        # Single prompt — replicate it for each scene
        prompts = [body.prompt] * body.num_scenes
    elif lyrics:
        prompts = await generate_scene_prompts_from_lyrics(
            lyrics=lyrics,
            artist_name=artist_name,
            track_title=track_title,
            num_scenes=body.num_scenes,
        )
    else:
        prompts = [
            f"Cinematic music video scene for {artist_name} - {track_title}. "
            f"Beautiful cinematography, atmospheric lighting."
        ] * body.num_scenes

    estimated_cost = len(prompts) * ESTIMATED_COST_PER_CLIP

    try:
        with tempfile.TemporaryDirectory(prefix="aivid_") as tmp_dir:
            output_path = await generate_music_video(
                prompts=prompts,
                audio_url=audio_url,
                image_url=image_url,
                duration_seconds=body.duration_seconds,
                aspect_ratio=body.aspect_ratio,
                replicate_api_token=settings.replicate_api_token,
                output_dir=tmp_dir,
            )

            # Upload to S3
            from app.services.media_service import MediaService
            media_svc = MediaService(
                s3_bucket=settings.s3_bucket,
                s3_region=settings.s3_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                media_base_url=settings.media_base_url,
            )
            with open(output_path, "rb") as f:
                video_url = await media_svc.upload(
                    tenant_id, f, f"ai-video-{uuid.uuid4()}.mp4", "video/mp4"
                )

        # Resolve campaign context for asset linking
        asset_artist_id = None
        asset_release_id = None
        asset_campaign_id = None
        if body.post_id:
            from amplify.db.models.post import PostModel
            from amplify.db.models.campaign import CampaignModel
            post_q = await db.execute(
                select(PostModel).where(PostModel.id == uuid.UUID(body.post_id))
            )
            linked_post = post_q.scalar_one_or_none()
            if linked_post and linked_post.campaign_id:
                asset_campaign_id = linked_post.campaign_id
                camp_q = await db.execute(
                    select(CampaignModel).where(CampaignModel.id == linked_post.campaign_id)
                )
                linked_camp = camp_q.scalar_one_or_none()
                if linked_camp:
                    asset_artist_id = linked_camp.artist_id
                    asset_release_id = linked_camp.release_id

        # Create asset with pending_review status
        from amplify.db.models.asset import AssetModel
        asset = AssetModel(
            tenant_id=tenant_id,
            artist_id=asset_artist_id,
            release_id=asset_release_id,
            campaign_id=asset_campaign_id,
            asset_type="ai_video",
            name=f"AI Video — {track_title or 'Untitled'}",
            description=f"AI-generated video for {artist_name} — {track_title}",
            file_url=video_url,
            mime_type="video/mp4",
            tags=["ai_video", "generated", "pending_review"],
            source="ai_generated",
            approval_status="pending_review",
            generation_prompt="\n---\n".join(prompts),
            generation_cost=estimated_cost,
        )
        db.add(asset)
        await db.flush()
        await db.refresh(asset)

        # Auto-attach to post — replaces existing media (upgrade)
        if body.post_id:
            from amplify.db.models.post import PostModel
            post_result = await db.execute(
                select(PostModel).where(
                    PostModel.id == uuid.UUID(body.post_id),
                    PostModel.tenant_id == tenant_id,
                )
            )
            post = post_result.scalar_one_or_none()
            if post:
                post.media_urls = [video_url]
                await db.flush()
                logger.info("Replaced media on post %s with AI video", body.post_id)

        # Record usage for billing
        try:
            from amplify.billing.metering import MeteringService, METRIC_MEDIA_RENDERS
            metering = MeteringService()
            await metering.record_usage(str(tenant_id), METRIC_MEDIA_RENDERS, len(prompts))
        except Exception:
            pass

        elapsed = int((time.time() - start_time) * 1000)

        try:
            await audit.log(
                action="ai.video_generated",
                entity_type="asset",
                entity_id=asset.id,
                user_id=user_id,
                changes={
                    "prompts": len(prompts),
                    "estimated_cost": estimated_cost,
                    "duration": body.duration_seconds,
                },
            )
        except Exception:
            pass

        return GenerateAIVideoResponse(
            video_url=video_url,
            asset_id=str(asset.id),
            approval_status="pending_review",
            estimated_cost=estimated_cost,
            clips_generated=len(prompts),
            elapsed_ms=elapsed,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI video generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI video generation failed: {exc}")
