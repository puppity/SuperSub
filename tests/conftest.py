import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


import keyring
import keyring.core
import pytest
from keyring.backend import KeyringBackend


class _MemoryKeyring(KeyringBackend):
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


@pytest.fixture(autouse=True)
def _in_memory_keyring():
    """No test may touch the real Windows Credential Manager."""
    previous = keyring.core._keyring_backend      # read the slot directly: get_keyring() would probe backends
    keyring.set_keyring(_MemoryKeyring())
    yield
    keyring.core._keyring_backend = previous
