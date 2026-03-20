"""Privacy module — enforces data boundaries for cross-tenant learning.

This module is the gatekeeper between tenant-specific data and any
cross-tenant aggregation.  It provides:
- Field redaction (strip PII before aggregation)
- K-anonymity enforcement (minimum tenant count per bucket)
- Cardinality capping (prevent re-identification via rare categories)
- Audit logging of all cross-tenant data flows
"""

from amplify.learning.privacy.filter import PrivacyFilter

__all__ = ["PrivacyFilter"]
