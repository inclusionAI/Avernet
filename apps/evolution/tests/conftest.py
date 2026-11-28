import secrets

import pytest

from avernet_evolution.config import Settings


@pytest.fixture
def settings(tmp_path):
    key = tmp_path / "worker-key"
    key.write_text(secrets.token_hex(32))
    token = tmp_path / "user-token"
    token.write_text(secrets.token_hex(32))
    return Settings(
        database=tmp_path / "evolution.db", signing_key_file=key, local_token_file=token, consumer=False
    )
