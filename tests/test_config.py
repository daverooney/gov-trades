import pytest

from gov_trades.config import load_settings


def test_requires_contact_email(monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="CONTACT_EMAIL"):
        load_settings()


def test_defaults_and_r2_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTACT_EMAIL", "x@y.z")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))
    for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ENDPOINT", "R2_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.request_delay_seconds == 2.0
    assert s.storage_local_root == tmp_path
    assert not s.r2_configured
    assert "x@y.z" in s.user_agent


def test_r2_configured_needs_all_four(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "x@y.z")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "a")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "b")
    monkeypatch.setenv("R2_ACCOUNT_ENDPOINT", "https://e")
    monkeypatch.delenv("R2_BUCKET", raising=False)
    assert not load_settings().r2_configured
    monkeypatch.setenv("R2_BUCKET", "gov-trades")
    assert load_settings().r2_configured
