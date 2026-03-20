"""Tests for the notification dispatcher."""

from __future__ import annotations

import pytest

from amplify.core.notifications.dispatcher import (
    InAppChannel,
    NotificationDispatcher,
    NotificationPayload,
    Severity,
    SlackWebhookChannel,
    EmailWebhookChannel,
    GenericWebhookChannel,
    create_default_dispatcher,
)


class TestNotificationPayload:
    def test_defaults(self):
        p = NotificationPayload(event_type="test")
        assert p.severity == Severity.INFO
        assert p.title == ""
        assert p.body == ""
        assert p.metadata == {}

    def test_custom_fields(self):
        p = NotificationPayload(
            event_type="approval.created",
            severity=Severity.CRITICAL,
            title="Urgent",
            body="Something happened",
            tenant_id="t1",
            entity_type="approval",
            entity_id="a1",
        )
        assert p.severity == Severity.CRITICAL
        assert p.title == "Urgent"


class TestInAppChannel:
    @pytest.mark.asyncio
    async def test_sends_and_records(self):
        channel = InAppChannel()
        payload = NotificationPayload(
            event_type="test",
            title="Hello",
            body="World",
        )
        result = await channel.send(payload)
        assert result is True
        assert len(channel.sent) == 1
        assert channel.sent[0].title == "Hello"

    @pytest.mark.asyncio
    async def test_min_severity_default(self):
        channel = InAppChannel()
        assert channel.min_severity == Severity.INFO


class TestSlackWebhookChannel:
    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        channel = SlackWebhookChannel(webhook_url="")
        payload = NotificationPayload(event_type="test", title="Hello")
        result = await channel.send(payload)
        assert result is False


class TestEmailWebhookChannel:
    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        channel = EmailWebhookChannel(webhook_url="")
        payload = NotificationPayload(event_type="test", title="Hello")
        result = await channel.send(payload)
        assert result is False


class TestGenericWebhookChannel:
    @pytest.mark.asyncio
    async def test_no_url_returns_false(self):
        channel = GenericWebhookChannel(webhook_url="")
        payload = NotificationPayload(event_type="test", title="Hello")
        result = await channel.send(payload)
        assert result is False


class TestNotificationDispatcher:
    @pytest.mark.asyncio
    async def test_dispatches_to_all_matching_channels(self):
        d = NotificationDispatcher()
        in_app = InAppChannel(min_severity=Severity.INFO)
        d.add_channel(in_app)

        payload = NotificationPayload(
            event_type="test",
            severity=Severity.WARNING,
            title="Test",
        )
        results = await d.dispatch(payload)
        assert results["in_app"] is True
        assert len(in_app.sent) == 1

    @pytest.mark.asyncio
    async def test_skips_channels_below_severity(self):
        d = NotificationDispatcher()
        warning_only = InAppChannel(min_severity=Severity.WARNING)
        warning_only.name = "warning_only"
        d.add_channel(warning_only)

        payload = NotificationPayload(
            event_type="test",
            severity=Severity.INFO,
            title="Low priority",
        )
        results = await d.dispatch(payload)
        assert "warning_only" not in results
        assert len(warning_only.sent) == 0

    @pytest.mark.asyncio
    async def test_critical_reaches_all(self):
        d = NotificationDispatcher()
        info_ch = InAppChannel(min_severity=Severity.INFO)
        info_ch.name = "info_ch"
        warn_ch = InAppChannel(min_severity=Severity.WARNING)
        warn_ch.name = "warn_ch"
        crit_ch = InAppChannel(min_severity=Severity.CRITICAL)
        crit_ch.name = "crit_ch"
        d.add_channel(info_ch)
        d.add_channel(warn_ch)
        d.add_channel(crit_ch)

        payload = NotificationPayload(
            event_type="test",
            severity=Severity.CRITICAL,
            title="Critical",
        )
        results = await d.dispatch(payload)
        assert results["info_ch"] is True
        assert results["warn_ch"] is True
        assert results["crit_ch"] is True

    @pytest.mark.asyncio
    async def test_empty_dispatcher(self):
        d = NotificationDispatcher()
        payload = NotificationPayload(event_type="test", title="Hello")
        results = await d.dispatch(payload)
        assert results == {}


class TestCreateDefaultDispatcher:
    def test_no_urls_creates_in_app_only(self):
        d = create_default_dispatcher()
        assert len(d.channels) == 1
        assert d.channels[0].name == "in_app"

    def test_slack_url_adds_slack(self):
        d = create_default_dispatcher(slack_url="https://hooks.slack.com/test")
        assert len(d.channels) == 2
        names = [c.name for c in d.channels]
        assert "slack" in names

    def test_all_urls(self):
        d = create_default_dispatcher(
            slack_url="https://hooks.slack.com/test",
            email_url="https://api.sendgrid.com/test",
            webhook_url="https://example.com/webhook",
        )
        assert len(d.channels) == 4
        names = [c.name for c in d.channels]
        assert "in_app" in names
        assert "slack" in names
        assert "email" in names
        assert "webhook" in names
