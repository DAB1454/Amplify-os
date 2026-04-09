"""Asset usage tracking — called after a successful publish.

The planner and content pipeline rank assets partly on whether they
have a track record. "Untested" assets have never been published
successfully; "proven" assets have been used enough times to trust
the signal. We don't need exact metrics here — just a counter and a
last-used timestamp that the ranker can key off.

Auto-promotion rule: after N successful publishes, flip an untested
asset to proven. N is env-tunable (``AMPLIFY_PROVEN_AFTER_USES``,
default 3) so we can tighten or loosen the bar without a deploy.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.asset import AssetModel
from amplify.db.models.post import PostModel

logger = logging.getLogger(__name__)


def _proven_threshold() -> int:
    try:
        return max(1, int(os.environ.get("AMPLIFY_PROVEN_AFTER_USES", "3") or 3))
    except ValueError:
        return 3


async def record_asset_usage_for_post(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    post: PostModel,
) -> None:
    """Increment usage counters for every asset attached to ``post``.

    Assets are looked up by ``file_url`` match within the tenant so we
    don't need the post model to carry asset IDs explicitly.
    """
    media_urls = list(getattr(post, "media_urls", None) or [])
    if not media_urls:
        return

    result = await db.execute(
        select(AssetModel).where(
            AssetModel.tenant_id == tenant_id,
            AssetModel.file_url.in_(media_urls),
        )
    )
    assets = list(result.scalars().all())
    if not assets:
        return

    threshold = _proven_threshold()
    now = datetime.utcnow()

    for asset in assets:
        asset.uses_count = (asset.uses_count or 0) + 1
        asset.last_used_at = now
        if (
            asset.test_status == "untested"
            and asset.uses_count >= threshold
        ):
            asset.test_status = "proven"
            logger.info(
                "Asset %s auto-promoted to proven after %s uses",
                asset.id,
                asset.uses_count,
            )

    await db.flush()
