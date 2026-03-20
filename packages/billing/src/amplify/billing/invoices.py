"""Invoice generation stubs for Amplify-OS billing.

TODO: Integrate with Stripe Billing or generate PDF invoices.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


class InvoiceService:
    """Stub invoice service.

    In production this would read from a billing database and/or
    delegate to Stripe for invoice management.
    """

    def __init__(self) -> None:
        self._invoices: dict[str, list[dict[str, object]]] = {}  # tenant_id -> list of invoices

    async def generate_invoice(self, tenant_id: str, period: str) -> dict[str, object]:
        """Generate an invoice for a tenant and billing period.

        Args:
            tenant_id: The tenant identifier.
            period: Billing period as ``YYYY-MM``.

        Returns:
            A dict representing the invoice.
        """
        invoice = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "period": period,
            "status": "draft",
            "line_items": [],
            "total": 0.00,
            "currency": "usd",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._invoices.setdefault(tenant_id, []).append(invoice)
        return invoice

    async def list_invoices(self, tenant_id: str) -> list[dict[str, object]]:
        """Return all invoices for a tenant.

        Args:
            tenant_id: The tenant identifier.

        Returns:
            List of invoice dicts, most recent first.
        """
        invoices = self._invoices.get(tenant_id, [])
        return sorted(invoices, key=lambda i: i["created_at"], reverse=True)
