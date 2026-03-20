"""Seed database with Drew Baird's real campaign data.

First tenant of Amplify-OS — a real album launch, not demo data.

    Artist:   Drew Baird
    Album:    For Love of Country
    Genre:    Country
    Release:  2026-03-29
    Distro:   DistroKid → Apple Music, Spotify

Platforms:
    Instagram   https://www.instagram.com/dbmusic2026/
    TikTok      https://www.tiktok.com/@db.music06
    YouTube     https://www.youtube.com/@dbmusic2026
    Bandcamp    https://dbmusic26.bandcamp.com/
    Linktree    linktr.ee/dbmusic26

Usage:
    python scripts/seed_drew.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

try:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
except ImportError:
    print("ERROR: SQLAlchemy is not installed.")
    print("  pip install sqlalchemy[asyncio] asyncpg")
    raise SystemExit(1)

from amplify.db.base import Base
from amplify.db.models import (
    ApprovalModel,
    ArtistModel,
    AssetModel,
    AuditLogModel,
    BillingPlanModel,
    CalendarItemModel,
    CampaignModel,
    ChannelConnectionModel,
    ExperimentModel,
    MembershipModel,
    PostModel,
    ReleaseModel,
    RedirectModel,
    SubscriptionModel,
    TenantModel,
    TrackModel,
    UserModel,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///amplify_local.db",
)

# ── Fixed IDs (match local-mode middleware) ────────────────────────
TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# ── Entity IDs (deterministic so we can reference them) ───────────
ARTIST_ID = uuid.UUID("00000001-0000-0000-0000-000000000001")
RELEASE_ID = uuid.UUID("00000001-0000-0000-0000-000000000002")
CAMPAIGN_ID = uuid.UUID("00000001-0000-0000-0000-000000000003")
PLAN_ID = uuid.UUID("00000001-0000-0000-0000-000000000004")

CH_INSTAGRAM_ID = uuid.UUID("00000001-0000-0000-0000-0000000000C1")
CH_TIKTOK_ID = uuid.UUID("00000001-0000-0000-0000-0000000000C2")
CH_YOUTUBE_ID = uuid.UUID("00000001-0000-0000-0000-0000000000C3")
CH_BANDCAMP_ID = uuid.UUID("00000001-0000-0000-0000-0000000000C4")
CH_LINKTREE_ID = uuid.UUID("00000001-0000-0000-0000-0000000000C5")

RELEASE_DATE = date(2026, 3, 29)
CAMPAIGN_START = date(2026, 3, 1)  # 4 weeks before release
CAMPAIGN_END = date(2026, 4, 28)  # 30 days after release


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Social links ──────────────────────────────────────────────────
SOCIAL_LINKS = {
    "instagram": "https://www.instagram.com/dbmusic2026/",
    "tiktok": "https://www.tiktok.com/@db.music06",
    "youtube": "https://www.youtube.com/@dbmusic2026",
    "bandcamp": "https://dbmusic26.bandcamp.com/",
    "linktree": "https://linktr.ee/dbmusic26",
}

PLATFORMS = ["instagram", "tiktok", "youtube", "bandcamp", "linktree"]


async def seed() -> None:
    """Seed Drew Baird as the first Amplify-OS tenant."""
    is_sqlite = DATABASE_URL.startswith("sqlite")
    engine = create_async_engine(DATABASE_URL, echo=False)

    # Auto-create tables for SQLite (Postgres uses Alembic migrations)
    if is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as session:
        # --- Idempotency -------------------------------------------------------
        result = await session.execute(
            select(TenantModel).where(TenantModel.slug == "drew-baird")
        )
        if result.scalar_one_or_none() is not None:
            print("Seed data already exists (tenant 'drew-baird' found). Skipping.")
            await engine.dispose()
            return

        now = _now()
        print("Seeding Drew Baird — For Love of Country campaign\n")

        # ── 1. Tenant ─────────────────────────────────────────────
        session.add(TenantModel(
            id=TENANT_ID,
            name="Drew Baird Music",
            slug="drew-baird",
            plan_tier="free",
            settings={
                "timezone": "America/New_York",
                "brand_color": "#c9a84c",
                "deployment_mode": "local",
            },
            is_active=True,
        ))
        print(f"  Tenant: {TENANT_ID} (Drew Baird Music)")

        # ── 2. User ───────────────────────────────────────────────
        session.add(UserModel(
            id=USER_ID,
            email="drew@amplify.local",
            hashed_password="$2b$12$local_mode_no_password_needed",
            display_name="Drew Baird",
            is_active=True,
        ))
        print(f"  User: {USER_ID} (drew@amplify.local)")

        # ── 3. Membership (owner) ─────────────────────────────────
        session.add(MembershipModel(
            id=uuid.uuid4(),
            user_id=USER_ID,
            tenant_id=TENANT_ID,
            role="owner",
        ))

        # ── 4. Artist — Drew Baird ────────────────────────────────
        session.add(ArtistModel(
            id=ARTIST_ID,
            tenant_id=TENANT_ID,
            name="Drew Baird",
            slug="drew-baird",
            bio=(
                "Country artist and songwriter. Debut album 'For Love of Country' "
                "drops March 29, 2026."
            ),
            genre="Country",
            image_url=None,  # TODO: add artist photo
            spotify_id=None,  # Can't claim yet — too far from release
            social_links=SOCIAL_LINKS,
        ))
        print(f"  Artist: {ARTIST_ID} (Drew Baird)")

        # ── 5. Release — For Love of Country ──────────────────────
        session.add(ReleaseModel(
            id=RELEASE_ID,
            tenant_id=TENANT_ID,
            artist_id=ARTIST_ID,
            title="For Love of Country",
            release_type="album",
            status="scheduled",
            release_date=RELEASE_DATE,
            upc=None,  # DistroKid assigns this
            artwork_url=None,  # TODO: add album art
            metadata_={
                "genre_tags": ["country", "americana", "singer-songwriter"],
                "distributor": "DistroKid",
                "distrokid_album_uuid": "67EDECDC-EEBE-47F9-8E55E9E8C975C18D",
                "distribution_targets": ["Apple Music", "Spotify"],
                "artist_page_status": "not yet claimable — too far from release",
            },
            # Destination URLs
            bandcamp_url="https://dbmusic26.bandcamp.com/",
            linktree_url="https://linktr.ee/dbmusic26",
            youtube_url="https://www.youtube.com/@dbmusic2026",
            tiktok_url="https://www.tiktok.com/@db.music06",
            instagram_url="https://www.instagram.com/dbmusic2026/",
            hyperfollow_url=None,  # TODO: DistroKid HyperFollow link once available
        ))
        print(f"  Release: {RELEASE_ID} (For Love of Country — 2026-03-29)")

        # ── 5b. Tracks (14 tracks from DistroKid) ────────────────
        tracks_data = [
            ("For Love of Country",          1,  "QZNWW2678815"),
            ("Whis(key) to My Heart",        2,  "QZNWW2678816"),
            ("Ain't No Bar in Heaven",       3,  "QZNWW2678817"),
            ("Good Boy",                     4,  "QZNWW2678818"),
            ("Long Neck State of Mind",      5,  "QZNWW2678819"),
            ("Barefoot Dreams",              6,  "QZNWW2678820"),
            ("Boat Problems",               7,  "QZNWW2678821"),
            ("Whiskey Scars",               8,  "QZNWW2678822"),
            ("Caviar Cowboy",               9,  "QZNWW2678823"),
            ("Shoot Your Shot",             10, "QZNWW2678824"),
            ("Redneck Rich",                11, "QZNWW2678825"),
            ("If the Boot Fits, Wear it",   12, "QZNWW2678826"),
            ("Tequila Kisses",              13, "QZNWW2678827"),
            ("Use Me For Good",             14, "QZNWW2678828"),
        ]
        for title, num, isrc in tracks_data:
            session.add(TrackModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                release_id=RELEASE_ID,
                title=title,
                track_number=num,
                duration_seconds=None,  # TODO: add durations once known
                isrc=isrc,
                is_single=False,
            ))
            print(f"    Track {num:2d}: {title} ({isrc})")

        # ── 6. Channel connections ────────────────────────────────
        channels = [
            (CH_INSTAGRAM_ID, "instagram", "dbmusic2026", "Drew Baird",
             "https://www.instagram.com/dbmusic2026/"),
            (CH_TIKTOK_ID, "tiktok", "db.music06", "Drew Baird",
             "https://www.tiktok.com/@db.music06"),
            (CH_YOUTUBE_ID, "youtube", "dbmusic2026", "Drew Baird",
             "https://www.youtube.com/@dbmusic2026"),
            (CH_BANDCAMP_ID, "bandcamp", "dbmusic26", "Drew Baird",
             "https://dbmusic26.bandcamp.com/"),
            (CH_LINKTREE_ID, "linktree", "dbmusic26", "Drew Baird",
             "https://linktr.ee/dbmusic26"),
        ]
        for ch_id, platform, acct_id, display, url in channels:
            session.add(ChannelConnectionModel(
                id=ch_id,
                tenant_id=TENANT_ID,
                artist_id=ARTIST_ID,
                platform=platform,
                platform_account_id=acct_id,
                display_name=display,
                access_token=None,  # Real OAuth tokens added per-platform later
                refresh_token=None,
                token_expires_at=None,
                is_active=True,
                settings={"profile_url": url},
            ))
            print(f"  Channel: {platform} ({url})")

        # ── 7. Campaign — For Love of Country Launch ──────────────
        session.add(CampaignModel(
            id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            artist_id=ARTIST_ID,
            release_id=RELEASE_ID,
            name="For Love of Country Launch",
            status="active",
            phase="pre_release",
            start_date=CAMPAIGN_START,
            end_date=CAMPAIGN_END,
            budget=None,  # Set when budget is decided
            goals={
                "pre_saves": "maximize",
                "release_week_streams": "track",
                "follower_growth": "track",
                "playlist_adds": "track",
                "bandcamp_sales": "track",
            },
            config={
                "platforms": PLATFORMS,
                "posting_cadence": "daily",
                "cta_destination": "https://linktr.ee/dbmusic26",
                "hashtags": [
                    "#ForLoveOfCountry", "#DrewBaird", "#CountryMusic",
                    "#NewCountry", "#CountryAlbum", "#NewMusic2026",
                    "#CountrySongwriter", "#Americana",
                ],
            },
        ))
        print(f"  Campaign: {CAMPAIGN_ID} (For Love of Country Launch)")

        # ── 8. Content calendar — pre-release (4 weeks) ───────────
        #
        # Today is ~2026-03-18.  Release is 2026-03-29.
        # Pre-release started 2026-03-01.  We're in Week 3.
        #
        # Week 1 (3/1–3/7):   Teaser & Announce         ← DONE
        # Week 2 (3/8–3/14):  Behind the Scenes         ← DONE
        # Week 3 (3/15–3/21): Single/Preview & Ramp     ← NOW
        # Week 4 (3/22–3/28): Final Countdown           ← NEXT
        # Release Day (3/29)                             ← 11 DAYS

        calendar_items: list[dict] = []

        # -- WEEK 1: Teaser & Announce (completed) --
        week1 = [
            ("Set up Instagram/TikTok/YouTube/Bandcamp/Linktree profiles",
             "Ensure all platform profiles are live with consistent branding",
             "setup", date(2026, 3, 1), time(10, 0), True),
            ("Upload album to DistroKid",
             "Album submitted for distribution to Apple Music & Spotify",
             "milestone", date(2026, 3, 2), time(12, 0), True),
            ("Post teaser — album announcement",
             "First public announcement: album title, release date, genre. "
             "Post across IG, TikTok, YouTube community tab",
             "content_drop", date(2026, 3, 3), time(18, 0), True),
            ("Create Linktree with pre-save links",
             "Set up linktr.ee/dbmusic26 as the canonical CTA destination",
             "setup", date(2026, 3, 5), time(10, 0), True),
            ("Post album artwork reveal",
             "Share the album cover art across all platforms",
             "content_drop", date(2026, 3, 7), time(18, 0), True),
        ]

        # -- WEEK 2: Behind the Scenes (completed) --
        week2 = [
            ("BTS — songwriting process",
             "Share a clip or story about writing one of the songs. "
             "What inspired it? Show the notebook, voice memos, etc.",
             "content_drop", date(2026, 3, 8), time(18, 0), True),
            ("BTS — recording sessions",
             "Studio footage or photos from recording. Show the instruments, "
             "the booth, the vibe.",
             "content_drop", date(2026, 3, 10), time(18, 0), True),
            ("Pre-save campaign push",
             "Dedicated post driving to Linktree for pre-save links. "
             "IG story with link sticker, TikTok with link in bio CTA.",
             "content_drop", date(2026, 3, 11), time(12, 0), True),
            ("BTS — mixing/mastering",
             "Show the final production process. Before/after audio clip "
             "if possible.",
             "content_drop", date(2026, 3, 13), time(18, 0), True),
            ("Engage with comments & DMs",
             "Respond to every comment and DM from the first two weeks. "
             "Build relationships, not just followers.",
             "engagement", date(2026, 3, 14), time(14, 0), True),
        ]

        # -- WEEK 3: Single/Preview & Ramp (IN PROGRESS) --
        week3 = [
            ("Post preview clip — lead track",
             "15-30 second clip of the strongest track. Hook-first. "
             "IG Reel + TikTok. CTA: 'Full album 3/29 — link in bio'",
             "content_drop", date(2026, 3, 15), time(18, 0), False),
            ("YouTube — track preview or lyric snippet",
             "YouTube Short with audio + lyrics on screen for the lead track. "
             "Or a longer 60-second preview.",
             "content_drop", date(2026, 3, 17), time(18, 0), False),
            ("Ramp posting frequency — daily content",
             "Start posting daily across platforms. Mix formats: "
             "reels, stories, static posts, shorts.",
             "milestone", date(2026, 3, 18), time(10, 0), False),
            ("TikTok — song snippet with trending format",
             "Use the catchiest hook in a trending TikTok format. "
             "Keep it authentic to the country genre.",
             "content_drop", date(2026, 3, 19), time(18, 0), False),
            ("Share fan reactions / early feedback",
             "If anyone has heard previews, share their genuine reactions. "
             "Quote tweets, DM screenshots (with permission), comments.",
             "engagement", date(2026, 3, 20), time(14, 0), False),
            ("Cross-promotion — tag collaborators",
             "If any musicians, producers, or songwriters contributed, "
             "tag them and share their involvement.",
             "content_drop", date(2026, 3, 21), time(18, 0), False),
        ]

        # -- WEEK 4: Final Countdown --
        week4 = [
            ("7-day countdown begins",
             "Daily countdown posts/stories. '7 days until For Love of Country.' "
             "New visual or snippet each day.",
             "content_drop", date(2026, 3, 22), time(10, 0), False),
            ("Share track listing",
             "Reveal the full track listing. Build anticipation for specific songs. "
             "Ask fans which title they're most excited about.",
             "content_drop", date(2026, 3, 23), time(18, 0), False),
            ("6 days — behind the album name",
             "Post about why the album is called 'For Love of Country.' "
             "What does it mean to you? Personal story.",
             "content_drop", date(2026, 3, 23), time(18, 0), False),
            ("5 days — snippet of track 2",
             "Second preview clip. Different energy from the first. "
             "Show range.",
             "content_drop", date(2026, 3, 24), time(18, 0), False),
            ("4 days — fan Q&A",
             "IG Stories Q&A or TikTok Live. Answer questions about the album, "
             "the process, what's next.",
             "engagement", date(2026, 3, 25), time(19, 0), False),
            ("3 days — final pre-save push",
             "Hard CTA push. 'Pre-save now so it's in your library Friday morning.' "
             "Linktree link in every post.",
             "content_drop", date(2026, 3, 26), time(12, 0), False),
            ("2 days — personal message to fans",
             "Authentic, unscripted video or written post. Thank people for following "
             "the journey. Say what this album means.",
             "content_drop", date(2026, 3, 27), time(18, 0), False),
            ("1 day — tomorrow it's here",
             "Final countdown post. Album artwork + 'Tomorrow.' "
             "Stories: 'Set your alarms.'",
             "content_drop", date(2026, 3, 28), time(20, 0), False),
            ("Prepare release-day content queue",
             "Pre-schedule or draft all release-day posts. Morning announcement, "
             "midday engagement, evening thank-you. Have everything ready to go.",
             "milestone", date(2026, 3, 28), time(22, 0), False),
        ]

        # -- RELEASE DAY (3/29) --
        release_day = [
            ("6 AM — Verify album is live",
             "Check Apple Music, Spotify, Bandcamp. Confirm all tracks play. "
             "Screenshot the album page.",
             "release", RELEASE_DATE, time(6, 0), False),
            ("8 AM — Morning announcement",
             "IT'S HERE. Post across all platforms with streaming/purchase links. "
             "CTA → Linktree. Pin to profile.",
             "release", RELEASE_DATE, time(8, 0), False),
            ("9 AM — Stories with link stickers",
             "IG Story + TikTok with direct link sticker to Linktree. "
             "Play a clip of the album.",
             "content_drop", RELEASE_DATE, time(9, 0), False),
            ("10 AM — Bandcamp push",
             "Dedicated Bandcamp post — 'Support directly on Bandcamp.' "
             "Bandcamp is where independent artists earn the most per sale.",
             "content_drop", RELEASE_DATE, time(10, 0), False),
            ("12 PM — Midday engagement",
             "Reply to every comment, share, and DM. Repost fan reactions. "
             "Show gratitude.",
             "engagement", RELEASE_DATE, time(12, 0), False),
            ("2 PM — Behind the scenes / making of",
             "Share a making-of clip or story. Deeper context now that "
             "people can listen to the full album.",
             "content_drop", RELEASE_DATE, time(14, 0), False),
            ("4 PM — Milestone stories",
             "Share any milestones: first stream count, first fan messages, "
             "playlist adds. Keep it genuine.",
             "engagement", RELEASE_DATE, time(16, 0), False),
            ("6 PM — Evening timezone push",
             "Second-wave announcement for West Coast / evening listeners. "
             "Fresh CTA with streaming links.",
             "content_drop", RELEASE_DATE, time(18, 0), False),
            ("8 PM — Thank-you post",
             "Genuine thank-you to everyone who listened, shared, bought. "
             "This is the relationship-building moment.",
             "engagement", RELEASE_DATE, time(20, 0), False),
            ("10 PM — Day 1 recap",
             "End-of-day story with first-day highlights. "
             "Set up anticipation for the post-release run.",
             "content_drop", RELEASE_DATE, time(22, 0), False),
        ]

        # -- POST-RELEASE WEEK 1 (3/30 – 4/5): Momentum --
        post_w1 = [
            ("Share streaming milestones",
             "Post any meaningful numbers — first 1K streams, playlist adds, "
             "Bandcamp sales. Celebrate with fans.",
             "content_drop", date(2026, 3, 30), time(18, 0), False),
            ("Repost fan reactions",
             "Share genuine fan posts, stories, reviews. Tag them. "
             "This is your best organic content.",
             "engagement", date(2026, 4, 1), time(14, 0), False),
            ("Submit to playlists",
             "Submit to Spotify editorial playlists, Apple Music, "
             "and user-curated playlists in the country genre.",
             "pr", date(2026, 4, 2), time(10, 0), False),
            ("Post lyric video or visualizer",
             "Alternative visual content for one of the deeper cuts. "
             "YouTube + IG Reel.",
             "content_drop", date(2026, 4, 3), time(18, 0), False),
            ("Engage with every comment",
             "Dedicated engagement session. Reply to all comments across "
             "all platforms from release week.",
             "engagement", date(2026, 4, 5), time(14, 0), False),
        ]

        # -- POST-RELEASE WEEK 2 (4/6 – 4/12): UGC & Challenges --
        post_w2 = [
            ("Launch TikTok challenge / trend",
             "Create a TikTok challenge using one of the catchier tracks. "
             "Make it easy for fans to participate.",
             "content_drop", date(2026, 4, 6), time(18, 0), False),
            ("Fan content hashtag campaign",
             "Encourage fans to post with #ForLoveOfCountry. "
             "Feature the best posts in your stories.",
             "engagement", date(2026, 4, 8), time(12, 0), False),
            ("Repost best UGC",
             "Share the best fan-generated content. Duets, covers, "
             "reactions, sing-alongs.",
             "engagement", date(2026, 4, 10), time(18, 0), False),
            ("Giveaway or contest",
             "Run a small giveaway — signed merch, personal video message, "
             "or unreleased acoustic recording.",
             "content_drop", date(2026, 4, 12), time(12, 0), False),
        ]

        # -- POST-RELEASE WEEK 3 (4/13 – 4/19): Deeper Cuts --
        post_w3 = [
            ("Spotlight a deep cut",
             "Pick a track that isn't the obvious single. Tell the story "
             "behind it. Why this song matters.",
             "content_drop", date(2026, 4, 13), time(18, 0), False),
            ("Acoustic / live version",
             "Record a simple acoustic version of one track. Phone camera, "
             "real setting. Authenticity over production.",
             "content_drop", date(2026, 4, 15), time(18, 0), False),
            ("Pitch to music blogs / press",
             "Send press kit to country music blogs, local press, "
             "and independent music publications.",
             "pr", date(2026, 4, 16), time(10, 0), False),
            ("Creator collaboration",
             "Reach out to country music content creators for reaction videos "
             "or feature posts. Genuine, not paid.",
             "engagement", date(2026, 4, 19), time(14, 0), False),
        ]

        # -- POST-RELEASE WEEK 4 (4/20 – 4/28): Recap --
        post_w4 = [
            ("30-day recap post",
             "Summary of the first month: streams, sales, fan moments, "
             "lessons learned. Share the real numbers.",
             "milestone", date(2026, 4, 20), time(18, 0), False),
            ("Thank-you message to fans",
             "Genuine, personal thank-you. Video preferred. "
             "Name specific fans or moments if possible.",
             "engagement", date(2026, 4, 22), time(18, 0), False),
            ("Analyze performance & document learnings",
             "Internal: review what worked, what didn't. Which platforms drove "
             "the most engagement? Best performing content formats?",
             "milestone", date(2026, 4, 25), time(10, 0), False),
            ("Tease what's next",
             "Plant the seed for what's coming. Don't over-commit — just enough "
             "to keep people following.",
             "content_drop", date(2026, 4, 27), time(18, 0), False),
            ("Update Linktree & evergreen links",
             "Transition Linktree from pre-save to streaming links. "
             "Update all bios with album links.",
             "setup", date(2026, 4, 28), time(10, 0), False),
        ]

        all_items = week1 + week2 + week3 + week4 + release_day + post_w1 + post_w2 + post_w3 + post_w4
        for title, desc, item_type, sched_date, sched_time, completed in all_items:
            session.add(CalendarItemModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                campaign_id=CAMPAIGN_ID,
                title=title,
                description=desc,
                item_type=item_type,
                scheduled_date=sched_date,
                scheduled_time=sched_time,
                is_completed=completed,
            ))
        print(f"  Calendar: {len(all_items)} items (pre-release through post-release day 30)")

        # ── 9. Redirect / smart links ─────────────────────────────
        redirects = [
            ("floc", "https://linktr.ee/dbmusic26", "Main CTA — For Love of Country"),
            ("bandcamp", "https://dbmusic26.bandcamp.com/", "Direct Bandcamp purchase"),
        ]
        for slug, target, note in redirects:
            session.add(RedirectModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                slug=slug,
                target_url=target,
                campaign_id=CAMPAIGN_ID,
                click_count=0,
            ))
            print(f"  Redirect: /r/{slug} -> {target}")

        # ── 10. Experiment — hook style A/B ───────────────────────
        session.add(ExperimentModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            campaign_id=CAMPAIGN_ID,
            name="Hook Style A/B — Storytelling vs. Direct",
            hypothesis=(
                "Storytelling hooks ('The song that almost didn't make the album...') "
                "will drive more engagement than direct hooks ('New album out 3/29') "
                "on TikTok and Reels."
            ),
            status="draft",
            variants=[
                {
                    "id": 0,
                    "label": "Storytelling",
                    "example": "I wrote this song at 2 AM after a phone call that changed everything...",
                },
                {
                    "id": 1,
                    "label": "Direct",
                    "example": "My debut album 'For Love of Country' drops March 29. Here's a preview.",
                },
            ],
            metrics_config={
                "primary_metric": "engagement_rate",
                "secondary_metrics": ["saves", "shares", "comments"],
                "min_sample_size": 200,
            },
        ))
        print("  Experiment: Hook Style A/B (storytelling vs. direct)")

        # ── 11. Billing plan ──────────────────────────────────────
        session.add(BillingPlanModel(
            id=PLAN_ID,
            name="Founder",
            tier="free",
            price_monthly=0.0,
            price_yearly=0.0,
            features=[
                "1 artist",
                "5 channels",
                "full analytics",
                "unlimited posts",
                "content calendar",
                "campaign workflows",
            ],
            limits={
                "max_artists": 1,
                "max_channels": 5,
                "max_posts_per_month": 999,
            },
            is_active=True,
        ))

        session.add(SubscriptionModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            plan_id=PLAN_ID,
            status="active",
            current_period_start=date(2026, 3, 1),
            current_period_end=date(2026, 12, 31),
        ))
        print("  Billing: Founder plan (unlimited)")

        # ── 12. Audit entry ───────────────────────────────────────
        session.add(AuditLogModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            action="campaign.created",
            entity_type="campaign",
            entity_id=CAMPAIGN_ID,
            changes={
                "name": "For Love of Country Launch",
                "status": "active",
                "release_date": "2026-03-29",
            },
            ip_address="127.0.0.1",
        ))

        # ── Commit ────────────────────────────────────────────────
        await session.commit()
        print(f"\nSeed complete — {len(all_items)} calendar items committed.")
        print(f"\n  Release date:  {RELEASE_DATE}")
        print(f"  Days until release: {(RELEASE_DATE - date.today()).days}")
        print(f"  Current phase: Week 3 — Single/Preview & Ramp")
        print(f"  Linktree CTA: https://linktr.ee/dbmusic26")

    await engine.dispose()


if __name__ == "__main__":
    print("=" * 60)
    print("  Amplify-OS — First Tenant Seed")
    print("  Drew Baird · For Love of Country · 2026-03-29")
    print("=" * 60)
    print()
    asyncio.run(seed())
