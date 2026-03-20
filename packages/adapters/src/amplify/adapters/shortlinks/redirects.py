"""Short link creation and click tracking via the redirects table."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.redirect import RedirectModel


class ShortlinkService:
    """Create short links and track click counts using the DB-backed redirect model."""

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        base_domain: str = "",
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.base_domain = base_domain.rstrip("/")

    async def create(
        self,
        long_url: str,
        slug: str | None = None,
        campaign_id: uuid.UUID | None = None,
    ) -> str:
        """Create a short link. Returns the short URL (e.g. https://domain/r/abc123)."""
        if slug is None:
            raw = f"{long_url}-{uuid.uuid4().hex[:8]}"
            slug = hashlib.sha256(raw.encode()).hexdigest()[:8]

        redirect = RedirectModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            slug=slug,
            target_url=long_url,
            campaign_id=campaign_id,
        )
        self.session.add(redirect)
        await self.session.flush()

        return f"{self.base_domain}/r/{slug}"

    async def resolve(self, slug: str) -> str | None:
        """Resolve a slug to its target URL, incrementing click count."""
        stmt = select(RedirectModel).where(RedirectModel.slug == slug)
        result = await self.session.execute(stmt)
        redirect = result.scalar_one_or_none()
        if redirect is None:
            return None

        await self.session.execute(
            update(RedirectModel)
            .where(RedirectModel.id == redirect.id)
            .values(click_count=RedirectModel.click_count + 1)
        )
        await self.session.flush()
        return redirect.target_url

    async def get_clicks(self, slug: str) -> int:
        """Get the total click count for a short link."""
        stmt = select(RedirectModel.click_count).where(RedirectModel.slug == slug)
        result = await self.session.execute(stmt)
        count = result.scalar_one_or_none()
        return count if count is not None else 0
