"""UTM parameter builder and campaign URL utilities."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug for UTM values."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class UTMBuilder:
    """Build URLs with UTM tracking parameters."""

    @staticmethod
    def build_url(
        base_url: str,
        source: str,
        medium: str,
        campaign: str,
        content: str | None = None,
        term: str | None = None,
    ) -> str:
        """Append UTM parameters to a base URL.

        Args:
            base_url: The destination URL.
            source: Traffic source (e.g. 'instagram', 'email').
            medium: Marketing medium (e.g. 'social', 'cpc').
            campaign: Campaign name.
            content: Optional content identifier for A/B testing.
            term: Optional keyword / term for paid search.

        Returns:
            The full URL with UTM query parameters.
        """
        parsed = urlparse(base_url)
        existing_params = parse_qs(parsed.query)

        utm_params: dict[str, str] = {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": campaign,
        }
        if content is not None:
            utm_params["utm_content"] = content
        if term is not None:
            utm_params["utm_term"] = term

        existing_params.update(utm_params)

        new_query = urlencode(existing_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    @staticmethod
    def build_campaign_url(
        destination_url: str,
        campaign_name: str,
        platform: str,
        content_label: str | None = None,
    ) -> str:
        """Generate a campaign URL with standard UTM structure.

        Convenience wrapper that maps platform → source and sets medium to 'social'.

        Args:
            destination_url: The target URL (e.g. HyperFollow link).
            campaign_name: Campaign name, will be slugified.
            platform: Platform name (instagram, tiktok, youtube, email, etc.).
            content_label: Optional variant label (e.g. 'variant-a', 'reel-hook-1').

        Returns:
            Full URL with utm_source, utm_medium, utm_campaign, and optionally utm_content.
        """
        medium = "email" if platform == "email" else "social"
        return UTMBuilder.build_url(
            base_url=destination_url,
            source=platform,
            medium=medium,
            campaign=_slugify(campaign_name),
            content=content_label,
        )

    @staticmethod
    def build_campaign_urls(
        destinations: dict[str, str | None],
        campaign_name: str,
        platform: str,
        content_label: str | None = None,
    ) -> dict[str, str]:
        """Generate UTM-tagged URLs for all non-null release destinations.

        Args:
            destinations: Dict of {field_name: url} from a Release model.
            campaign_name: Campaign name.
            platform: Source platform.
            content_label: Optional variant label.

        Returns:
            Dict of {field_name: utm_url} for every destination that has a URL.
        """
        result = {}
        for field, url in destinations.items():
            if url:
                result[field] = UTMBuilder.build_campaign_url(
                    destination_url=url,
                    campaign_name=campaign_name,
                    platform=platform,
                    content_label=content_label,
                )
        return result
