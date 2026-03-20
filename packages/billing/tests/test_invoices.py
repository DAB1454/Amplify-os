"""Tests for invoice generation."""

from __future__ import annotations

import pytest

from amplify.billing.invoices import InvoiceService


class TestInvoiceService:
    @pytest.fixture
    def svc(self):
        return InvoiceService()

    @pytest.mark.asyncio
    async def test_generate_invoice(self, svc):
        inv = await svc.generate_invoice("t1", "2026-03")
        assert inv["tenant_id"] == "t1"
        assert inv["period"] == "2026-03"
        assert inv["status"] == "draft"
        assert inv["total"] == 0.00
        assert inv["currency"] == "usd"
        assert "id" in inv
        assert "created_at" in inv

    @pytest.mark.asyncio
    async def test_list_invoices_empty(self, svc):
        result = await svc.list_invoices("nobody")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_invoices_returns_all(self, svc):
        await svc.generate_invoice("t1", "2026-01")
        await svc.generate_invoice("t1", "2026-02")
        await svc.generate_invoice("t1", "2026-03")
        result = await svc.list_invoices("t1")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_invoices_most_recent_first(self, svc):
        inv1 = await svc.generate_invoice("t1", "2026-01")
        inv2 = await svc.generate_invoice("t1", "2026-03")
        # Force distinct timestamps so sort is deterministic
        inv1["created_at"] = "2026-01-15T00:00:00+00:00"
        inv2["created_at"] = "2026-03-15T00:00:00+00:00"
        result = await svc.list_invoices("t1")
        # Most recent created_at should be first
        assert result[0]["period"] == "2026-03"

    @pytest.mark.asyncio
    async def test_invoices_isolated_by_tenant(self, svc):
        await svc.generate_invoice("t1", "2026-01")
        await svc.generate_invoice("t2", "2026-01")
        assert len(await svc.list_invoices("t1")) == 1
        assert len(await svc.list_invoices("t2")) == 1

    @pytest.mark.asyncio
    async def test_invoice_has_unique_id(self, svc):
        inv1 = await svc.generate_invoice("t1", "2026-01")
        inv2 = await svc.generate_invoice("t1", "2026-02")
        assert inv1["id"] != inv2["id"]

    @pytest.mark.asyncio
    async def test_invoice_has_line_items(self, svc):
        inv = await svc.generate_invoice("t1", "2026-01")
        assert isinstance(inv["line_items"], list)
