import pytest

from gpusa_server import aws_secrets
from gpusa_server.aws_secrets import SecretError, fetch_db_credentials
from gpusa_server.config import ConfigError, load_settings
from gpusa_server.db import DatabaseNotConfigured, conninfo_for

ARN = "arn:aws:secretsmanager:eu-central-1:123456789012:secret:rds!db-abc"
AWS_ENV = {"GPUSA_DB_HOST": "db.example.com", "GPUSA_DB_NAME": "gpusa", "GPUSA_DB_SECRET_ARN": ARN}


class FakeSM:
    def __init__(self, secret='{"username": "gpusa_app", "password": "s3cr3t"}'):
        self.secret = secret
        self.calls = 0

    def get_secret_value(self, SecretId):
        self.calls += 1
        return {"SecretString": self.secret}


@pytest.fixture(autouse=True)
def _fresh_cache():
    aws_secrets.clear_cache()
    yield
    aws_secrets.clear_cache()


def test_empty_env_means_no_database():
    s = load_settings({})
    assert s.mode == "none"
    with pytest.raises(DatabaseNotConfigured):
        conninfo_for(s)


def test_local_url_mode_never_touches_secrets_manager(monkeypatch):
    def no_aws(*a, **k):
        raise AssertionError("Secrets Manager must not be called without GPUSA_DB_SECRET_ARN")

    monkeypatch.setattr(aws_secrets, "fetch_db_credentials", no_aws)
    s = load_settings({"DATABASE_URL": "postgresql://gpusa:pw@localhost/gpusa"})
    assert s.mode == "url"
    assert conninfo_for(s) == "postgresql://gpusa:pw@localhost/gpusa"
    assert "pw" not in repr(s)


def test_aws_mode_defaults_and_validation():
    s = load_settings(AWS_ENV)
    assert (s.mode, s.db_port, s.db_sslmode) == ("aws", 5432, "require")

    with pytest.raises(ConfigError, match="GPUSA_DB_SECRET_ARN"):
        load_settings({"GPUSA_DB_HOST": "h", "GPUSA_DB_NAME": "n"})
    with pytest.raises(ConfigError, match="local development only"):
        load_settings({**AWS_ENV, "DATABASE_URL": "postgresql://x"})
    with pytest.raises(ConfigError, match="SSLMODE"):
        load_settings({**AWS_ENV, "GPUSA_DB_SSLMODE": "yes"})


def test_aws_conninfo_uses_secret(monkeypatch):
    fake = FakeSM()
    monkeypatch.setattr(aws_secrets, "_default_client", lambda arn: fake)
    info = conninfo_for(load_settings({**AWS_ENV, "GPUSA_DB_SSLMODE": "verify-full"}))
    for part in ("host=db.example.com", "dbname=gpusa", "user=gpusa_app", "password=s3cr3t",
                 "sslmode=verify-full"):
        assert part in info


def test_secret_is_cached_until_ttl():
    fake, clock = FakeSM(), [1000.0]
    get = lambda: fetch_db_credentials(ARN, client=fake, now=lambda: clock[0], ttl=300)
    assert get().password == "s3cr3t"
    get()
    assert fake.calls == 1
    clock[0] += 301
    get()
    assert fake.calls == 2
    assert "s3cr3t" not in repr(get())


def test_bad_secret_format():
    with pytest.raises(SecretError):
        fetch_db_credentials(ARN, client=FakeSM(secret="not json"))
    with pytest.raises(SecretError):
        fetch_db_credentials(ARN, client=FakeSM(secret='{"username": "x"}'))
