# Multi-Tenancy Model

## Overview

Amplify-OS uses **row-level tenant isolation** within a shared database. Every data table includes a `tenant_id` column, and all queries are automatically scoped to the current tenant.

## TenantMixin

All SQLAlchemy models that hold tenant-scoped data inherit from `TenantMixin`:

```python
class TenantMixin:
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.id"), index=True, nullable=False
    )
```

This ensures every row is tagged with its owning tenant.

## Middleware Behavior

### Local Mode
In local/development mode, the system operates in **single-tenant** mode:
- A default tenant (`"local"`) is created on first startup.
- The middleware injects `tenant_id = "local"` into every request context.
- No authentication is required for tenant resolution.

### Cloud Mode
In production, tenant resolution is performed via:
1. **JWT claims** -- The `tenant_id` is embedded in the access token at login.
2. **API key header** -- For machine-to-machine calls, the `X-Tenant-ID` header is validated against the API key's allowed tenants.
3. **Subdomain mapping** (future) -- `acme.amplify-os.com` resolves to the `acme` tenant.

## Query Scoping

The `TenantRepository` base class automatically applies tenant filtering:

```python
class TenantRepository(Generic[T]):
    async def list(self) -> list[T]:
        stmt = select(self.model).where(
            self.model.tenant_id == self.context.tenant_id
        )
        ...
```

This prevents cross-tenant data leakage without requiring developers to remember to filter manually.

## Tenant Provisioning Flow

1. New user signs up or is invited.
2. A `Tenant` record is created with a unique ID and slug.
3. Default settings, billing plan (free tier), and admin user are created.
4. The tenant is ready for use -- no schema migrations needed since all tenants share the same schema.

## Limits and Isolation

| Concern          | Mechanism                              |
|------------------|----------------------------------------|
| Data isolation   | `tenant_id` on every row + query scope |
| Rate limiting    | Per-tenant Redis counters              |
| Feature gating   | Plan tier checks via billing package   |
| Resource quotas  | Metering service enforces plan limits  |
