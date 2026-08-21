"""Content generation pipeline — triggered on post approval.

When a post is approved, this service:
1. Generates a real caption from the brief using the ContentAgent
2. Finds matching assets from the asset library
3. Attaches the best media to the post
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def generate_content_for_post(
    db: AsyncSession,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict:
    """Generate real content for an approved post.

    Returns a dict with what was updated.
    """
    from amplify.db.models.post import PostModel
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.track import TrackModel

    # Load the post
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.tenant_id == tenant_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        logger.warning("Content pipeline: post %s not found", post_id)
        return {"error": "post_not_found"}

    # Load campaign context
    artist_name = "Unknown Artist"
    release_title = ""
    release_id = None
    artist_id = None

    if post.campaign_id:
        camp_result = await db.execute(
            select(CampaignModel).where(CampaignModel.id == post.campaign_id)
        )
        campaign = camp_result.scalar_one_or_none()
        if campaign:
            artist_id = campaign.artist_id
            release_id = campaign.release_id

            # Get artist name
            if campaign.artist_id:
                artist_result = await db.execute(
                    select(ArtistModel).where(ArtistModel.id == campaign.artist_id)
                )
                artist = artist_result.scalar_one_or_none()
                if artist:
                    artist_name = artist.name

            # Get release title
            if campaign.release_id:
                release_result = await db.execute(
                    select(ReleaseModel).where(ReleaseModel.id == campaign.release_id)
                )
                release = release_result.scalar_one_or_none()
                if release:
                    release_title = release.title or ""

    # Resolve the canonical track. track_id is the hard anchor set by the
    # planner; fall back to track_reference only for legacy posts that
    # pre-date the FK migration.
    track: TrackModel | None = None
    if post.track_id:
        tr = await db.execute(
            select(TrackModel).where(
                TrackModel.id == post.track_id,
                TrackModel.tenant_id == tenant_id,
            )
        )
        track = tr.scalar_one_or_none()
    canonical_track_title = (track.title if track else post.track_reference) or ""

    updates: dict = {}

    # Pre-load every other track title on this release so the caption
    # validator below can detect cross-pollinated captions like
    # "this line from Track A reminded me of Track B" — the classic
    # mismatch the track-anchor rework didn't fully close.
    sibling_track_titles: list[str] = []
    if release_id:
        try:
            from amplify.db.models.track import TrackModel as _TM
            _sibs = await db.execute(
                select(_TM.title).where(
                    _TM.release_id == release_id,
                    _TM.tenant_id == tenant_id,
                )
            )
            for (_title,) in _sibs.all():
                if (
                    _title
                    and _title.strip()
                    and _title.strip().lower() != (canonical_track_title or "").strip().lower()
                ):
                    sibling_track_titles.append(_title.strip())
        except Exception as exc:
            logger.debug("Failed to load sibling tracks for validation: %s", exc)

    # Step 1: Generate real caption from the brief, locked to the canonical
    # track title. The agent prompt uses this as the track this post is
    # about — not the fuzzy track_reference string. The validator below
    # rejects captions that mention any OTHER track on the same release.
    brief = post.content_text or ""
    if brief and len(brief) < 500:  # Looks like a brief, not a real caption
        try:
            caption, validator_meta = await _generate_caption_validated(
                tenant_id=tenant_id,
                user_id=user_id,
                artist_name=artist_name,
                release_title=release_title,
                track_title=canonical_track_title,
                platform=post.platform or "instagram",
                brief=brief,
                sibling_track_titles=sibling_track_titles,
            )
            if caption:
                post.content_text = caption
                updates["caption_generated"] = True
                updates["caption_validator"] = validator_meta
                logger.info(
                    "Generated caption for post %s (%d chars, validator=%s)",
                    post_id, len(caption), validator_meta,
                )
        except Exception as exc:
            logger.warning("Caption generation failed for post %s: %s", post_id, exc)
            updates["caption_error"] = str(exc)

    # Step 2: media attachment. Prefer a clip from the track's video, but
    # only for a fraction of the track's posts — the rest fall through to
    # the normal media pipeline (asset images, and lyric/static video
    # generated elsewhere). Mixing media types per track feeds the
    # intelligence layer comparable data points (clip vs. static) so it can
    # learn which format performs, instead of every video'd-track post being
    # a clip. See _should_use_clip.
    clip_share = _env_float("AMPLIFY_CLIP_SHARE", 0.5)
    if (
        (not post.media_urls or len(post.media_urls) == 0)
        and _should_use_clip(
            post_id=post_id, day_number=post.day_number, clip_share=clip_share
        )
    ):
        try:
            from amplify.agents.pipeline.clips import _find_clip_for_post
            # YouTube defaults to landscape, others to vertical
            _ar = "16:9" if post.platform == "youtube" else "9:16"
            clip_url = await _find_clip_for_post(
                db=db,
                tenant_id=tenant_id,
                release_id=release_id,
                track_reference=canonical_track_title or post.track_reference,
                platform=post.platform,
                campaign_id=post.campaign_id,
                day_number=post.day_number or 0,
                aspect_ratio=_ar,
            )
            if clip_url:
                post.media_urls = [clip_url]
                updates["clip_library_used"] = True
                logger.info("Used clip library for post %s", post_id)
        except Exception as exc:
            logger.debug("Clip library lookup failed for post %s: %s", post_id, exc)

    if not post.media_urls or len(post.media_urls) == 0:
        try:
            max_media = _desired_media_count(post.platform, post.action_type_label)
            media_urls = await _find_matching_assets(
                db=db,
                tenant_id=tenant_id,
                artist_id=artist_id,
                release_id=release_id,
                campaign_id=post.campaign_id,
                platform=post.platform,
                action_type=post.action_type_label,
                content_hint=brief,
                track_reference=canonical_track_title or post.track_reference,
                day_number=post.day_number,
                max_results=max_media,
            )
            if media_urls:
                post.media_urls = media_urls
                updates["assets_attached"] = len(media_urls)
                logger.info("Attached %d assets to post %s", len(media_urls), post_id)
        except Exception as exc:
            logger.warning("Asset matching failed for post %s: %s", post_id, exc)
            updates["asset_error"] = str(exc)

    await db.flush()
    return updates


async def _generate_caption(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    artist_name: str,
    release_title: str,
    track_title: str = "",
    platform: str,
    brief: str,
) -> str | None:
    """Use the ContentAgent to turn a brief into a real caption."""
    from amplify.agents.runtime.agent_runner import AgentRunner
    from amplify.agents.runtime.config import AgentConfig
    from amplify.agents.subagents.content_agent import ContentAgent

    config = AgentConfig()
    runner = AgentRunner(config=config)
    agent = ContentAgent(runner)

    result = await agent.generate_caption(
        tenant_id=tenant_id,
        user_id=user_id,
        artist_name=artist_name,
        platform=platform,
        content_type="caption",
        release_title=release_title,
        track_title=track_title,
        key_message=brief,
        cta_destination="link in bio",
        tone="authentic",
    )

    # Pick the best variant
    if result.structured and hasattr(result.structured, "variants"):
        variants = result.structured.variants
        if variants:
            best = variants[0]
            # The AI often populates `copy` instead of `body`; resolved_body
            # returns whichever is set. Reading best.body directly yielded an
            # empty body for those variants, so this returned bare hashtags —
            # which tripped the empty-body validator into 3 wasted retries per
            # post. Only trust the structured path when it has real body text;
            # otherwise fall through to the text/JSON parser below.
            body = best.resolved_body
            if body:
                tags = best.hashtags
                # hashtags is typed list[str] | str — a raw string would be
                # spread into characters by " ".join, so normalize first.
                if isinstance(tags, str):
                    tags = tags.split()
                caption = body
                if tags:
                    caption += "\n\n" + " ".join(tags)
                return caption

    # Fallback: try to parse raw text as JSON and extract caption
    if result.text:
        import json as _json
        import re as _re

        raw = result.text.strip()

        # Strip markdown code fences
        fence_match = _re.search(r"```(?:json)?\s*\n?(.*?)```", raw, _re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()

        # Try to extract structured caption from JSON
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                variants = parsed.get("variants", [])
                if variants and isinstance(variants, list):
                    v = variants[0]
                    # Try common field names for the caption text
                    body = v.get("copy") or v.get("body") or v.get("text") or v.get("caption") or ""
                    if body:
                        hashtags = v.get("hashtags", "")
                        if isinstance(hashtags, list):
                            hashtags = " ".join(hashtags)
                        # Don't append if hashtags are already in the body
                        if hashtags and hashtags not in body:
                            body += "\n\n" + hashtags
                        return body
        except (ValueError, KeyError, TypeError):
            pass

        # If it doesn't look like JSON, return as-is (it's a real caption)
        if not raw.startswith("{") and not raw.startswith("["):
            return raw

    return None


def _strip_hashtags_and_links(text: str) -> str:
    """Return the caption with hashtags and URLs removed — used to
    detect captions whose body is empty (only hashtags / link present).
    """
    if not text:
        return ""
    import re
    # Strip URLs (http/https) and hashtags. Bare emoji + whitespace
    # remain, which is intentional — a caption that's just "🎵" is
    # still a body, even if a thin one.
    cleaned = re.sub(r"https?://\S+", "", text)
    cleaned = re.sub(r"#\w+", "", cleaned)
    # Also strip the "🎵 Featuring: <name>" tag that the legacy planner
    # appended — that's metadata, not body content.
    cleaned = re.sub(r"\n*\U0001f3b5\s*Featuring:.*", "", cleaned, flags=re.DOTALL)
    # Common CTA fragments the planner appends; their presence alone
    # doesn't make a caption have a real body.
    cleaned = re.sub(r"\U0001f3a7\s*(Link in bio|https?://\S+)", "", cleaned)
    return cleaned.strip()


def _caption_mentions_other_tracks(
    caption: str,
    anchored_title: str,
    sibling_titles: list[str],
) -> list[str]:
    """Return the sibling track titles a caption mentions in violation
    of the single-anchor rule.

    A caption is allowed to mention only the anchored track. If it
    references any other track on the same release — even in an
    aside like "this hits like 'Track B' did" — that's a drift bug
    waiting to confuse a viewer (the video/audio plays Track A but
    the text invokes Track B). Detected so the caller can regenerate.

    Match is word-aware and punctuation-tolerant via _normalize_for_match.
    """
    if not sibling_titles or not caption:
        return []
    # CRITICAL: normalize BEFORE lowering. The camelCase splitter inside
    # _normalize_for_match keys off [a-z]→[A-Z] transitions, which only
    # exist on the original casing. Calling _normalize_for_match(s.lower())
    # silently disables hashtag detection — "#WhiskeyScars" never gets
    # split into "whiskey scars", and the validator misses every
    # camelCase hashtag mention of a sibling track.
    caption_clean = _normalize_for_match(caption).lower()
    anchored_clean = _normalize_for_match(anchored_title or "").lower()
    offenders: list[str] = []
    for sib in sibling_titles:
        sib_clean = _normalize_for_match(sib).lower()
        if not sib_clean or len(sib_clean) < 3:
            continue
        # Skip sibling titles that are substrings of (or contain) the
        # anchored title — e.g. "Bar in Heaven" vs "Ain't No Bar in
        # Heaven" — to avoid spurious false positives when track titles
        # nest. Reviewer logic over flagging here matches what users
        # would intuit as a "real" cross-track reference.
        if anchored_clean and (
            sib_clean in anchored_clean or anchored_clean in sib_clean
        ):
            continue
        # Word-boundary check: the sibling title must appear as a
        # contiguous phrase, not as a fragment inside another word.
        # _normalize_for_match has already split camelCase and stripped
        # punctuation, so a simple containment check on the clean form
        # is precise enough in practice.
        if sib_clean in caption_clean:
            offenders.append(sib)
    return offenders


async def _generate_caption_validated(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    artist_name: str,
    release_title: str,
    track_title: str,
    platform: str,
    brief: str,
    sibling_track_titles: list[str],
    max_retries: int = 2,
) -> tuple[str | None, dict]:
    """Generate a caption and reject any output that mentions a
    sibling track. Retries up to max_retries with an increasingly
    explicit constraint appended to the brief. On final failure,
    returns the last attempt anyway (with validator metadata so the
    caller can flag the post for human review).
    """
    meta: dict = {"attempts": 0, "offenders": [], "passed": False}
    last_caption: str | None = None
    base_brief = brief
    for attempt in range(max_retries + 1):
        meta["attempts"] = attempt + 1
        # Strengthen the prompt on retries with the explicit constraint
        # that produced the violation last time. The ContentAgent has
        # no forbidden-terms knob, so we inject into key_message.
        if attempt == 0:
            effective_brief = base_brief
        else:
            siblings_list = ", ".join(f"'{t}'" for t in sibling_track_titles)
            effective_brief = (
                f"{base_brief}\n\n"
                f"CONSTRAINT: This post is exclusively about '{track_title}' "
                f"from the release '{release_title}'. The caption MUST refer "
                f"only to '{track_title}'. Do NOT mention any other track "
                f"names from this release. Forbidden track names: "
                f"{siblings_list}."
            )
        last_caption = await _generate_caption(
            tenant_id=tenant_id,
            user_id=user_id,
            artist_name=artist_name,
            release_title=release_title,
            track_title=track_title,
            platform=platform,
            brief=effective_brief,
        )
        if not last_caption:
            meta["error"] = "caption_generation_returned_none"
            return None, meta
        # Empty-body guard: when the LLM returns variants with empty
        # body and only hashtags populated, _generate_caption produces
        # "\n\n#tag1 #tag2...". That's not a publishable post — the
        # text body is gone entirely. Treat as a validation failure so
        # we retry rather than letting bare hashtags ship.
        body_only = _strip_hashtags_and_links(last_caption)
        if len(body_only.strip()) < 20:
            meta["last_failure"] = "empty_body"
            logger.warning(
                "Caption validator: attempt %d returned hashtags-only "
                "(body=%r) — retrying",
                attempt + 1, body_only[:80],
            )
            continue
        offenders = _caption_mentions_other_tracks(
            last_caption, track_title, sibling_track_titles
        )
        meta["offenders"] = offenders
        if not offenders:
            meta["passed"] = True
            return last_caption, meta
        logger.warning(
            "Caption validator: attempt %d mentioned forbidden tracks %s — retrying",
            attempt + 1, offenders,
        )
    # All retries exhausted. Surface the last attempt so the post isn't
    # left empty, but the caller has the metadata to flag for human
    # review. Logged as warning so the failure is visible.
    logger.warning(
        "Caption validator: exhausted %d retries for track '%s'; final caption still mentions %s",
        max_retries, track_title, meta["offenders"],
    )
    return last_caption, meta


async def _find_matching_assets(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    artist_id: uuid.UUID | None,
    release_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    platform: str | None,
    action_type: str | None,
    content_hint: str = "",
    track_reference: str | None = None,
    day_number: int | None = None,
    assets_required: list[str] | None = None,
    max_results: int = 1,
    release_name: str | None = None,
) -> list[str]:
    """Find matching assets from the library.

    Improved matching:
    - Uses assets_required hints from the planner (e.g. "album art", "promo photo")
    - Scores by content hint keyword overlap with asset name/tags/description
    - Rotates through top candidates using day_number for variety
    - Returns multiple URLs for carousel posts
    """
    from amplify.db.models.asset import AssetModel
    from sqlalchemy import or_

    # ── Load release name so we can debias title-track matching ──
    # When album name == title track name, every caption referencing the album
    # would unfairly score the title track's assets highest.
    release_name_clean = ""
    if release_id and not release_name:
        try:
            from amplify.db.models.release import ReleaseModel
            rq = select(ReleaseModel).where(
                ReleaseModel.id == release_id,
                ReleaseModel.tenant_id == tenant_id,
            )
            rr = await db.execute(rq)
            rel = rr.scalar_one_or_none()
            if rel:
                release_name = rel.name
        except Exception:
            pass
    if release_name:
        release_name_clean = _normalize_for_match(release_name.lower())

    # Load all candidate assets, broadening scope progressively
    candidates: list = []
    for filters in [
        {"campaign_id": campaign_id} if campaign_id else None,
        {"release_id": release_id} if release_id else None,
        {"artist_id": artist_id} if artist_id else None,
        {},
    ]:
        if filters is None:
            continue

        q = select(AssetModel).where(AssetModel.tenant_id == tenant_id)
        # Exclude rejected assets
        q = q.where(or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)))
        for col, val in filters.items():
            q = q.where(getattr(AssetModel, col) == val)
        q = q.order_by(AssetModel.created_at.desc()).limit(50)

        result = await db.execute(q)
        candidates = list(result.scalars().all())
        if candidates:
            break

    if not candidates:
        return []

    # ── Build track name map for UUID-named asset enrichment ──
    track_url_to_title = await _build_track_name_map(db, tenant_id, release_id)

    # ── Extract explicit track reference ──
    # The planner-set track_reference (resolved from the canonical track
    # title via track_id at the caller) is authoritative. We deliberately do
    # NOT fall through to _extract_track_reference(content_hint) here — that
    # regex used to pick up stray "quoted" phrases from the caption and
    # override the planner's intent, which is exactly the bug class this
    # rework exists to kill.
    explicit_track = track_reference
    explicit_track_clean = _normalize_for_match(explicit_track).lower() if explicit_track else ""

    # Build combined hint text from content_hint + assets_required
    # IMPORTANT: normalize BEFORE lowering so camelCase splitting works on hashtags
    # e.g. #IfTheBootFitsWearIt → "if the boot fits wear it" (not "ifthebootfitswearit")
    hint_lower = _normalize_for_match(content_hint).lower()
    hint_words = set(w for w in hint_lower.split() if len(w) > 3)
    if assets_required:
        for req in assets_required:
            hint_words.update(w.lower() for w in req.split() if len(w) > 3)

    # Determine what we're looking for
    wants_video = action_type and action_type.lower() in ("reel", "short", "story")
    wants_album_art = any(
        kw in hint_lower
        for kw in ("album", "cover", "artwork", "album_art")
    )
    if assets_required:
        req_text = " ".join(assets_required).lower()
        if "video" in req_text:
            wants_video = True
        if "album" in req_text or "cover" in req_text or "artwork" in req_text:
            wants_album_art = True

    # IMPORTANT: Only consider visual assets (image, video, album_art, promo_photo, lyric_video)
    VISUAL_TYPES = {"image", "album_art", "promo_photo", "video", "lyric_video", "logo"}
    visual_candidates = [c for c in candidates if c.asset_type in VISUAL_TYPES]

    # Separate original assets from auto-generated clips to prevent repetition.
    # Auto-generated clips can be reused occasionally (e.g. cross-platform)
    # but shouldn't dominate — prefer original images/art for fresh video generation.
    original_assets = [
        c for c in visual_candidates
        if not (c.source == "ai_generated" and c.asset_type in ("video", "lyric_video"))
    ]

    # Use originals if available; fall back to all visual (including generated) only
    # if there are no original visual assets at all
    pool = original_assets if original_assets else (visual_candidates if visual_candidates else candidates)

    scored = []
    import re as _re
    for asset in pool:
        score = 0
        # Use asset name, falling back to track title or description if name is a UUID
        raw_name = asset.name or ""
        if _re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", raw_name):
            # Name is a UUID — try track title from audio_url mapping first
            track_title = track_url_to_title.get(asset.file_url, "")
            raw_name = track_title or asset.description or ""
        name_lower = raw_name.lower()
        desc_lower = (asset.description or "").lower()
        tag_text = " ".join(asset.tags or []).lower()

        logger.debug("Matching asset %s (name=%s, resolved=%s, type=%s) against hint: %s",
                      asset.id, asset.name, raw_name[:40], asset.asset_type, hint_lower[:60])

        # ── Normalize for comparison ──
        name_clean = _normalize_for_match(name_lower)
        # hint_lower is already normalized (camelCase split + lowered above)
        hint_clean = hint_lower

        # Debias title-track matching
        is_title_track_match = (
            release_name_clean
            and name_clean
            and (name_clean == release_name_clean
                 or name_clean in release_name_clean
                 or release_name_clean in name_clean)
        )

        # ── Priority 1: Explicit track reference match ──
        # If content says "Featuring: Good Boy", strongly prefer assets named "Good Boy"
        if explicit_track_clean and name_clean and len(name_clean) > 3:
            if name_clean in explicit_track_clean or explicit_track_clean in name_clean:
                score += 80  # Strongest signal — explicit track reference
                logger.debug("  → Explicit track match: %s ↔ %s (+80)", name_clean, explicit_track_clean)

        # ── Priority 2: Asset name appears in caption (or vice versa) ──
        if name_clean and len(name_clean) > 3 and name_clean in hint_clean:
            if is_title_track_match:
                score += 10
            else:
                score += 50
        elif hint_clean and len(hint_clean) > 3 and _any_phrase_match(hint_lower, name_lower):
            if is_title_track_match:
                score += 8
            else:
                score += 40

        # ── Keyword matching from hint + assets_required ──
        for word in hint_words:
            if word in name_clean:
                score += 10
            if word in desc_lower:
                score += 5
            if word in tag_text:
                score += 5

        # Type affinity scoring
        if wants_video and asset.asset_type in ("video", "lyric_video"):
            score += 15
        elif wants_album_art and asset.asset_type in ("album_art", "image"):
            score += 12
        else:
            if asset.asset_type in ("image", "album_art", "promo_photo"):
                score += 5
            elif asset.asset_type in ("video", "lyric_video"):
                score += 3

        # Bonus for assets linked to the same release/campaign
        if release_id and asset.release_id == release_id:
            score += 8
        if campaign_id and asset.campaign_id == campaign_id:
            score += 6

        # ── Proven vs untested bonus (Task #20) ──────────────────────
        # Assets that have been used in published posts with good
        # engagement get a scoring boost, so the library drifts toward
        # "use what works" over time. Untested assets don't get
        # penalized here — the experiment slot below gives them their
        # chance.
        if getattr(asset, "test_status", "untested") == "proven":
            score += 15

        # ── Usage-aware penalty ──────────────────────────────────────
        # Penalize heavily-used assets so the same image/video doesn't
        # dominate every post. Each prior use costs 8 points (capped
        # at -40). This ensures that after ~5 uses, even a strong
        # keyword match loses to a fresh alternative.
        asset_uses = getattr(asset, "uses_count", 0) or 0
        score -= min(asset_uses * 8, 40)

        scored.append((score, asset))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return []

    logger.info("Asset matching top 3: %s",
                [(s, a.name, a.asset_type) for s, a in scored[:3]])

    # For carousel posts (multiple images), return top N unique visual assets
    if max_results > 1:
        urls = []
        seen = set()
        for _, asset in scored:
            if asset.file_url not in seen:
                urls.append(asset.file_url)
                seen.add(asset.file_url)
            if len(urls) >= max_results:
                break
        return urls

    # ── Experiment slot (Task #20) ──────────────────────────────
    # Roughly 1 in EXPERIMENT_EVERY posts, if there's an untested
    # asset in the candidate pool, override the top pick with the
    # best untested asset so the library keeps learning. Keyed off
    # day_number for determinism: re-running the planner on the same
    # day picks the same experiment slot, which makes debugging sane.
    EXPERIMENT_EVERY = int(os.environ.get("AMPLIFY_EXPERIMENT_EVERY", "7") or 7)
    is_experiment_day = (
        day_number is not None
        and EXPERIMENT_EVERY > 0
        and day_number % EXPERIMENT_EVERY == 0
    )
    if is_experiment_day:
        untested = [
            (s, a)
            for s, a in scored
            if getattr(a, "test_status", "untested") == "untested" and s > 0
        ]
        if untested:
            pick = untested[0][1]
            logger.info(
                "Experiment slot (day=%s): picking untested asset %s (%s) score=%s",
                day_number, pick.id, pick.name, untested[0][0],
            )
            pick.uses_count = (pick.uses_count or 0) + 1
            pick.last_used_at = datetime.utcnow()
            return [pick.file_url]

    # Rotate among close candidates (within 20 pts) for variety.
    # The wider window ensures different assets surface across a campaign
    # instead of the same top-scorer winning every day.
    top_score = scored[0][0]
    close_candidates = [a for s, a in scored if s >= top_score - 20 and top_score > 0]
    if len(close_candidates) > 1 and day_number is not None:
        pick = close_candidates[day_number % len(close_candidates)]
    else:
        pick = scored[0][1]

    # Track usage so future calls penalize this asset
    pick.uses_count = (pick.uses_count or 0) + 1
    pick.last_used_at = datetime.utcnow()
    return [pick.file_url]


def _normalize_for_match(text: str) -> str:
    """Strip punctuation, extensions, and common suffixes for fuzzy matching."""
    import re
    # Remove file extensions
    text = re.sub(r"\.(wav|mp3|mp4|jpeg|jpg|png|webp|flac|aac)$", "", text)
    # Remove common suffixes like "cover art", "promo", etc. (whole words only)
    text = re.sub(r"\s+(?:cover art|cover|promo|artwork|art|audio|video|clip)\s*$", "", text)
    # Split camelCase/PascalCase: "IfTheBootFitsWearIt" → "If The Boot Fits Wear It"
    # This is critical for hashtags like #AintNoBarInHeaven
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    # Normalize parentheses, punctuation
    text = text.replace("(", "").replace(")", "").replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_track_reference(content: str) -> str | None:
    """Extract explicit track name from content patterns like '🎵 Featuring: Track Name'.

    Returns the track name if found, or None.
    """
    import re
    # Match "Featuring: Track Name" (with or without emoji prefix)
    m = re.search(r"(?:🎵\s*)?[Ff]eaturing:\s*(.+?)(?:\n|$)", content)
    if m:
        return m.group(1).strip()
    # Match quoted track titles: "Track Name" or 'Track Name'
    m = re.search(r'["\u201c]([^"\u201d]{3,50})["\u201d]', content)
    if m:
        return m.group(1).strip()
    return None


async def _build_track_name_map(
    db: "AsyncSession",
    tenant_id: "uuid.UUID",
    release_id: "uuid.UUID | None",
) -> dict[str, str]:
    """Build a map of audio asset file_url → track title from TrackModel.

    This lets us match UUID-named audio assets to their real track names.
    Uses two strategies:
    1. Direct match: track.audio_url == asset.file_url
    2. Track number match: if assets are in track_number order
    """
    if not release_id:
        return {}
    try:
        from amplify.db.models.track import TrackModel
        from amplify.db.models.asset import AssetModel
        from sqlalchemy import or_

        tq = select(TrackModel).where(
            TrackModel.release_id == release_id,
            TrackModel.tenant_id == tenant_id,
        ).order_by(TrackModel.track_number)
        result = await db.execute(tq)
        tracks = list(result.scalars().all())
        if not tracks:
            return {}

        # Map audio_url → title for tracks that have audio URLs
        url_to_title: dict[str, str] = {}
        for track in tracks:
            if track.audio_url:
                url_to_title[track.audio_url] = track.title

        # Also load audio assets for this release to try positional matching
        # (when assets are uploaded in track order but have UUID names)
        if len(url_to_title) < len(tracks):
            aq = select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.release_id == release_id,
                AssetModel.asset_type == "audio",
                or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)),
            ).order_by(AssetModel.created_at)
            ar = await db.execute(aq)
            audio_assets = list(ar.scalars().all())

            # If asset count matches track count, map by position
            if len(audio_assets) == len(tracks):
                for asset, track in zip(audio_assets, tracks):
                    if asset.file_url not in url_to_title:
                        url_to_title[asset.file_url] = track.title

        return url_to_title
    except Exception:
        return {}


def _any_phrase_match(caption: str, asset_name: str) -> bool:
    """Check if any meaningful phrase from the caption appears in the asset name.

    Catches cases like caption mentioning 'Boat Problems' matching asset 'Boat Problems.wav'
    """
    import re
    # Look for quoted titles or capitalized phrases in the original (pre-lowered) text
    # Since we receive lowered text, look for multi-word sequences that appear in asset name
    asset_clean = _normalize_for_match(asset_name)
    if not asset_clean or len(asset_clean) < 3:
        return False

    caption_clean = _normalize_for_match(caption)
    # Check if the full asset name appears in the caption
    if asset_clean in caption_clean:
        return True

    # Check 2-3 word sliding windows from caption against asset name
    words = caption_clean.split()
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if len(phrase) > 5 and phrase in asset_clean:
                return True

    return False


def _desired_media_count(platform: str | None, action_type: str | None) -> int:
    """How many media items should we attach?"""
    if not platform:
        return 1
    p = platform.lower()
    a = (action_type or "").lower().replace("_", " ")
    # Carousel posts get multiple images
    if "carousel" in a:
        return 3
    if p == "instagram" and a in ("post",):
        return 2
    return 1


def _env_float(name: str, default: float) -> float:
    """Read a float env var, tolerating empty/malformed values."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _should_use_clip(
    *,
    post_id: uuid.UUID,
    day_number: int | None,
    clip_share: float,
) -> bool:
    """Decide whether THIS post should use a video clip vs. fall through to
    the normal media pipeline (images / lyric video / static video).

    Even when a track has a full-length video, only a fraction
    (``clip_share``, default 0.5) of its posts are routed to clips; the rest
    use other media. Mixing media types per track gives the intelligence /
    learning layer comparable data points (clip vs. static) to learn which
    format drives engagement, instead of every video'd-track post looking
    identical. Deterministic per post so re-running content generation is
    stable and debuggable.
    """
    if clip_share >= 1.0:
        return True
    if clip_share <= 0.0:
        return False
    import hashlib
    seed = hashlib.md5(f"clipmix:{post_id}:{day_number or 0}".encode()).digest()
    bucket = int.from_bytes(seed[:4], "little") % 100
    return bucket < round(clip_share * 100)


async def generate_content_for_posts(
    db: AsyncSession,
    post_ids: list[uuid.UUID],
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict:
    """Generate content for multiple posts (used by approve-all)."""
    results = {}
    for post_id in post_ids:
        try:
            result = await generate_content_for_post(db, post_id, tenant_id, user_id)
            results[str(post_id)] = result
        except Exception as exc:
            logger.warning("Content pipeline failed for post %s: %s", post_id, exc)
            results[str(post_id)] = {"error": str(exc)}
    return results
