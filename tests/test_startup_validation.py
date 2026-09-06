"""Tests for api.main's stricter production startup validation.

Ported from excellerate-bot's config.validate_for_runtime() pattern: these
checks are only enforced when APP_ENV=production is explicitly set, so the
default (unset APP_ENV) dev/test behavior is completely unaffected.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from api.main import _validate_production_config, _validate_startup


class _StubStore:
    primary_db = "TestDB"

    def ping(self) -> bool:
        return True


class TestValidateProductionConfig:
    def test_passes_with_strong_secret_and_no_wildcard_cors(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-unique-password")
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
        _validate_production_config("x" * 32)

    def test_short_jwt_secret_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-unique-password")
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
        with pytest.raises(RuntimeError, match="JWT_SECRET must be at least 32"):
            _validate_production_config("short-secret")

    def test_default_admin_password_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD must not be the default"):
            _validate_production_config("x" * 32)

    def test_wildcard_cors_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-unique-password")
        monkeypatch.setenv("ALLOWED_ORIGINS", "*")
        with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must not be '\\*'"):
            _validate_production_config("x" * 32)

    def test_wildcard_among_multiple_origins_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-unique-password")
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com,*")
        with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must not be '\\*'"):
            _validate_production_config("x" * 32)

    def test_multiple_problems_all_reported(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        monkeypatch.setenv("ALLOWED_ORIGINS", "*")
        with pytest.raises(RuntimeError) as exc_info:
            _validate_production_config("short")
        message = str(exc_info.value)
        assert "JWT_SECRET" in message
        assert "ADMIN_PASSWORD" in message
        assert "ALLOWED_ORIGINS" in message


class TestValidateStartupAppEnvGate:
    def test_development_default_skips_production_checks(self, monkeypatch):
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("JWT_SECRET", "short-but-fine-in-dev")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        monkeypatch.setenv("ALLOWED_ORIGINS", "*")
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            _validate_startup(_StubStore())  # must not raise

    def test_production_app_env_enforces_checks(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SECRET", "short")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        monkeypatch.setenv("ALLOWED_ORIGINS", "*")
        with pytest.raises(RuntimeError, match="Invalid production configuration"):
            _validate_startup(_StubStore())

    def test_production_app_env_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "PRODUCTION")
        monkeypatch.setenv("JWT_SECRET", "short")
        monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
        monkeypatch.setenv("ALLOWED_ORIGINS", "*")
        with pytest.raises(RuntimeError, match="Invalid production configuration"):
            _validate_startup(_StubStore())
