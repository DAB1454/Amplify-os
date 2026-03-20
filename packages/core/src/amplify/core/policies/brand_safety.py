"""Brand safety policy for content moderation.

Combines a curated block list with the platform-wide IntegrityPolicy
so that every content check benefits from both layers.
"""

from __future__ import annotations

from amplify.core.policies.integrity import IntegrityPolicy, IntegrityResult


class BrandSafetyPolicy:
    """Checks content against brand-safety rules before publishing."""

    # Additional brand-safety terms beyond what IntegrityPolicy covers.
    # IntegrityPolicy handles fake engagement, fake personas, bulk spam,
    # impersonation, and generic content dumps.  These catch softer
    # marketing anti-patterns that are brand-unsafe but not necessarily
    # integrity violations.
    BLOCKED_WORDS: list[str] = [
        "guaranteed results",
        "get rich quick",
        "free money",
        "click here now",
        "act now limited time",
        "miracle cure",
        "no risk",
        "once in a lifetime",
        "secret method",
        "double your money",
    ]

    @classmethod
    def check_content(cls, text: str) -> tuple[bool, list[str]]:
        """Check text content for brand-safety violations.

        Runs both the local block list AND IntegrityPolicy so that
        prohibited tactics (fake streams, spam, etc.) are also caught.

        Args:
            text: The content text to check.

        Returns:
            (safe, flagged_terms) — True if safe, with a list of any
            flagged terms found.
        """
        lower_text = text.lower()
        flagged: list[str] = []

        for word in cls.BLOCKED_WORDS:
            if word.lower() in lower_text:
                flagged.append(word)

        # Cross-reference with IntegrityPolicy
        integrity: IntegrityResult = IntegrityPolicy.check_text(text)
        for v in integrity.violations:
            flagged.append(f"[{v.rule}] {v.detail}")

        return (len(flagged) == 0, flagged)

    @classmethod
    def check_image_url(cls, url: str) -> tuple[bool, str]:
        """Check an image URL for brand-safety violations.

        This is a stub — image analysis is not yet implemented.

        Args:
            url: The URL of the image to check.

        Returns:
            (safe, reason) — Always returns safe for now.
        """
        return (True, "Image check not yet implemented — assumed safe.")
