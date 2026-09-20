import pytest

from gov_trades.storage import LocalStorage, storage_from_settings


def test_local_round_trip(tmp_path):
    s = LocalStorage(tmp_path)
    key = "raw/house/2026/P/20031234.pdf"
    assert not s.exists(key)
    s.put(key, b"%PDF-")
    assert s.exists(key)
    assert s.get(key) == b"%PDF-"
    assert (tmp_path / key).is_file()
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("key", ["", "/abs", "a/../b"])
def test_rejects_bad_keys(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put(key, b"x")


def test_settings_pick_local_when_r2_unset(settings):
    assert isinstance(storage_from_settings(settings), LocalStorage)
