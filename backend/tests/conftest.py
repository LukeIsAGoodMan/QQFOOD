import os
import tempfile
import uuid

import pytest

os.environ["QQ_DATA_DIR"] = tempfile.mkdtemp(prefix="qq597test_")
os.environ["QQ_ORG_MODE"] = "standard"  # most tests check role separation; test_simple_mode covers the default

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%test\n"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def login(client):
    cache = {}

    def _login(username):
        if username not in cache:
            r = client.post("/api/auth/login", json={"username": username, "password": "qq597demo"})
            assert r.status_code == 200, r.text
            cache[username] = {"Authorization": f"Bearer {r.json()['token']}"}
        return cache[username]
    return _login


def key():
    return str(uuid.uuid4())


def png(n=0):
    return PNG + str(n).encode() + uuid.uuid4().bytes
