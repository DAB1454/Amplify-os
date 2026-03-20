"""Prompt versioning and generation lineage tracking.

Provides a DB-free core for prompt version resolution, assignment logic,
shadow mode evaluation, and lineage metadata attachment.  The heavy
DB-backed CRUD lives in apps/api/app/services/prompt_version_service.py;
this package contains the deterministic logic that agents import directly.
"""

from amplify.learning.prompts.resolver import PromptResolver
from amplify.learning.prompts.lineage import GenerationLineage, PromptLineageAttachment
from amplify.learning.prompts.registry import PromptRegistry, PromptVersion, PromptAssignment

__all__ = [
    "GenerationLineage",
    "PromptAssignment",
    "PromptLineageAttachment",
    "PromptRegistry",
    "PromptResolver",
    "PromptVersion",
]
