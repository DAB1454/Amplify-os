"""Root conftest — pytest rootdir marker and app-namespace isolation.

Problem: three apps (api, worker, local_node) each contain an ``app/``
package.  When the full suite runs from the monorepo root, ``from app.X``
is ambiguous unless we ensure the correct app directory is at the front
of ``sys.path`` when each app's tests are collected and executed.

With ``--import-mode=importlib`` (set in pyproject.toml), pytest no longer
mutates ``sys.path`` itself, so we manage it explicitly here via two hooks:

* ``pytest_collectstart``  — resolves ``from app.X`` at *collection* time
  (when test modules are first imported).
* ``pytest_runtest_setup``  — resolves ``from app.X`` at *execution* time
  (when fixtures do lazy imports inside function bodies).

Both hooks call ``_switch_app``, which is a no-op when the active app has
not changed, so overhead is negligible.
"""

from __future__ import annotations

import sys
from pathlib import Path

_current_app_root: str | None = None


def _app_root_for(fspath: str | Path) -> str | None:
    """Return the app root directory if *fspath* lives under ``apps/<name>/``
    and that directory contains an ``app/`` Python package."""
    parts = Path(fspath).parts
    try:
        idx = parts.index("apps")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    candidate = str(Path(*parts[: idx + 2]))
    if (Path(candidate) / "app" / "__init__.py").is_file():
        return candidate
    return None


def _switch_app(app_root: str) -> None:
    """Ensure *app_root* is first on ``sys.path`` so ``import app`` resolves
    to its ``app/`` package.  Removes every *other* app root whose own
    ``app/`` would shadow it, then clears any cached ``app.*`` modules so
    the next ``import app`` picks up the new location."""
    global _current_app_root
    if app_root == _current_app_root:
        return

    # Remove competing app roots.
    for p in list(sys.path):
        if p != app_root and (Path(p) / "app" / "__init__.py").is_file():
            sys.path.remove(p)

    # Promote the target to position 0.
    if app_root in sys.path:
        sys.path.remove(app_root)
    sys.path.insert(0, app_root)

    # Flush cached ``app.*`` entries so Python re-discovers from the new path.
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]

    _current_app_root = app_root


# ── Hooks ─────────────────────────────────────────────────────────────


def pytest_collectstart(collector) -> None:  # noqa: ANN001
    """Switch ``app`` during collection (test-module imports)."""
    path = getattr(collector, "path", None) or getattr(collector, "fspath", None)
    if path is not None:
        root = _app_root_for(str(path))
        if root:
            _switch_app(root)


def pytest_runtest_setup(item) -> None:  # noqa: ANN001
    """Switch ``app`` before each test (fixture lazy imports)."""
    fspath = str(item.path) if hasattr(item, "path") else str(item.fspath)
    root = _app_root_for(fspath)
    if root:
        _switch_app(root)
