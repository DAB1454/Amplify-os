"""Rights guard — validates that content can be legally published on target platforms."""

from __future__ import annotations

from uuid import UUID


class RightsGuard:
    """Validates that content can be legally published on target platforms."""

    @classmethod
    def check_music_rights(cls, track_id: UUID, platform: str) -> tuple[bool, str]:
        """Check whether a track has the necessary rights for a platform.

        Stub — always returns cleared.

        Args:
            track_id: The track to check.
            platform: The target platform.

        Returns:
            (cleared, reason).
        """
        return (True, "Rights check not yet implemented — assumed cleared.")

    @classmethod
    def check_asset_license(cls, asset_id: UUID) -> tuple[bool, str]:
        """Check whether an asset has a valid license for use.

        Stub — always returns valid.

        Args:
            asset_id: The asset to check.

        Returns:
            (valid, reason).
        """
        return (True, "License check not yet implemented — assumed valid.")
