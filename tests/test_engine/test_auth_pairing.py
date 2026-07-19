"""Security and lifecycle tests for the installed-app pairing protocol."""

from __future__ import annotations

from uuid import uuid4

import pytest

from engine.auth import pairing

SECRET = "oap_" + "A" * 43
TOKEN = "oai_ingest_" + "B" * 32
HOST = "https://app.openadapt.ai"
VALID_URI = f"openadapt://connect?pairing={SECRET}&host=https%3A%2F%2Fapp.openadapt.ai"


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def test_parser_accepts_only_the_fixed_connect_action() -> None:
    assert pairing.parse_connect_uri(VALID_URI) == {
        "pairing": SECRET,
        "host": HOST,
    }
    for uri in (
        VALID_URI.replace("://connect?", "://run?"),
        VALID_URI.replace("openadapt:", "https:"),
        VALID_URI.replace("connect?", "connect/record?"),
        VALID_URI + "#fragment",
        f"openadapt://user@connect?pairing={SECRET}&host={HOST}",
    ):
        with pytest.raises(pairing.PairingError, match="Invalid OpenAdapt connect link"):
            pairing.parse_connect_uri(uri)


def test_parser_rejects_malformed_missing_duplicate_and_unknown_fields() -> None:
    bad = (
        "",
        "openadapt://connect?pairing",
        f"openadapt://connect?pairing=short&host={HOST}",
        f"openadapt://connect?pairing={SECRET}",
        f"openadapt://connect?pairing={SECRET}&host={HOST}&pairing={SECRET}",
        f"openadapt://connect?pairing={SECRET}&host={HOST}&command=whoami",
        f"openadapt://connect?pairing={SECRET}&host={HOST}&destination_kind=customer-managed",
    )
    for uri in bad:
        with pytest.raises(pairing.PairingError):
            pairing.parse_connect_uri(uri)


def test_destination_is_exact_managed_origin_or_explicit_loopback() -> None:
    for host in (
        "https://app.openadapt.ai.evil.example",
        "https://user@app.openadapt.ai",
        "https://app.openadapt.ai/path",
        "http://app.openadapt.ai",
        "https://example.com",
    ):
        uri = f"openadapt://connect?pairing={SECRET}&host={host}"
        with pytest.raises(pairing.PairingError):
            pairing.parse_connect_uri(uri)

    local = (
        f"openadapt://connect?pairing={SECRET}&host=http%3A%2F%2Flocalhost%3A3000"
        "&destination_kind=local"
    )
    assert pairing.parse_connect_uri(local)["host"] == "http://localhost:3000"
    remote_local = (
        f"openadapt://connect?pairing={SECRET}&host=https%3A%2F%2Fexample.com"
        "&destination_kind=local"
    )
    with pytest.raises(pairing.PairingError, match="must use this computer"):
        pairing.parse_connect_uri(remote_local)


def test_argument_shaped_values_remain_data_and_cannot_select_an_action() -> None:
    for payload in (
        "--host=https://evil.example",
        "%2D%2Dhost%3Dhttps%3A%2F%2Fevil.example",
        "oap_" + "A" * 42 + ";",
    ):
        uri = f"openadapt://connect?pairing={payload}&host={HOST}"
        with pytest.raises(pairing.PairingError, match="Pairing code is malformed"):
            pairing.parse_connect_uri(uri)


def test_pairing_refuses_before_claim_when_secure_store_is_unavailable(monkeypatch) -> None:
    called = False

    def _post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("claim must not be consumed")

    monkeypatch.setattr(pairing, "secure_store_available", lambda: False)
    monkeypatch.setattr(pairing.httpx, "post", _post)
    with pytest.raises(pairing.PairingError, match="keychain"):
        pairing.connect_uri(VALID_URI)
    assert called is False


def test_connect_claims_stores_verifies_and_confirms_without_returning_token(
    monkeypatch,
) -> None:
    pairing_id = str(uuid4())
    stored = []
    posts: list[tuple[str, dict, dict]] = []

    def _post(url, *, json, headers=None, **kwargs):
        posts.append((url, json, headers or {}))
        if url.endswith("/claim"):
            assert json == {"pairing_secret": SECRET, "device_name": "test-device"}
            assert headers is None
            return _Response(200, {"ingest_token": TOKEN, "pairing_id": pairing_id})
        assert url.endswith("/confirm")
        assert json == {"pairing_id": pairing_id}
        assert headers == {"Authorization": f"Bearer {TOKEN}"}
        return _Response(200, {"connected": True})

    def _get(url, *, headers, **kwargs):
        assert url == f"{HOST}/api/needs-attention/count"
        assert headers == {"Authorization": f"Bearer {TOKEN}"}
        return _Response(200, {"count": 0})

    monkeypatch.setattr(pairing, "secure_store_available", lambda: True)
    monkeypatch.setattr(pairing, "_safe_device_name", lambda: "test-device")
    monkeypatch.setattr(
        pairing, "store_credential_secure", lambda cred: stored.append(cred) or True
    )
    monkeypatch.setattr(pairing.httpx, "post", _post)
    monkeypatch.setattr(pairing.httpx, "get", _get)

    result = pairing.connect_uri(VALID_URI)

    assert result["authenticated"] is True
    assert result["host"] == HOST
    assert "token" not in result
    assert stored[0]["token"] == TOKEN
    assert [url.rsplit("/", 1)[-1] for url, _, _ in posts] == ["claim", "confirm"]


def test_rejected_verification_removes_the_new_credential(monkeypatch) -> None:
    pairing_id = str(uuid4())
    cleared: list[str] = []

    monkeypatch.setattr(pairing, "secure_store_available", lambda: True)
    monkeypatch.setattr(pairing, "store_credential_secure", lambda cred: True)
    monkeypatch.setattr(
        pairing.httpx,
        "post",
        lambda *args, **kwargs: _Response(
            200, {"ingest_token": TOKEN, "pairing_id": pairing_id}
        ),
    )
    monkeypatch.setattr(pairing.httpx, "get", lambda *args, **kwargs: _Response(401, {}))
    monkeypatch.setattr(pairing, "clear_credential", lambda host: cleared.append(host))

    with pytest.raises(pairing.PairingError, match="removed"):
        pairing.connect_uri(VALID_URI)
    assert cleared == [HOST]
