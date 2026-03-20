"""amplify.learning — explainable, auditable learning subsystem for Amplify-OS.

Provides per-tenant learning (what works for *this* artist) and privacy-safe
cross-tenant priors (what generally works across all tenants), with full
auditability at every layer.
"""

from amplify.learning.config import LearningConfig

__all__ = ["LearningConfig"]
