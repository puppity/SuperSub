import keyring
import pytest
from keyring.backend import KeyringBackend

from subtitle_translator.core import settings as st
from subtitle_translator.core.pricing import DEFAULT_MODEL, MODELS, cost_usd


def test_cost_math():
    assert DEFAULT_MODEL == "claude-opus-5-5" and MODELS[DEFAULT_MODEL].recommended
    assert cost_usd("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert cost_usd("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.20)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_write=1_000_000) == pytest.approx(5.0)


def test_settings_roundtrip_ignores_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    s = st.Settings(backend="claude_code", chunk_size=60)
    st.save_settings(s, path)
    assert st.load_settings(path) == s
    path.write_text('{"backend": "api", "bogus": 1}', encoding="utf-8")
    assert st.load_settings(path).backend == "api"
    assert st.load_settings(tmp_path / "none.json") == st.Settings()
    assert "key" not in path.read_text(encoding="utf-8").lower()
    path.write_text("[]", encoding="utf-8")
    assert st.load_settings(path) == st.Settings()


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


def test_api_key_uses_keyring():
    keyring.set_keyring(MemoryKeyring())
    assert st.get_api_key() is None
    st.set_api_key("  sk-ant-test  ")
    assert st.get_api_key() == "sk-ant-test"
    st.clear_api_key()
    assert st.get_api_key() is None
