"""Tests for the calendar CSV import pipeline."""

import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Will test the service directly and via API


# ── Service-level tests ──────────────────────────────────────────

VALID_CSV = """date,time,type,title,description,platform,caption,asset_ref,cta,track_ref,release_ref,campaign_ref
2026-03-01,10:00 AM,post,Album Announcement,,instagram,Big news coming,artwork.jpg,Pre-save link in bio,,Midnight Frequencies,
2026-03-08,,milestone,Pre-save Launch,Launch pre-save links,,,,,,Midnight Frequencies,
2026-03-14,12:00 PM,release,Single Release,Lead single out,,,,Neon Pulse,Midnight Frequencies,
2026-03-29,12:00 AM,post,Album Out Now,,tiktok,IT'S HERE,release_reel.mp4,Link in bio,,Midnight Frequencies,
"""

INVALID_CSV = """date,time,type,title
,10:00 AM,post,Missing Date
2026-03-01,,invalid_type,Bad Type
2026-03-01,,post,
not-a-date,,post,Bad Date Format
"""

MIXED_CSV = """date,time,type,title,platform,caption
2026-03-01,10:00 AM,post,Good Row,instagram,Caption here
,,post,,
2026-03-05,2:00 PM,milestone,Another Good Row,,
"""


class TestCalendarImportService:
    """Test the import service directly."""

    @pytest.mark.asyncio
    async def test_import_valid_csv(self, db_session, seed_tenant):
        from app.services.calendar_import_service import CalendarImportService
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(VALID_CSV)

        assert result.total_rows == 4
        assert result.imported == 4
        assert result.skipped == 0
        assert len(result.errors) == 0
        assert len(result.created_ids) == 4

    @pytest.mark.asyncio
    async def test_import_invalid_csv_all_bad(self, db_session, seed_tenant):
        from app.services.calendar_import_service import CalendarImportService
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(INVALID_CSV)

        assert result.total_rows == 4
        assert result.imported == 0
        assert result.skipped == 4
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_import_mixed_csv(self, db_session, seed_tenant):
        from app.services.calendar_import_service import CalendarImportService
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(MIXED_CSV)

        assert result.imported == 2
        assert result.skipped == 1
        assert result.total_rows == 3

    @pytest.mark.asyncio
    async def test_import_with_campaign_id(self, db_session, seed_tenant, seed_artist):
        """When campaign_id is provided, all items should be linked to it."""
        from app.services.calendar_import_service import CalendarImportService
        from amplify.db.models.campaign import CampaignModel

        # Create a campaign
        campaign = CampaignModel(
            id=uuid.uuid4(),
            tenant_id=seed_tenant[0].id,
            artist_id=seed_artist.id,
            name="Test Campaign",
        )
        db_session.add(campaign)
        await db_session.flush()

        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(VALID_CSV, campaign_id=campaign.id)

        assert result.imported == 4

        # Verify items are linked to campaign
        from amplify.db.models.calendar_item import CalendarItemModel
        stmt = select(CalendarItemModel).where(CalendarItemModel.campaign_id == campaign.id)
        items = await db_session.execute(stmt)
        assert len(list(items.scalars().all())) == 4

    @pytest.mark.asyncio
    async def test_import_campaign_ref_resolution(self, db_session, seed_tenant, seed_artist):
        """campaign_ref column should resolve to campaign_id by name."""
        from app.services.calendar_import_service import CalendarImportService
        from amplify.db.models.campaign import CampaignModel

        campaign = CampaignModel(
            id=uuid.uuid4(),
            tenant_id=seed_tenant[0].id,
            artist_id=seed_artist.id,
            name="Midnight Frequencies Launch",
        )
        db_session.add(campaign)
        await db_session.flush()

        csv_with_ref = """date,type,title,campaign_ref
2026-03-01,post,Test Post,Midnight Frequencies Launch
"""
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv_with_ref)

        assert result.imported == 1
        from amplify.db.models.calendar_item import CalendarItemModel
        stmt = select(CalendarItemModel).where(CalendarItemModel.campaign_id == campaign.id)
        items = await db_session.execute(stmt)
        assert len(list(items.scalars().all())) == 1

    @pytest.mark.asyncio
    async def test_import_unknown_campaign_ref_warns(self, db_session, seed_tenant):
        """Unknown campaign_ref should produce a warning but still import."""
        from app.services.calendar_import_service import CalendarImportService

        csv = """date,type,title,campaign_ref
2026-03-01,post,Test Post,Nonexistent Campaign
"""
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv)

        assert result.imported == 1
        assert len(result.warnings) == 1
        assert "not found" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_import_date_formats(self, db_session, seed_tenant):
        """Multiple date formats should be accepted."""
        from app.services.calendar_import_service import CalendarImportService

        csv = """date,type,title
2026-03-01,post,ISO Format
03/01/2026,post,US Format
3/1/26,post,Short US Format
"""
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv)

        assert result.imported == 3
        assert result.skipped == 0

    @pytest.mark.asyncio
    async def test_import_time_formats(self, db_session, seed_tenant):
        """Multiple time formats should be accepted."""
        from app.services.calendar_import_service import CalendarImportService

        csv = """date,time,type,title
2026-03-01,10:00,post,24h Format
2026-03-01,2:00 PM,post,12h Format
2026-03-01,,post,No Time
"""
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv)

        assert result.imported == 3

    @pytest.mark.asyncio
    async def test_import_description_building(self, db_session, seed_tenant):
        """Description should be built from platform, caption, track_ref, asset_ref, cta."""
        from app.services.calendar_import_service import CalendarImportService
        from amplify.db.models.calendar_item import CalendarItemModel

        csv = """date,type,title,platform,caption,asset_ref,cta,track_ref
2026-03-01,post,Test,instagram,Check this out,photo.jpg,Link in bio,Neon Pulse
"""
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv)

        assert result.imported == 1
        stmt = select(CalendarItemModel).where(CalendarItemModel.id == result.created_ids[0])
        item_result = await db_session.execute(stmt)
        item = item_result.scalar_one()

        assert "[INSTAGRAM]" in item.description
        assert "Check this out" in item.description
        assert "Asset: photo.jpg" in item.description
        assert "CTA: Link in bio" in item.description
        assert "Track: Neon Pulse" in item.description

    @pytest.mark.asyncio
    async def test_import_missing_required_columns(self, db_session, seed_tenant):
        """CSV missing required columns should fail immediately."""
        from app.services.calendar_import_service import CalendarImportService

        csv = """platform,caption
instagram,Hello
"""
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv)

        assert result.imported == 0
        assert len(result.errors) == 1
        assert "Missing required columns" in result.errors[0].reason

    @pytest.mark.asyncio
    async def test_import_empty_csv(self, db_session, seed_tenant):
        """Empty CSV (header only) should return 0 rows."""
        from app.services.calendar_import_service import CalendarImportService

        csv = "date,type,title\n"
        service = CalendarImportService(db_session, seed_tenant[0].id)
        result = await service.import_csv(csv)

        assert result.total_rows == 0
        assert result.imported == 0

    @pytest.mark.asyncio
    async def test_import_success_property(self, db_session, seed_tenant):
        """success should be True only when all rows imported."""
        from app.services.calendar_import_service import CalendarImportService

        service = CalendarImportService(db_session, seed_tenant[0].id)

        result = await service.import_csv(VALID_CSV)
        assert result.success is True

        result2 = await service.import_csv(MIXED_CSV)
        assert result2.success is False  # Has skipped rows


# ── API endpoint tests ────────────────────────────────────────────

class TestCalendarImportAPI:
    """Test the POST /api/v1/calendar/import endpoint."""

    @pytest.mark.asyncio
    async def test_upload_csv_endpoint(self, client, seed_tenant):
        csv_content = "date,type,title\n2026-03-01,post,Test Post\n"

        response = await client.post(
            "/api/v1/calendar/import",
            files={"file": ("calendar.csv", csv_content.encode(), "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] == 1
        assert data["total_rows"] == 1

    @pytest.mark.asyncio
    async def test_upload_rejects_non_csv(self, client, seed_tenant):
        response = await client.post(
            "/api/v1/calendar/import",
            files={"file": ("data.txt", b"not csv", "text/plain")},
        )

        assert response.status_code == 400
        assert "csv" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upload_with_errors_returns_report(self, client, seed_tenant):
        csv_content = "date,type,title\n,,\n2026-03-01,post,Good Row\n"

        response = await client.post(
            "/api/v1/calendar/import",
            files={"file": ("calendar.csv", csv_content.encode(), "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] == 1
        assert data["skipped"] == 1
        assert len(data["errors"]) > 0

    @pytest.mark.asyncio
    async def test_upload_creates_audit_log(self, client, seed_tenant):
        csv_content = "date,type,title\n2026-03-01,post,Test\n"

        await client.post(
            "/api/v1/calendar/import",
            files={"file": ("calendar.csv", csv_content.encode(), "text/csv")},
        )

        # Check audit log
        response = await client.get("/api/v1/tenants/me/audit-log")
        assert response.status_code == 200
        logs = response.json()
        import_logs = [l for l in logs if l["action"] == "calendar.imported"]
        assert len(import_logs) >= 1
        assert import_logs[0]["changes"]["filename"] == "calendar.csv"

    @pytest.mark.asyncio
    async def test_imported_items_appear_in_list(self, client, seed_tenant):
        csv_content = "date,type,title\n2026-03-15,milestone,Test Milestone\n"

        await client.post(
            "/api/v1/calendar/import",
            files={"file": ("calendar.csv", csv_content.encode(), "text/csv")},
        )

        response = await client.get("/api/v1/calendar")
        assert response.status_code == 200
        items = response.json()
        assert any(i["title"] == "Test Milestone" for i in items)

    @pytest.mark.asyncio
    async def test_sample_csv_imports_successfully(self, client, seed_tenant):
        """The sample CSV from scripts/ should import without errors."""
        import os
        sample_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "scripts", "sample_calendar.csv"
        )
        if not os.path.exists(sample_path):
            pytest.skip("Sample CSV not found")

        with open(sample_path, "rb") as f:
            response = await client.post(
                "/api/v1/calendar/import",
                files={"file": ("sample_calendar.csv", f, "text/csv")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] > 20
        assert data["skipped"] == 0
