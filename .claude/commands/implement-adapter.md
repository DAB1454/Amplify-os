# Implement Adapter

## Description
Generates boilerplate for a new platform adapter in the Amplify OS system, including authentication, publishing, analytics modules, and test stubs.

## Arguments
- `platform_name` (required): Name of the platform (e.g., spotify, twitter, soundcloud)

## Steps

1. **Validate platform name**
   - Ensure the platform name is lowercase, alphanumeric (hyphens allowed)
   - Check that an adapter doesn't already exist at `packages/adapters/{platform_name}/`
   - Look up if there's a known API spec or SDK for the platform

2. **Create adapter directory structure**
   - Create `packages/adapters/{platform_name}/`
   - Create `packages/adapters/{platform_name}/tests/`

3. **Generate auth.py**
   - Import base auth class from `packages/core/auth/base.py`
   - Implement OAuth2 flow scaffolding with `authorize()`, `callback()`, `refresh_token()`, `revoke()`
   - Add platform-specific scopes as constants
   - Include token storage and retrieval methods

4. **Generate publish.py**
   - Import base publisher class from `packages/core/publishing/base.py`
   - Implement `validate_asset()`, `publish()`, `schedule()`, `delete()`, `get_status()`
   - Add platform-specific asset validation rules (dimensions, formats, size limits)
   - Include rate limit handling

5. **Generate analytics.py**
   - Import base analytics class from `packages/core/analytics/base.py`
   - Implement `fetch_metrics()`, `get_post_performance()`, `get_audience_insights()`, `get_top_content()`
   - Define platform-specific metric mappings to the unified Metric domain model

6. **Generate __init__.py**
   - Export all public classes: `{Platform}Auth`, `{Platform}Publisher`, `{Platform}Analytics`
   - Include adapter metadata (name, version, supported features)

7. **Create test stubs**
   - Generate `tests/test_auth.py` with test cases for OAuth flow
   - Generate `tests/test_publish.py` with test cases for validation and publishing
   - Generate `tests/test_analytics.py` with test cases for metric fetching
   - Include pytest fixtures for mocking API responses
   - Add `tests/conftest.py` with shared fixtures

8. **Output summary**
   - List all created files
   - Note which methods need platform-specific implementation
   - Link to platform API documentation if known
