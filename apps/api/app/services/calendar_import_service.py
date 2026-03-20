"""Content calendar CSV import pipeline.

Parses CSV rows, validates fields, maps to CalendarItem records,
and produces a detailed import report.

Expected CSV columns:
  date, time, type, title, description, platform, caption, asset_ref, cta, track_ref, release_ref, campaign_ref

Column mapping:
  - date -> scheduled_date (required, formats: YYYY-MM-DD, MM/DD/YYYY, M/D/YYYY)
  - time -> scheduled_time (optional, formats: HH:MM, H:MM AM/PM)
  - type -> item_type (required, must be one of: post, milestone, deadline, reminder, release, story, reel, email, ad)
  - title -> title (required, max 255 chars)
  - description -> description (optional, built from caption + CTA if not provided)
  - platform -> stored in description metadata as [platform] prefix
  - caption -> used as description if description column is empty
  - asset_ref -> stored in description as "Asset: {ref}"
  - cta -> appended to description as "CTA: {cta}"
  - track_ref -> stored in description as "Track: {ref}"
  - release_ref -> not mapped directly (for reference only, logged in report)
  - campaign_ref -> looked up against campaign name to resolve campaign_id
"""

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, time, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.calendar_item import CalendarItemModel
from amplify.db.models.campaign import CampaignModel


VALID_ITEM_TYPES = {"post", "milestone", "deadline", "reminder", "release", "story", "reel", "email", "ad"}

REQUIRED_COLUMNS = {"date", "type", "title"}


@dataclass
class RowError:
    row_number: int
    column: str
    value: str
    reason: str


@dataclass
class ImportResult:
    total_rows: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)
    created_ids: list[uuid.UUID] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.imported > 0 and self.skipped == 0

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "imported": self.imported,
            "skipped": self.skipped,
            "success": self.success,
            "errors": [
                {"row": e.row_number, "column": e.column, "value": e.value, "reason": e.reason}
                for e in self.errors
            ],
            "warnings": self.warnings,
            "created_ids": [str(i) for i in self.created_ids],
        }


class CalendarImportService:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id
        self._campaign_cache: dict[str, uuid.UUID] = {}

    async def import_csv(self, csv_content: str, campaign_id: uuid.UUID | None = None) -> ImportResult:
        """Parse and import CSV content into CalendarItem records."""
        result = ImportResult()

        reader = csv.DictReader(io.StringIO(csv_content))

        # Normalize header names (strip whitespace, lowercase)
        if reader.fieldnames:
            reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]

        # Check required columns
        if reader.fieldnames:
            missing = REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                result.errors.append(RowError(0, "header", "", f"Missing required columns: {', '.join(missing)}"))
                return result

        rows = list(reader)
        result.total_rows = len(rows)

        for i, row in enumerate(rows, start=2):  # Row 2 = first data row (1 = header)
            row = {k.strip().lower(): v.strip() if v else "" for k, v in row.items() if k}

            row_errors = self._validate_row(i, row)
            if row_errors:
                result.errors.extend(row_errors)
                result.skipped += 1
                continue

            # Parse fields
            scheduled_date = self._parse_date(row.get("date", ""))
            scheduled_time = self._parse_time(row.get("time", ""))
            item_type = row.get("type", "").lower().strip()
            title = row.get("title", "").strip()[:255]

            # Build description from available fields
            description = self._build_description(row)

            # Resolve campaign_id
            resolved_campaign_id = campaign_id
            campaign_ref = row.get("campaign_ref", "").strip()
            if campaign_ref and not resolved_campaign_id:
                resolved_campaign_id = await self._resolve_campaign(campaign_ref)
                if resolved_campaign_id is None:
                    result.warnings.append(f"Row {i}: campaign_ref '{campaign_ref}' not found, imported without campaign link")

            # Create the record
            item = CalendarItemModel(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                campaign_id=resolved_campaign_id,
                title=title,
                description=description,
                item_type=item_type,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                is_completed=False,
            )
            self.session.add(item)
            result.created_ids.append(item.id)
            result.imported += 1

        if result.imported > 0:
            await self.session.flush()

        return result

    def _validate_row(self, row_num: int, row: dict) -> list[RowError]:
        errors = []

        # date required
        date_val = row.get("date", "").strip()
        if not date_val:
            errors.append(RowError(row_num, "date", "", "Date is required"))
        elif self._parse_date(date_val) is None:
            errors.append(RowError(row_num, "date", date_val, "Invalid date format (expected YYYY-MM-DD or MM/DD/YYYY)"))

        # type required and valid
        type_val = row.get("type", "").strip().lower()
        if not type_val:
            errors.append(RowError(row_num, "type", "", "Type is required"))
        elif type_val not in VALID_ITEM_TYPES:
            errors.append(RowError(row_num, "type", type_val, f"Invalid type. Must be one of: {', '.join(sorted(VALID_ITEM_TYPES))}"))

        # title required
        title_val = row.get("title", "").strip()
        if not title_val:
            errors.append(RowError(row_num, "title", "", "Title is required"))
        elif len(title_val) > 255:
            errors.append(RowError(row_num, "title", title_val[:50] + "...", "Title exceeds 255 characters"))

        # time optional but validate format if present
        time_val = row.get("time", "").strip()
        if time_val and self._parse_time(time_val) is None:
            errors.append(RowError(row_num, "time", time_val, "Invalid time format (expected HH:MM or H:MM AM/PM)"))

        return errors

    def _parse_date(self, value: str) -> date | None:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _parse_time(self, value: str) -> time | None:
        if not value.strip():
            return None
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), fmt).time()
            except ValueError:
                continue
        return None

    def _build_description(self, row: dict) -> str:
        parts = []

        platform = row.get("platform", "").strip()
        if platform:
            parts.append(f"[{platform.upper()}]")

        # Use description if provided, otherwise use caption
        desc = row.get("description", "").strip()
        caption = row.get("caption", "").strip()
        if desc:
            parts.append(desc)
        elif caption:
            parts.append(caption)

        track_ref = row.get("track_ref", "").strip()
        if track_ref:
            parts.append(f"Track: {track_ref}")

        asset_ref = row.get("asset_ref", "").strip()
        if asset_ref:
            parts.append(f"Asset: {asset_ref}")

        cta = row.get("cta", "").strip()
        if cta:
            parts.append(f"CTA: {cta}")

        return "\n".join(parts) if parts else ""

    async def _resolve_campaign(self, campaign_ref: str) -> uuid.UUID | None:
        if campaign_ref in self._campaign_cache:
            return self._campaign_cache[campaign_ref]

        stmt = select(CampaignModel).where(
            CampaignModel.tenant_id == self.tenant_id,
            CampaignModel.name == campaign_ref,
        )
        result = await self.session.execute(stmt)
        campaign = result.scalar_one_or_none()

        if campaign:
            self._campaign_cache[campaign_ref] = campaign.id
            return campaign.id

        return None
