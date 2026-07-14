"""Tests for the shared keychain credential store + auth_header()."""

from __future__ import annotations

import pytest

from engine.auth import store
from engine.auth.paste import PasteTokenProvider
from engine.auth.provider import AuthProvider, Credential


def _cred(host: str = "https://app.openadapt.ai", token: str = "oai_ingest_abc") -> Credential:
    return {
        "kind": "ingest_token",
        "token": token,
        "refresh_token": None,
        "org_id": "org_1",
        "host": host,
        "expires_at": None,
    }


class TestCredentialStore:
    def test_store_and_load_roundtrip(self, fake_keyring) -> None:
        store.store_credential(_cred())
        loaded = store.load_credential("https://app.openadapt.ai")
        assert loaded is not None
        assert loaded["token"] == "oai_ingest_abc"
        assert loaded["org_id"] == "org_1"

    def test_load_missing_returns_none(self, fake_keyring) -> None:
        assert store.load_credential("https://nope.example") is None

    def test_store_sets_active_host(self, fake_keyring) -> None:
        store.store_credential(_cred(host="https://h1"))
        assert store.active_host() == "https://h1"
        store.store_credential(_cred(host="https://h2", token="oai_ingest_h2"))
        assert store.active_host() == "https://h2"

    def test_clear_credential(self, fake_keyring) -> None:
        store.store_credential(_cred())
        store.clear_credential("https://app.openadapt.ai")
        assert store.load_credential("https://app.openadapt.ai") is None

    def test_corrupt_credential_ignored(self, fake_keyring) -> None:
        fake_keyring.set_password(store.SERVICE_NAME, "https://bad", "{not json")
        assert store.load_credential("https://bad") is None


class TestAuthHeader:
    def test_env_token_takes_precedence(self, fake_keyring, monkeypatch) -> None:
        store.store_credential(_cred(token="oai_ingest_stored"))
        monkeypatch.setenv("OPENADAPT_INGEST_TOKEN", "oai_ingest_env")
        assert store.auth_header() == {"Authorization": "Bearer oai_ingest_env"}

    def test_falls_back_to_active_credential(self, fake_keyring) -> None:
        store.store_credential(_cred(token="oai_ingest_stored"))
        assert store.auth_header() == {"Authorization": "Bearer oai_ingest_stored"}

    def test_empty_when_no_credential(self, fake_keyring) -> None:
        assert store.auth_header() == {}


class TestProtocolConformance:
    def test_paste_is_auth_provider(self) -> None:
        assert isinstance(PasteTokenProvider(), AuthProvider)

    def test_browser_is_auth_provider(self) -> None:
        from engine.auth.browser_pkce import BrowserPkceProvider

        assert isinstance(BrowserPkceProvider(), AuthProvider)

    def test_credential_shape(self) -> None:
        c = _cred()
        assert set(c.keys()) == {
            "kind", "token", "refresh_token", "org_id", "host", "expires_at"
        }
        with pytest.raises(KeyError):
            _ = c["nonexistent"]  # type: ignore[misc]
