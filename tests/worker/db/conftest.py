"""Tests in this directory talk to a real PostgreSQL through psycopg's async driver."""

from __future__ import annotations

import asyncio
import sys

import pytest


@pytest.hookimpl
def pytest_asyncio_loop_factories(config, item):
    # psycopg's async driver cannot run on the Proactor loop Windows uses by
    # default. Scoped to this directory so the rest of the suite is untouched.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
