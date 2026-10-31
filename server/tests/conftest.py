import importlib.util
import os
import sys

# The server's dependencies live in server/requirements.txt, not in the
# desktop apps' requirements. When the root `pytest` runs without them
# (desktop CI job, a cashier's dev box), skip collecting these tests instead
# of failing.
if importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("psycopg") is None:
    collect_ignore_glob = ["test_*.py"]
else:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Importing gpusa_server.main builds the module-level app from os.environ;
    # keep a developer's own DB settings from leaking into it.
    for _key in ("DATABASE_URL", "GPUSA_DB_HOST", "GPUSA_DB_NAME", "GPUSA_DB_SECRET_ARN",
                 "GPUSA_DB_SSLMODE", "GPUSA_DB_PORT"):
        os.environ.pop(_key, None)
