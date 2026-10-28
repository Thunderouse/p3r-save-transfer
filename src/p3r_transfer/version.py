"""Single source of truth for the project version.

Everything else reads it from here: the CLI ``--version``, the GUI title,
``pyproject.toml`` (kept in sync by ``tests/test_version.py``), the portable zip
name and the git tag.
"""

from __future__ import annotations

__version__ = "1.0.0-rc1"
