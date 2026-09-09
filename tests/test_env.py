"""`.env` loading and the config report."""
from __future__ import annotations

import pytest

from nexcraftviz import env

pytest.importorskip("dotenv", reason="needs python-dotenv")


def test_finds_env_files_walking_upwards(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("OPENAI_MODEL=from-root\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / ".env").write_text("OPENAI_MODEL=from-nested\n", encoding="utf-8")

    found = env.find_env_files(nested)
    assert found[0] == nested / ".env", "nearest first"
    assert tmp_path / ".env" in found


def test_env_local_is_searched_before_env(tmp_path) -> None:
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("A=2\n", encoding="utf-8")
    assert env.find_env_files(tmp_path)[0].name == ".env.local"


def test_a_file_loads_into_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENAI_MODEL=gpt-from-file\n", encoding="utf-8")

    loaded = env.load(tmp_path)
    assert loaded
    import os

    assert os.getenv("OPENAI_MODEL") == "gpt-from-file"


def test_an_exported_variable_beats_the_file(tmp_path, monkeypatch) -> None:
    """A stale .env silently beating the key you just exported is a genuinely
    horrible half-hour."""
    import os

    monkeypatch.setenv("OPENAI_MODEL", "exported")
    (tmp_path / ".env").write_text("OPENAI_MODEL=from-file\n", encoding="utf-8")

    env.load(tmp_path)
    assert os.getenv("OPENAI_MODEL") == "exported"


def test_loading_is_not_automatic_on_import() -> None:
    """A library that reads files off disk at import time is a surprise, and in
    a server it is a surprise that happens once and never reflects a change."""
    import inspect

    import nexcraftviz

    source = inspect.getsource(nexcraftviz)
    assert "env.load()" not in source
    assert "load_dotenv" not in source


def test_describe_provider_reports_a_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "no OPENAI_API_KEY" in env.describe_provider()


def test_describe_provider_never_prints_the_whole_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-1234")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")

    described = env.describe_provider()
    assert "sk-secret-value-1234" not in described
    assert "1234" in described and "gpt-5-mini" in described


def test_the_config_command_runs_without_a_key(capsys, monkeypatch) -> None:
    from nexcraftviz import cli

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cli.main(["--no-dotenv", "config"]) == 0
    out = capsys.readouterr().out
    assert "no OPENAI_API_KEY set" in out
    assert "themes:" in out
