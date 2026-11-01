"""shared.paths.get_server_url / set_server_url: config.json plus the env override."""

from shared import paths

# Taken at import, before conftest's autouse fixture swaps them for in-memory stand-ins.
GET, SET = paths.get_server_url, paths.set_server_url


def test_saved_cleared_and_env_wins(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setattr(paths, "_config_path", lambda: config)
    assert GET() is None
    SET(" http://10.0.0.5:8000 ")
    assert GET() == "http://10.0.0.5:8000"
    assert "10.0.0.5" in config.read_text(encoding="utf-8")
    monkeypatch.setenv(paths.SERVER_URL_ENV, "http://other:8000")
    assert GET() == "http://other:8000"
    monkeypatch.delenv(paths.SERVER_URL_ENV)
    SET(None)
    assert GET() is None
