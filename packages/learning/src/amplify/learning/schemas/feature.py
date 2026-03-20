"""Feature schemas — structured representation of action context.

Features describe the context in which an action was taken: platform,
content type, timing, audience segment, etc.  The FeatureSchema defines
what features exist and their types, enabling validation and
human-readable explanations.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FeatureType(str, Enum):
    """Data type of a feature, used for validation and display."""

    CATEGORICAL = "categorical"
    NUMERIC = "numeric"
    BOOLEAN = "boolean"
    TEMPORAL = "temporal"  # hour-of-day, day-of-week, etc.


class FeatureDefinition(BaseModel):
    """Metadata about a single feature."""

    name: str
    feature_type: FeatureType
    description: str = ""
    categories: list[str] | None = None  # valid values for CATEGORICAL
    min_value: float | None = None  # bounds for NUMERIC
    max_value: float | None = None


class FeatureSchema(BaseModel):
    """Registry of all known features.

    Acts as documentation and validation layer.  New features are registered
    here so that downstream components (ranking, evaluation, explanations)
    know what they're working with.
    """

    features: dict[str, FeatureDefinition] = Field(default_factory=dict)

    def register(self, definition: FeatureDefinition) -> None:
        self.features[definition.name] = definition

    def validate_vector(self, vector: FeatureVector) -> list[str]:
        """Return a list of warnings for any unknown or mistyped features."""
        warnings: list[str] = []
        for key in vector.values:
            if key not in self.features:
                warnings.append(f"Unknown feature: {key}")
        return warnings


class FeatureVector(BaseModel):
    """A concrete set of feature values for a single observation.

    Thin wrapper around a dict — exists so that type signatures are
    explicit and we can add validation without changing call sites.
    """

    values: dict[str, Any] = Field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    def __contains__(self, name: str) -> bool:
        return name in self.values
