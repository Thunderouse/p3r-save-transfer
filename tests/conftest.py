"""Shared test setup.

The CLI tests call ``logging.basicConfig(force=True)``, which would otherwise
leave a handler bound to pytest's already-closed capture stream for every later
test - and a failing ``logging`` call inside a worker thread must not be allowed
to leak between tests.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
        root.handlers[:] = handlers
        root.setLevel(level)
