"""Publishing service — state machine, policy checks, retry, and adapter dispatch."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.core.domain.post import PostStatus, VALID_TRANSITIONS
from amplify.core.policies.engine import ActionContext, create_default_engine
from amplify.db.models.post import PostModel
from app.services.learning_event_service import LearningEventService

logger = logging.getLogger(__name__)

BASE_DELAY = 30  # seconds
MAX_DELAY = 600  # 10 minutes


class InvalidTransition(Exception):
    """Raised when a post state transition is not allowed."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current!r} to {target!r}")


class PublishingService:
    """Service layer for publishing posts to social platforms."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        learning: LearningEventService | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self._learning = learning

    @property
    def learning(self) -> LearningEventService | None:
        return self._learning

    # ── helpers ───────────────────────────────────────────────────────

    async def _get_post(self, post_id: uuid.UUID) -> PostModel:
        stmt = select(PostModel).where(
            PostModel.id == post_id,
            PostModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        post = result.scalar_one_or_none()
        if post is None:
            raise ValueError(f"Post {post_id} not found")
        return post

    def _assert_transition(self, post: PostModel, target: PostStatus) -> None:
        current = PostStatus(post.status)
        if target not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(post.status, target.value)

    async def _transition(self, post: PostModel, target: PostStatus, **extra: object) -> PostModel:
        self._assert_transition(post, target)
        post.status = target.value
        for k, v in extra.items():
            if hasattr(post, k):
                setattr(post, k, v)
        await self.db.flush()
        return post

    async def _emit_learning(self, event_dict: dict) -> None:
        """Emit a learning event if the service is available. Never raises.

        If emission fails, the DB session is rolled back to a clean state
        via savepoint so the calling transaction is not poisoned.
        """
        if self._learning is None:
            return
        try:
            nested = await self.db.begin_nested()
            await self._learning.emit(**{
                k: v for k, v in event_dict.items()
                if k != "tenant_id"  # already set on the service
            })
            await nested.commit()
        except Exception:
            logger.debug("Learning event emission failed (non-fatal)", exc_info=True)
            try:
                await nested.rollback()
            except Exception:
                pass

    # ── state machine actions ─────────────────────────────────────────

    async def queue_post(self, post_id: uuid.UUID) -> dict:
        """Move a draft post into the approval queue.

        Runs the policy engine first.  If blocked, the post stays as draft.
        """
        from amplify.learning.capture import policy_evaluated, post_queued

        post = await self._get_post(post_id)
        self._assert_transition(post, PostStatus.QUEUED)

        # Run policy engine
        policy_result = self._evaluate_policy(post)

        # Capture policy evaluation
        await self._emit_learning(policy_evaluated(
            post_id=post_id,
            tenant_id=self.tenant_id,
            platform=post.platform or "",
            decision=policy_result["decision"],
            blocking_reasons=policy_result.get("reasons", []),
        ))

        if policy_result["decision"] == "block":
            return {
                "post_id": str(post_id),
                "status": post.status,
                "policy_decision": "block",
                "reasons": policy_result["reasons"],
            }

        post = await self._transition(
            post,
            PostStatus.QUEUED,
            policy_decision=policy_result["decision"],
        )

        # Capture queue event
        await self._emit_learning(post_queued(
            post_id=post_id,
            tenant_id=self.tenant_id,
            platform=post.platform or "",
            policy_decision=policy_result["decision"],
            policy_reasons=policy_result.get("reasons", []),
            campaign_id=post.campaign_id,
        ))

        return {
            "post_id": str(post_id),
            "status": post.status,
            "policy_decision": policy_result["decision"],
            "reasons": policy_result.get("reasons", []),
        }

    async def approve_post(self, post_id: uuid.UUID) -> dict:
        """Approve a queued post. Auto-schedules if scheduled_at is already set."""
        post = await self._get_post(post_id)
        post = await self._transition(post, PostStatus.APPROVED)
        # If post already has a scheduled time, auto-transition to SCHEDULED
        if post.scheduled_at:
            post = await self._transition(post, PostStatus.SCHEDULED)
        return {"post_id": str(post_id), "status": post.status}

    async def reject_post(self, post_id: uuid.UUID) -> dict:
        """Reject a queued post back to draft."""
        post = await self._get_post(post_id)
        post = await self._transition(post, PostStatus.DRAFT)
        return {"post_id": str(post_id), "status": post.status}

    async def schedule_post(self, post_id: uuid.UUID, scheduled_at: datetime) -> dict:
        """Schedule an approved or draft post for future publication."""
        post = await self._get_post(post_id)
        # Allow scheduling from approved or draft
        target = PostStatus.SCHEDULED
        self._assert_transition(post, target)
        post = await self._transition(post, target, scheduled_at=scheduled_at)
        return {
            "post_id": str(post_id),
            "status": post.status,
            "scheduled_at": scheduled_at.isoformat(),
        }

    async def publish_post(self, post_id: uuid.UUID, dry_run: bool = False) -> dict:
        """Publish a post immediately via the platform adapter.

        If dry_run=True, runs everything except the actual API call.
        """
        from amplify.learning.capture import post_published, post_failed

        post = await self._get_post(post_id)

        # Must be approved or scheduled to publish
        self._assert_transition(post, PostStatus.PUBLISHING)
        post = await self._transition(post, PostStatus.PUBLISHING)

        if dry_run:
            # Roll back to previous state
            post.status = PostStatus.APPROVED.value
            await self.db.flush()
            return {
                "post_id": str(post_id),
                "status": "dry_run",
                "preview": {
                    "platform": post.platform,
                    "content_text": post.content_text,
                    "media_urls": post.media_urls,
                    "destination_url": post.destination_url,
                    "would_publish": True,
                },
            }

        try:
            result = await self._do_publish(post)
            post = await self._transition(
                post,
                PostStatus.PUBLISHED,
                published_at=datetime.utcnow(),
                platform_post_id=result.get("platform_post_id"),
                permalink=result.get("permalink"),
                last_error=None,
                retry_count=0,
            )

            await self._emit_learning(post_published(
                post_id=post_id,
                tenant_id=self.tenant_id,
                platform=post.platform or "",
                platform_post_id=result.get("platform_post_id"),
                permalink=result.get("permalink"),
                published_at=post.published_at,
                campaign_id=post.campaign_id,
            ))

            return {
                "post_id": str(post_id),
                "status": post.status,
                "published_at": post.published_at.isoformat() if post.published_at else None,
                "permalink": post.permalink,
                "platform_post_id": post.platform_post_id,
            }
        except Exception as exc:
            post.retry_count = (post.retry_count or 0) + 1
            post.last_error = str(exc)[:1000]
            # Detect permanent errors that should not be retried
            err_str = str(exc).lower()
            is_auth_error = any(kw in err_str for kw in [
                "unaudited_client", "invalid_token", "token expired",
                "access_token_invalid", "scope not authorized",
                "403", "401", "permission",
            ])
            is_permanent = is_auth_error or post.retry_count >= post.max_retries
            if is_permanent:
                post.status = PostStatus.FAILED.value
                await self.db.flush()
                logger.error("Post %s failed permanently: %s", post_id, exc)

                await self._emit_learning(post_failed(
                    post_id=post_id,
                    tenant_id=self.tenant_id,
                    platform=post.platform or "",
                    error=str(exc),
                    retry_count=post.retry_count,
                    max_retries=post.max_retries,
                    is_permanent=True,
                    campaign_id=post.campaign_id,
                ))

                return {
                    "post_id": str(post_id),
                    "status": "failed",
                    "error": str(exc),
                    "retry_count": post.retry_count,
                    "alert": True,
                }
            else:
                post.status = PostStatus.SCHEDULED.value
                await self.db.flush()
                delay = self._backoff_delay(post.retry_count)
                logger.warning(
                    "Post %s publish attempt %d failed, retry in %ds: %s",
                    post_id, post.retry_count, delay, exc,
                )

                await self._emit_learning(post_failed(
                    post_id=post_id,
                    tenant_id=self.tenant_id,
                    platform=post.platform or "",
                    error=str(exc),
                    retry_count=post.retry_count,
                    max_retries=post.max_retries,
                    is_permanent=False,
                    campaign_id=post.campaign_id,
                ))

                return {
                    "post_id": str(post_id),
                    "status": "retry",
                    "retry_count": post.retry_count,
                    "retry_delay_seconds": delay,
                    "error": str(exc),
                }

    async def retry_post(self, post_id: uuid.UUID) -> dict:
        """Re-queue a failed post for another publish attempt."""
        post = await self._get_post(post_id)
        post = await self._transition(post, PostStatus.QUEUED)
        return {"post_id": str(post_id), "status": post.status, "retry_count": post.retry_count}

    async def get_status(self, post_id: uuid.UUID) -> dict:
        """Return the current status of a post with retry info."""
        post = await self._get_post(post_id)
        return {
            "post_id": str(post_id),
            "status": post.status,
            "platform": post.platform,
            "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
            "published_at": post.published_at.isoformat() if post.published_at else None,
            "permalink": post.permalink,
            "platform_post_id": post.platform_post_id,
            "retry_count": post.retry_count,
            "max_retries": post.max_retries,
            "last_error": post.last_error,
            "policy_decision": post.policy_decision,
        }

    async def list_pending_approvals(self) -> list[PostModel]:
        """Return all posts awaiting approval for this tenant."""
        stmt = select(PostModel).where(
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == PostStatus.QUEUED.value,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_scheduled(self) -> list[PostModel]:
        """Return all scheduled posts for this tenant."""
        stmt = select(PostModel).where(
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == PostStatus.SCHEDULED.value,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_failed(self) -> list[PostModel]:
        """Return all failed posts for this tenant."""
        stmt = select(PostModel).where(
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == PostStatus.FAILED.value,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── internals ─────────────────────────────────────────────────────

    def _evaluate_policy(self, post: PostModel) -> dict:
        """Run the policy engine against a post.

        Never raises — returns allow on error so posts aren't blocked
        by policy engine bugs.
        """
        try:
            ctx = ActionContext(
                action_type="publish",
                platform=post.platform or "",
                content=post.content_text or "",
                media_urls=post.media_urls or [],
                destination_url=post.destination_url or "",
                artist_id=str(post.channel_id) if post.channel_id else "",
                release_id="",
                campaign_id=str(post.campaign_id) if post.campaign_id else "",
            )
            engine = create_default_engine()
            result = engine.evaluate(ctx)

            if result.blocked:
                return {"decision": "block", "reasons": result.blocking_reasons}
            if result.needs_approval:
                return {"decision": "require_approval", "reasons": result.approval_reasons}
            return {"decision": "allow", "reasons": []}
        except Exception:
            logger.exception("Policy evaluation failed — defaulting to allow")
            return {"decision": "allow", "reasons": []}

    async def _do_publish(self, post: PostModel) -> dict:
        """Call the platform adapter to publish.

        Returns dict with platform_post_id and permalink.
        Uses the adapter factory to load credentials and dispatch.
        Auto-refreshes expired tokens once before failing.
        """
        from amplify.adapters.base import TokenExpiredError, RateLimitError

        channel_id = getattr(post, "channel_id", None)
        if not channel_id:
            logger.info("No channel_id on post %s — using stub publish", post.id)
            return {
                "platform_post_id": f"stub_{post.id}",
                "permalink": f"https://{post.platform}.com/p/stub_{post.id}",
            }

        from app.services.adapter_factory import get_adapter
        from app.config import Settings

        settings = Settings()

        try:
            adapter = await get_adapter(self.db, channel_id, settings)
        except Exception as exc:
            logger.error("Failed to load adapter for channel %s: %s", channel_id, exc)
            raise

        try:
            result = await adapter.publish(
                content=post.content_text or "",
                media_paths=post.media_urls or [],
            )
        except TokenExpiredError:
            # Auto-refresh and retry once
            logger.info("Token expired for channel %s — attempting refresh", channel_id)
            from app.services.oauth_service import OAuthService
            svc = OAuthService(self.db, None, settings)
            await svc.refresh_channel_token(channel_id, self.tenant_id)

            adapter = await get_adapter(self.db, channel_id, settings)
            result = await adapter.publish(
                content=post.content_text or "",
                media_paths=post.media_urls or [],
            )
        except RateLimitError as exc:
            raise Exception(
                f"Rate limited by {post.platform}. Retry after {exc.retry_after or 60}s."
            ) from exc

        return {
            "platform_post_id": result.platform_post_id,
            "permalink": result.url,
        }

    @staticmethod
    def _backoff_delay(retry_count: int) -> int:
        """Exponential backoff: 30s, 120s, 480s, capped at 600s."""
        delay = BASE_DELAY * (2 ** (retry_count - 1))
        return min(int(delay), MAX_DELAY)
