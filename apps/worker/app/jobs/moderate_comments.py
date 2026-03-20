"""Comment moderation job — screens auto-replies through the policy engine.

Payload schema:
{
    "platform": "instagram|tiktok|youtube",
    "artist_id": "uuid",
    "replies": [
        {
            "comment_id": "c123",
            "original_comment": "Love this track!",
            "reply_text": "Thank you so much!",
        },
        ...
    ]
}

Returns:
{
    "status": "ok",
    "comments_processed": N,
    "approved": [...],
    "blocked": [...],
    "needs_approval": [...]
}
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def moderate_comments(payload: dict) -> dict:
    """Screen auto-generated replies through the policy engine.

    Each reply is evaluated independently.  Blocked replies are
    dropped, approval-required replies are flagged, and clean
    replies are marked as approved for sending.
    """
    from amplify.core.policies.engine import ActionContext, Decision, create_default_engine

    replies = payload.get("replies", [])
    platform = payload.get("platform", "")
    artist_id = payload.get("artist_id", "")

    if not replies:
        return {"status": "ok", "comments_processed": 0, "approved": [], "blocked": [], "needs_approval": []}

    engine = create_default_engine()

    approved: list[dict] = []
    blocked: list[dict] = []
    needs_approval: list[dict] = []

    for reply in replies:
        reply_text = reply.get("reply_text", "")
        comment_id = reply.get("comment_id", "")

        ctx = ActionContext(
            action_type="reply",
            platform=platform,
            content=reply_text,
            artist_id=artist_id,
        )

        result = engine.evaluate(ctx)

        entry = {
            "comment_id": comment_id,
            "reply_text": reply_text,
            "policy_decision": result.final_decision.value,
        }

        if result.blocked:
            entry["reasons"] = result.blocking_reasons
            blocked.append(entry)
            logger.warning(
                "Reply to %s blocked: %s",
                comment_id,
                result.blocking_reasons,
            )
        elif result.needs_approval:
            entry["reasons"] = result.approval_reasons
            needs_approval.append(entry)
            logger.info(
                "Reply to %s needs approval: %s",
                comment_id,
                result.approval_reasons,
            )
        else:
            approved.append(entry)

    return {
        "status": "ok",
        "comments_processed": len(replies),
        "approved": approved,
        "blocked": blocked,
        "needs_approval": needs_approval,
    }
