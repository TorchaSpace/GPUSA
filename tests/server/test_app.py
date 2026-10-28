from contextlib import contextmanager

from fastapi.testclient import TestClient

from server.app import create_app


class _Conn:
    def execute(self, sql):
        assert sql == "SELECT 1"


class _Pool:
    def __init__(self, fail=False):
        self.fail = fail

    @contextmanager
    def connection(self):
        if self.fail:
            raise RuntimeError("down")
        yield _Conn()


def test_health_never_touches_the_database():
    with TestClient(create_app(pool=_Pool(fail=True))) as c:
        assert c.get("/health").json()["status"] == "ok"


def test_health_db_ok_and_503():
    with TestClient(create_app(pool=_Pool())) as c:
        assert c.get("/health/db").status_code == 200
    with TestClient(create_app(pool=_Pool(fail=True))) as c:
        assert c.get("/health/db").status_code == 503
