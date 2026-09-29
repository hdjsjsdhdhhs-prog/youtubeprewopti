import pytest

from app.providers.storage import LocalFSStorage
from app.providers.storage.base import content_key, sha256_hex


def test_put_is_content_addressed_and_deduplicated(tmp_path):
    s = LocalFSStorage(tmp_path)
    first = s.put(b"hello", "PNG")
    assert first.created
    assert first.key == f"images/{sha256_hex(b'hello')[:2]}/{sha256_hex(b'hello')}.png"
    again = s.put(b"hello", ".png")
    assert not again.created and again.key == first.key
    assert s.get(first.key) == b"hello"
    assert s.exists(first.key)
    assert not list(tmp_path.rglob(".tmp-*")), "temporary files must not be left behind"


def test_delete_is_idempotent(tmp_path):
    s = LocalFSStorage(tmp_path)
    key = s.put(b"x", "jpg").key
    s.delete(key)
    s.delete(key)
    assert not s.exists(key)


@pytest.mark.parametrize("key", ["../outside.txt", "images/../../outside.txt", "C:/Windows/win.ini"])
def test_keys_cannot_escape_root(tmp_path, key):
    s = LocalFSStorage(tmp_path / "root")
    with pytest.raises(ValueError):
        s.get(key)


@pytest.mark.parametrize("ext", ["", "p/ng", "../x", "toolong", "png;"])
def test_invalid_extensions_rejected(ext):
    with pytest.raises(ValueError):
        content_key("a" * 64, ext)
