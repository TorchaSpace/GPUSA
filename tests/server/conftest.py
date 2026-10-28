"""The server tests need the packages in server/requirements.txt, which the
desktop CI does not install: skip collecting them when those are missing."""

import importlib.util

collect_ignore_glob = []
if any(importlib.util.find_spec(m) is None for m in ("fastapi", "psycopg", "psycopg_pool", "httpx")):
    collect_ignore_glob.append("test_*.py")
