# Package Layout

Amplify-OS uses a monorepo with seven shared Python packages under `packages/`. Every package follows the **src layout** with an **implicit namespace package** so that all code lives under a single `amplify.*` import namespace.

## Directory structure

```
packages/
├── core/
│   ├── pyproject.toml           # amplify-core
│   ├── src/
│   │   └── amplify/             # ← NO __init__.py (implicit namespace)
│   │       └── core/
│   │           ├── __init__.py
│   │           ├── domain/
│   │           ├── policies/
│   │           ├── workflows/
│   │           ├── analytics/
│   │           ├── notifications/
│   │           └── prompts/
│   └── tests/
│       ├── test_workflows.py
│       └── ...
├── db/
│   ├── pyproject.toml           # amplify-db
│   ├── src/
│   │   └── amplify/
│   │       └── db/
│   │           ├── __init__.py
│   │           ├── base.py
│   │           ├── session.py
│   │           ├── repository.py
│   │           ├── models/
│   │           └── migrations/
│   └── ...
├── adapters/                    # amplify-adapters
├── agents/                      # amplify-agents
├── media/                       # amplify-media
├── billing/                     # amplify-billing
└── observability/               # amplify-observability
```

## Key conventions

### Implicit namespace package

The `amplify/` directory inside each package's `src/` does **not** contain an `__init__.py`. This makes it an [implicit namespace package](https://peps.python.org/pep-0420/) — Python and setuptools merge the `amplify` namespace across all seven packages at install time. This is what allows `from amplify.core.domain import Artist` and `from amplify.db.models import ArtistModel` to work simultaneously even though `amplify.core` and `amplify.db` live in different installable packages.

**Rule:** Never create `__init__.py` in any `src/amplify/` directory.

### Canonical imports

Every import throughout the repo uses the `amplify.*` prefix:

```python
# Domain models
from amplify.core.domain import Artist, Campaign, Post

# Policies
from amplify.core.policies import PolicyEngine, create_default_engine

# Workflows
from amplify.core.workflows.pre_release import PreReleaseWorkflow

# Database
from amplify.db.models import ArtistModel, PostModel
from amplify.db.base import Base

# Adapters
from amplify.adapters import BaseAdapter, PublishResult
from amplify.adapters.instagram.adapter import InstagramAdapter

# Agents
from amplify.agents import AgentRunner, ClaudeClient

# Media
from amplify.media.renderers import CaptionBurner, LyricCardRenderer

# Billing
from amplify.billing.plans import get_plan, PlanTier

# Observability
from amplify.observability.metrics import MetricsCollector
```

**Never** use bare imports like `from core.domain import ...` or `from db.models import ...`.

### Package discovery

Each `pyproject.toml` uses explicit setuptools discovery:

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

This tells setuptools to look for packages under `src/`, and since there's no `__init__.py` in `src/amplify/`, it discovers `amplify.core`, `amplify.core.domain`, etc. as sub-packages of the shared namespace.

### Test directories

Test directories live at `packages/<pkg>/tests/` (outside `src/`). They do **not** have `__init__.py` files — this avoids namespace collisions when pytest collects tests across multiple packages in a single run.

Tests import from the installed package using the canonical namespace:

```python
# packages/core/tests/test_workflows.py
from amplify.core.workflows.pre_release import PreReleaseWorkflow
```

No `sys.path` manipulation. If tests fail with `ModuleNotFoundError`, run `pip install -e packages/core` (or whichever package) first.

## Installing packages

### Development (editable)

```bash
# Install all packages in editable mode
pip install -e packages/core
pip install -e packages/db
pip install -e packages/adapters
pip install -e packages/agents
pip install -e packages/media
pip install -e packages/billing
pip install -e packages/observability

# Or use the Makefile
make setup
```

Editable installs (`-e`) mean changes to source files take effect immediately without reinstalling.

### Dependency graph

```
amplify-core          ← no internal deps (pydantic only)
amplify-db            ← depends on amplify-core
amplify-adapters      ← depends on amplify-core
amplify-agents        ← depends on amplify-core
amplify-media         ← no internal deps
amplify-billing       ← no internal deps
amplify-observability ← no internal deps
```

Install `amplify-core` first, then the others in any order.

## Adding a new package

1. Create the directory structure:
   ```
   packages/newpkg/
   ├── pyproject.toml
   ├── src/
   │   └── amplify/          # NO __init__.py
   │       └── newpkg/
   │           ├── __init__.py
   │           └── ...
   └── tests/
       └── test_newpkg.py    # NO __init__.py in tests/
   ```

2. Write `pyproject.toml`:
   ```toml
   [project]
   name = "amplify-newpkg"
   version = "0.1.0"
   requires-python = ">=3.11"
   dependencies = []

   [build-system]
   requires = ["setuptools>=68.0"]
   build-backend = "setuptools.build_meta"

   [tool.setuptools.packages.find]
   where = ["src"]
   ```

3. Add to `Makefile` setup target:
   ```makefile
   .venv/bin/pip install -e packages/newpkg
   ```

4. Add a smoke test in `tests/test_package_imports.py`.

5. Install: `pip install -e packages/newpkg`

## Smoke tests

`tests/test_package_imports.py` verifies every package installs and its public API imports. Run it after any packaging change:

```bash
python -m pytest tests/test_package_imports.py -v
```
