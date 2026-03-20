"""Tests for the token store."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from amplify.adapters.token_store import REFRESH_BUFFER, TokenSet, TokenStore


class TestTokenSet:
    def test_defaults(self):
        t = TokenSet()
        assert t.access_token == ""
        assert t.refresh_token == ""
        assert t.expires_at is None
        assert not t.is_expired
        assert not t.needs_refresh

    def test_not_expired(self):
        t = TokenSet(expires_at=datetime.utcnow() + timedelta(hours=1))
        assert not t.is_expired
        assert not t.needs_refresh

    def test_expired(self):
        t = TokenSet(expires_at=datetime.utcnow() - timedelta(hours=1))
        assert t.is_expired
        assert t.needs_refresh

    def test_needs_refresh_within_buffer(self):
        t = TokenSet(expires_at=datetime.utcnow() + timedelta(minutes=2))
        assert not t.is_expired
        assert t.needs_refresh  # within 5-min buffer

    def test_no_expiry_never_expired(self):
        t = TokenSet(access_token="abc")
        assert not t.is_expired
        assert not t.needs_refresh


class TestTokenStore:
    def test_get_missing(self):
        store = TokenStore()
        assert store.get("instagram", "ch1") is None

    def test_set_and_get(self):
        store = TokenStore()
        tokens = TokenSet(access_token="abc", platform="instagram")
        store.set("instagram", "ch1", tokens)
        result = store.get("instagram", "ch1")
        assert result is not None
        assert result.access_token == "abc"

    def test_different_channels(self):
        store = TokenStore()
        store.set("instagram", "ch1", TokenSet(access_token="a"))
        store.set("instagram", "ch2", TokenSet(access_token="b"))
        assert store.get("instagram", "ch1").access_token == "a"
        assert store.get("instagram", "ch2").access_token == "b"

    def test_different_platforms(self):
        store = TokenStore()
        store.set("instagram", "ch1", TokenSet(access_token="ig"))
        store.set("tiktok", "ch1", TokenSet(access_token="tt"))
        assert store.get("instagram", "ch1").access_token == "ig"
        assert store.get("tiktok", "ch1").access_token == "tt"

    def test_remove(self):
        store = TokenStore()
        store.set("instagram", "ch1", TokenSet(access_token="abc"))
        store.remove("instagram", "ch1")
        assert store.get("instagram", "ch1") is None

    def test_remove_nonexistent(self):
        store = TokenStore()
        store.remove("instagram", "ch1")  # should not raise

    def test_overwrite(self):
        store = TokenStore()
        store.set("tiktok", "ch1", TokenSet(access_token="old"))
        store.set("tiktok", "ch1", TokenSet(access_token="new"))
        assert store.get("tiktok", "ch1").access_token == "new"
