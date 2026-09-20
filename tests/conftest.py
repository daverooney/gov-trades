import pytest

from gov_trades.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        contact_email="test@example.invalid",
        request_delay_seconds=2.0,
        request_jitter_seconds=0.5,
        request_timeout_seconds=5.0,
        storage_local_root=tmp_path / "store",
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_account_endpoint=None,
        r2_bucket=None,
    )
