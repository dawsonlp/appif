"""Unit tests for the shared :mod:`appif.config` discovery module."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from appif import config


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """Point config discovery at an isolated temp dir and return it."""
    d = tmp_path / "cfg"
    monkeypatch.setenv("APPIF_CONFIG_DIR", str(d))
    return d


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


class TestConfigDir:
    def test_explicit_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPIF_CONFIG_DIR", str(tmp_path / "custom"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert config.config_dir() == tmp_path / "custom"

    def test_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("APPIF_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert config.config_dir() == tmp_path / "xdg" / "appif"

    def test_default_under_home(self, monkeypatch):
        monkeypatch.delenv("APPIF_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert config.config_dir() == Path.home() / ".config" / "appif"

    def test_service_paths(self, cfg_dir):
        assert config.service_dir("gmail") == cfg_dir / "gmail"
        assert config.service_config_path("gmail") == cfg_dir / "gmail" / "config.yaml"


class TestLoadEnv:
    def test_loads_values_from_file(self, tmp_path, monkeypatch):
        env = tmp_path / "my.env"
        env.write_text("APPIF_TEST_TOKEN=abc123\n")
        monkeypatch.setenv("APPIF_ENV_FILE", str(env))
        monkeypatch.delenv("APPIF_TEST_TOKEN", raising=False)
        config._env_loaded = False

        loaded = config.load_env()
        assert loaded == env
        import os

        assert os.environ["APPIF_TEST_TOKEN"] == "abc123"

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPIF_ENV_FILE", str(tmp_path / "nope.env"))
        config._env_loaded = False
        assert config.load_env() is None

    def test_idempotent(self, tmp_path, monkeypatch):
        env = tmp_path / "my.env"
        env.write_text("APPIF_TEST_X=1\n")
        monkeypatch.setenv("APPIF_ENV_FILE", str(env))
        config._env_loaded = False
        assert config.load_env() == env
        # Second call is a no-op (already loaded) and returns None.
        assert config.load_env() is None


class TestServiceConfig:
    def test_missing_returns_empty(self, cfg_dir):
        assert config.load_service_config("gmail") == {}

    def test_non_dict_returns_empty(self, cfg_dir):
        _write(config.service_config_path("gmail"), "- just\n- a\n- list\n")
        assert config.load_service_config("gmail") == {}

    def test_valid_yaml(self, cfg_dir):
        _write(
            config.service_config_path("outlook"),
            """
            accounts:
              work:
                client_id: cid-work
            default: work
            """,
        )
        data = config.load_service_config("outlook")
        assert data["default"] == "work"
        assert data["accounts"]["work"]["client_id"] == "cid-work"

    def test_account_names(self, cfg_dir):
        _write(
            config.service_config_path("slack"),
            """
            accounts:
              labs: {bot_oauth_token: x}
              prod: {bot_oauth_token: y}
            default: labs
            """,
        )
        assert set(config.account_names("slack")) == {"labs", "prod"}


class TestSelectAccount:
    def _write_outlook(self):
        _write(
            config.service_config_path("outlook"),
            """
            accounts:
              default: {client_id: cid-default}
              work: {client_id: cid-work}
            default: work
            """,
        )

    def test_explicit_account_wins(self, cfg_dir):
        self._write_outlook()
        name, settings = config.select_account("outlook", "default", env_account_var="APPIF_OUTLOOK_ACCOUNT")
        assert name == "default"
        assert settings["client_id"] == "cid-default"

    def test_env_var_selects(self, cfg_dir, monkeypatch):
        self._write_outlook()
        monkeypatch.setenv("APPIF_OUTLOOK_ACCOUNT", "default")
        name, settings = config.select_account("outlook", env_account_var="APPIF_OUTLOOK_ACCOUNT")
        assert name == "default"

    def test_yaml_default_used(self, cfg_dir):
        self._write_outlook()
        name, settings = config.select_account("outlook", env_account_var="APPIF_OUTLOOK_ACCOUNT")
        assert name == "work"
        assert settings["client_id"] == "cid-work"

    def test_fallback_when_no_config(self, cfg_dir):
        name, settings = config.select_account("outlook")
        assert name == "default"
        assert settings == {}

    def test_custom_fallback_empty(self, cfg_dir):
        name, settings = config.select_account("gmail", fallback="")
        assert name == ""
        assert settings == {}

    def test_instances_section_for_jira(self, cfg_dir):
        _write(
            config.service_config_path("jira"),
            """
            instances:
              personal: {jira: {url: https://x}}
            default: personal
            """,
        )
        name, settings = config.select_account("jira", section="instances")
        assert name == "personal"
        assert settings["jira"]["url"] == "https://x"
