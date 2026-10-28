import pytest

from server.config import ConfigError, load_settings

IAM_ENV = {"GPUSA_DB_HOST": "db.example", "GPUSA_DB_REGION": "eu-north-1"}


def test_iam_is_the_default():
    s = load_settings(IAM_ENV)
    assert (s.host, s.port, s.name, s.user, s.sslmode) == ("db.example", 5432, "postgres", "postgres", "require")
    assert s.uses_iam


def test_host_required():
    with pytest.raises(ConfigError):
        load_settings({})


def test_iam_needs_region():
    with pytest.raises(ConfigError):
        load_settings({"GPUSA_DB_HOST": "db.example"})


def test_password_mode_needs_password():
    env = {"GPUSA_DB_HOST": "h", "GPUSA_DB_AUTH": "password"}
    with pytest.raises(ConfigError):
        load_settings(env)
    assert not load_settings({**env, "GPUSA_DB_PASSWORD": "x"}).uses_iam


def test_database_url_wins_for_local_dev():
    s = load_settings({"DATABASE_URL": "postgresql://u:p@localhost/x"})
    assert not s.uses_iam


def test_bad_auth_and_port():
    with pytest.raises(ConfigError):
        load_settings({**IAM_ENV, "GPUSA_DB_AUTH": "magic"})
    with pytest.raises(ConfigError):
        load_settings({**IAM_ENV, "GPUSA_DB_PORT": "abc"})


def test_secrets_stay_out_of_repr():
    s = load_settings({"GPUSA_DB_HOST": "h", "GPUSA_DB_AUTH": "password", "GPUSA_DB_PASSWORD": "hunter2"})
    assert "hunter2" not in repr(s)
