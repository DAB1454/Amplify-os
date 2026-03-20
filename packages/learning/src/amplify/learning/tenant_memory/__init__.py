"""Tenant memory — per-tenant observation store and insight derivation.

Each tenant has its own isolated memory of observations and derived
insights.  This module manages storage, retrieval, compaction, and
insight generation.  Nothing in tenant memory is shared across tenants
without going through the privacy module first.
"""

from amplify.learning.tenant_memory.store import TenantMemoryStore

__all__ = ["TenantMemoryStore"]
