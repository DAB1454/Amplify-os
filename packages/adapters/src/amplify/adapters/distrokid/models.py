"""Pydantic models for DistroKid data."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class DistroKidRelease(BaseModel):
    """A release distributed through DistroKid."""

    title: str
    artist: str
    upc: str
    status: str
    stores: list[str]
    release_date: date


class DistroKidEarnings(BaseModel):
    """Earnings report for a given period."""

    period: str
    amount: float
    currency: str
    breakdown: list[dict]
