"""Seed database with sample data for local development.

Creates:
- 1 Tenant ("Local Studio")
- 1 User (admin@amplify.local)
- 1 Membership (owner)
- 1 Artist ("Luna Vega" - indie electronic)
- 1 Release (album "Midnight Frequencies" with 8 tracks, releasing 2026-04-15)
- 3 ChannelConnections (Instagram, TikTok, YouTube - without real tokens)
- 1 Campaign ("Midnight Frequencies Launch" - pre_release phase)
- 5 CalendarItems (key dates in the campaign)
- 3 Assets (album artwork, teaser video, press photo)
- 2 AssetVariants (Instagram square crop, TikTok vertical crop of artwork)
- 2 Posts (one draft Instagram post, one scheduled TikTok)
- 1 Approval (pending for the Instagram post)
- 1 Experiment (A/B test on caption style)
- 5 MetricEvents (sample engagement data)
- 3 DailyMetrics (follower counts)
- 1 AuditLog entry
- 1 BillingPlan (free tier)
- 1 Subscription

Usage:
    python scripts/seed.py

Requires DATABASE_URL environment variable or uses docker-compose default.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

try:
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.ext.asyncio import async_sessionmaker
except ImportError:
    print("ERROR: SQLAlchemy is not installed.")
    print("  pip install sqlalchemy[asyncio] asyncpg")
    raise SystemExit(1)

from amplify.db.base import Base
from amplify.db.models import (
    ApprovalModel,
    ArtistModel,
    AssetModel,
    AssetVariantModel,
    AuditLogModel,
    BillingPlanModel,
    CalendarItemModel,
    CampaignModel,
    ChannelConnectionModel,
    DailyMetricModel,
    ExperimentModel,
    MembershipModel,
    MetricEventModel,
    PostModel,
    ReleaseModel,
    SubscriptionModel,
    TenantModel,
    TrackModel,
    UserModel,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://amplify:amplify@localhost:5432/amplify_os",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def seed() -> None:
    """Seed the database with comprehensive sample data."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as session:
        # --- Idempotency check ---------------------------------------------------
        result = await session.execute(
            select(TenantModel).where(TenantModel.slug == "local-studio")
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            print("Seed data already exists (tenant 'local-studio' found). Skipping.")
            await engine.dispose()
            return

        # -----------------------------------------------------------------------
        # IDs — generated up-front so we can cross-reference
        # -----------------------------------------------------------------------
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        membership_id = uuid.uuid4()
        artist_id = uuid.uuid4()
        release_id = uuid.uuid4()
        campaign_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        subscription_id = uuid.uuid4()

        channel_ig_id = uuid.uuid4()
        channel_tt_id = uuid.uuid4()
        channel_yt_id = uuid.uuid4()

        asset_artwork_id = uuid.uuid4()
        asset_teaser_id = uuid.uuid4()
        asset_press_id = uuid.uuid4()

        variant_ig_id = uuid.uuid4()
        variant_tt_id = uuid.uuid4()

        post_ig_id = uuid.uuid4()
        post_tt_id = uuid.uuid4()

        approval_id = uuid.uuid4()
        experiment_id = uuid.uuid4()

        now = _now()
        release_date = date(2026, 4, 15)

        # -----------------------------------------------------------------------
        # 1. Tenant
        # -----------------------------------------------------------------------
        tenant = TenantModel(
            id=tenant_id,
            name="Local Studio",
            slug="local-studio",
            plan_tier="free",
            settings={"timezone": "America/New_York", "brand_color": "#6C5CE7"},
            is_active=True,
        )
        session.add(tenant)
        print(f"  Tenant: {tenant_id} (Local Studio)")

        # -----------------------------------------------------------------------
        # 2. User
        # -----------------------------------------------------------------------
        user = UserModel(
            id=user_id,
            email="admin@amplify.local",
            hashed_password="$2b$12$placeholder_hash_not_a_real_password",
            display_name="Admin User",
            is_active=True,
        )
        session.add(user)
        print(f"  User: {user_id} (admin@amplify.local)")

        # -----------------------------------------------------------------------
        # 3. Membership (owner)
        # -----------------------------------------------------------------------
        membership = MembershipModel(
            id=membership_id,
            user_id=user_id,
            tenant_id=tenant_id,
            role="owner",
        )
        session.add(membership)
        print(f"  Membership: {membership_id} (owner)")

        # -----------------------------------------------------------------------
        # 4. Artist — Luna Vega (indie electronic)
        # -----------------------------------------------------------------------
        artist = ArtistModel(
            id=artist_id,
            tenant_id=tenant_id,
            name="Luna Vega",
            slug="luna-vega",
            bio=(
                "Luna Vega is an indie electronic artist blending dreamy "
                "synthscapes with pulsing bass lines. Born in Brooklyn, she "
                "draws from late-night city energy and 80s synth-pop to craft "
                "music that feels both nostalgic and futuristic."
            ),
            genre="Indie Electronic",
            image_url="https://cdn.amplify.local/artists/luna-vega/profile.jpg",
            spotify_id="4ZGK4hkNX6pilPZyivAVY7",
            social_links={
                "instagram": "https://instagram.com/lunavegamusic",
                "tiktok": "https://tiktok.com/@lunavega",
                "youtube": "https://youtube.com/@lunavegaofficial",
                "spotify": "https://open.spotify.com/artist/4ZGK4hkNX6pilPZyivAVY7",
            },
        )
        session.add(artist)
        print(f"  Artist: {artist_id} (Luna Vega)")

        # -----------------------------------------------------------------------
        # 5. Release — "Midnight Frequencies" (album, 8 tracks)
        # -----------------------------------------------------------------------
        release = ReleaseModel(
            id=release_id,
            tenant_id=tenant_id,
            artist_id=artist_id,
            title="Midnight Frequencies",
            release_type="album",
            status="scheduled",
            release_date=release_date,
            upc="196588274519",
            artwork_url="https://cdn.amplify.local/releases/midnight-frequencies/cover.jpg",
            metadata_={
                "label": "Neon Drift Records",
                "copyright": "2026 Neon Drift Records",
                "genre_tags": ["indie electronic", "synthwave", "dream pop"],
                "distributor": "DistroKid",
            },
        )
        session.add(release)
        print(f"  Release: {release_id} (Midnight Frequencies)")

        # Tracks ---------------------------------------------------------------
        tracks_data = [
            ("Signal Lost", 1, 245, "USRC12600001", False),
            ("Midnight Frequencies", 2, 312, "USRC12600002", True),
            ("Neon Rain", 3, 198, "USRC12600003", False),
            ("Velvet Static", 4, 267, "USRC12600004", False),
            ("Phantom Drive", 5, 224, "USRC12600005", True),
            ("Glass City", 6, 289, "USRC12600006", False),
            ("Waveform", 7, 203, "USRC12600007", False),
            ("Last Transmission", 8, 356, "USRC12600008", False),
        ]
        for title, num, dur, isrc, is_single in tracks_data:
            track = TrackModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                release_id=release_id,
                title=title,
                track_number=num,
                duration_seconds=dur,
                isrc=isrc,
                is_single=is_single,
            )
            session.add(track)
            print(f"    Track {num}: {title} ({dur}s)")

        # -----------------------------------------------------------------------
        # 6. Channel connections (no real tokens)
        # -----------------------------------------------------------------------
        channels = [
            (channel_ig_id, "instagram", "lunavegamusic", "Luna Vega"),
            (channel_tt_id, "tiktok", "lunavega", "Luna Vega"),
            (channel_yt_id, "youtube", "UCx_example", "Luna Vega Official"),
        ]
        for ch_id, platform, acct_id, display in channels:
            channel = ChannelConnectionModel(
                id=ch_id,
                tenant_id=tenant_id,
                artist_id=artist_id,
                platform=platform,
                platform_account_id=acct_id,
                display_name=display,
                access_token="seed_token_not_real",
                refresh_token="seed_refresh_not_real",
                token_expires_at=now + timedelta(days=60),
                is_active=True,
                settings={},
            )
            session.add(channel)
            print(f"  Channel: {platform} ({display})")

        # -----------------------------------------------------------------------
        # 7. Campaign — "Midnight Frequencies Launch"
        # -----------------------------------------------------------------------
        campaign = CampaignModel(
            id=campaign_id,
            tenant_id=tenant_id,
            artist_id=artist_id,
            release_id=release_id,
            name="Midnight Frequencies Launch",
            status="active",
            phase="pre_release",
            start_date=date(2026, 3, 15),
            end_date=date(2026, 5, 15),
            budget=2500.00,
            goals={
                "target_streams_first_week": 50000,
                "target_playlist_adds": 25,
                "target_follower_growth": 2000,
            },
            config={
                "platforms": ["instagram", "tiktok", "youtube"],
                "posting_cadence": "daily",
            },
        )
        session.add(campaign)
        print(f"  Campaign: {campaign_id} (Midnight Frequencies Launch)")

        # -----------------------------------------------------------------------
        # 8. Calendar items (5 key dates)
        # -----------------------------------------------------------------------
        cal_items = [
            (
                "Album Artwork Finalized",
                "Approve final album artwork with designer",
                "milestone",
                date(2026, 3, 20),
                time(10, 0),
                True,
            ),
            (
                "Teaser Video Drop",
                "Post 15-second teaser clip across all platforms",
                "content_drop",
                date(2026, 3, 28),
                time(18, 0),
                False,
            ),
            (
                "Lead Single Release — Phantom Drive",
                "Release lead single on all DSPs; push social content",
                "release",
                date(2026, 4, 1),
                time(0, 0),
                False,
            ),
            (
                "Press Kit Distribution",
                "Send press kits to blogs, playlists, and radio",
                "pr",
                date(2026, 4, 5),
                time(9, 0),
                False,
            ),
            (
                "Album Release Day",
                "Full album drops; launch day social blitz",
                "release",
                release_date,
                time(0, 0),
                False,
            ),
        ]
        for title, desc, item_type, sched_date, sched_time, completed in cal_items:
            cal = CalendarItemModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                title=title,
                description=desc,
                item_type=item_type,
                scheduled_date=sched_date,
                scheduled_time=sched_time,
                is_completed=completed,
            )
            session.add(cal)
            print(f"  CalendarItem: {title} ({sched_date})")

        # -----------------------------------------------------------------------
        # 9. Assets (3 assets, 2 variants)
        # -----------------------------------------------------------------------
        asset_artwork = AssetModel(
            id=asset_artwork_id,
            tenant_id=tenant_id,
            release_id=release_id,
            campaign_id=campaign_id,
            asset_type="image",
            name="Midnight Frequencies — Album Artwork",
            file_url="https://cdn.amplify.local/assets/midnight-freq-artwork.png",
            file_size_bytes=4_200_000,
            mime_type="image/png",
            metadata_={"width": 3000, "height": 3000, "dpi": 300},
        )
        session.add(asset_artwork)

        asset_teaser = AssetModel(
            id=asset_teaser_id,
            tenant_id=tenant_id,
            release_id=release_id,
            campaign_id=campaign_id,
            asset_type="video",
            name="Midnight Frequencies — Teaser Video",
            file_url="https://cdn.amplify.local/assets/midnight-freq-teaser.mp4",
            file_size_bytes=28_500_000,
            mime_type="video/mp4",
            metadata_={"width": 1920, "height": 1080, "duration_seconds": 15},
        )
        session.add(asset_teaser)

        asset_press = AssetModel(
            id=asset_press_id,
            tenant_id=tenant_id,
            release_id=release_id,
            campaign_id=None,
            asset_type="image",
            name="Luna Vega — Press Photo",
            file_url="https://cdn.amplify.local/assets/luna-vega-press.jpg",
            file_size_bytes=3_100_000,
            mime_type="image/jpeg",
            metadata_={"width": 4000, "height": 2667, "photographer": "Mia Chen"},
        )
        session.add(asset_press)
        print("  Assets: artwork, teaser video, press photo")

        # Variants
        variant_ig = AssetVariantModel(
            id=variant_ig_id,
            asset_id=asset_artwork_id,
            platform="instagram",
            file_url="https://cdn.amplify.local/assets/midnight-freq-artwork-ig.jpg",
            width=1080,
            height=1080,
            format="jpeg",
        )
        session.add(variant_ig)

        variant_tt = AssetVariantModel(
            id=variant_tt_id,
            asset_id=asset_artwork_id,
            platform="tiktok",
            file_url="https://cdn.amplify.local/assets/midnight-freq-artwork-tt.jpg",
            width=1080,
            height=1920,
            format="jpeg",
        )
        session.add(variant_tt)
        print("  AssetVariants: Instagram square crop, TikTok vertical crop")

        # -----------------------------------------------------------------------
        # 10. Posts (1 draft Instagram, 1 scheduled TikTok)
        # -----------------------------------------------------------------------
        post_ig = PostModel(
            id=post_ig_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            channel_id=channel_ig_id,
            platform="instagram",
            status="draft",
            content_text=(
                "Something's coming. 04.15.26 \u2728\n\n"
                "#MidnightFrequencies #NewMusic #IndieElectronic #LunaVega"
            ),
            media_urls=[
                "https://cdn.amplify.local/assets/midnight-freq-artwork-ig.jpg"
            ],
        )
        session.add(post_ig)

        post_tt = PostModel(
            id=post_tt_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            channel_id=channel_tt_id,
            platform="tiktok",
            status="scheduled",
            content_text=(
                "POV: you find the frequency \ud83d\udd0a "
                "#MidnightFrequencies #newmusic #indieelectronic"
            ),
            media_urls=[
                "https://cdn.amplify.local/assets/midnight-freq-teaser.mp4"
            ],
            scheduled_at=datetime(2026, 3, 28, 18, 0, 0, tzinfo=timezone.utc),
        )
        session.add(post_tt)
        print("  Posts: 1 draft (Instagram), 1 scheduled (TikTok)")

        # -----------------------------------------------------------------------
        # 11. Approval (pending for Instagram post)
        # -----------------------------------------------------------------------
        approval = ApprovalModel(
            id=approval_id,
            tenant_id=tenant_id,
            post_id=post_ig_id,
            asset_id=None,
            requested_by=user_id,
            reviewed_by=None,
            status="pending",
            notes="Please review copy and artwork before we go live.",
        )
        session.add(approval)
        print(f"  Approval: {approval_id} (pending)")

        # -----------------------------------------------------------------------
        # 12. Experiment (A/B caption style)
        # -----------------------------------------------------------------------
        experiment = ExperimentModel(
            id=experiment_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            name="Caption Style A/B Test",
            hypothesis=(
                "Mysterious/cryptic captions will drive more engagement "
                "than descriptive captions for pre-release teaser posts."
            ),
            status="draft",
            variants=[
                {
                    "id": 0,
                    "label": "Cryptic",
                    "caption": "Something's coming. 04.15.26 \u2728",
                },
                {
                    "id": 1,
                    "label": "Descriptive",
                    "caption": (
                        "My new album 'Midnight Frequencies' drops April 15! "
                        "8 tracks of dreamy electronic vibes."
                    ),
                },
            ],
            metrics_config={
                "primary_metric": "engagement_rate",
                "secondary_metrics": ["saves", "shares"],
                "min_sample_size": 500,
            },
        )
        session.add(experiment)
        print(f"  Experiment: {experiment_id} (Caption Style A/B)")

        # -----------------------------------------------------------------------
        # 13. Metric events (5 sample engagement data points)
        # -----------------------------------------------------------------------
        metric_events_data = [
            ("instagram", "like", "post", post_ig_id, 1.0, now - timedelta(hours=5)),
            ("instagram", "comment", "post", post_ig_id, 1.0, now - timedelta(hours=4)),
            ("instagram", "save", "post", post_ig_id, 1.0, now - timedelta(hours=3)),
            ("tiktok", "view", "post", post_tt_id, 1.0, now - timedelta(hours=2)),
            ("tiktok", "share", "post", post_tt_id, 1.0, now - timedelta(hours=1)),
        ]
        for source, evt_type, ent_type, ent_id, value, occurred in metric_events_data:
            me = MetricEventModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                source=source,
                event_type=evt_type,
                entity_type=ent_type,
                entity_id=ent_id,
                value=value,
                metadata_={},
                occurred_at=occurred,
            )
            session.add(me)
        print("  MetricEvents: 5 engagement events")

        # -----------------------------------------------------------------------
        # 14. Daily metrics (3 follower count snapshots)
        # -----------------------------------------------------------------------
        daily_data = [
            ("instagram", "follower_count", date(2026, 3, 16), 12450.0, 12380.0),
            ("instagram", "follower_count", date(2026, 3, 17), 12510.0, 12450.0),
            ("tiktok", "follower_count", date(2026, 3, 17), 8730.0, 8690.0),
        ]
        for source, metric_name, d, value, prev in daily_data:
            dm = DailyMetricModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                source=source,
                metric_name=metric_name,
                entity_type="artist",
                entity_id=artist_id,
                date=d,
                value=value,
                previous_value=prev,
            )
            session.add(dm)
        print("  DailyMetrics: 3 follower snapshots")

        # -----------------------------------------------------------------------
        # 15. Audit log entry
        # -----------------------------------------------------------------------
        audit = AuditLogModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            action="campaign.created",
            entity_type="campaign",
            entity_id=campaign_id,
            changes={"name": "Midnight Frequencies Launch", "status": "active"},
            ip_address="127.0.0.1",
        )
        session.add(audit)
        print("  AuditLog: 1 entry (campaign.created)")

        # -----------------------------------------------------------------------
        # 16. Billing plan (free tier)
        # -----------------------------------------------------------------------
        plan = BillingPlanModel(
            id=plan_id,
            name="Free",
            tier="free",
            price_monthly=0.0,
            price_yearly=0.0,
            features=["1 artist", "3 channels", "basic analytics"],
            limits={
                "max_artists": 1,
                "max_channels": 3,
                "max_posts_per_month": 30,
            },
            is_active=True,
        )
        session.add(plan)
        print(f"  BillingPlan: {plan_id} (Free)")

        # -----------------------------------------------------------------------
        # 17. Subscription
        # -----------------------------------------------------------------------
        subscription = SubscriptionModel(
            id=subscription_id,
            tenant_id=tenant_id,
            plan_id=plan_id,
            status="active",
            current_period_start=date(2026, 3, 1),
            current_period_end=date(2026, 4, 1),
        )
        session.add(subscription)
        print(f"  Subscription: {subscription_id} (active)")

        # -----------------------------------------------------------------------
        # Commit everything
        # -----------------------------------------------------------------------
        await session.commit()
        print("\nSeed complete — all data committed.")

    await engine.dispose()


if __name__ == "__main__":
    print("Seeding local development database...\n")
    asyncio.run(seed())
