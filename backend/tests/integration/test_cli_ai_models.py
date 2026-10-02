"""``python -m app.cli ai-models`` against the test database (sync tests: the CLI runs its own loop)."""

from __future__ import annotations

from app import cli


def test_ai_models_lists_registry_and_checks_mock_ids(settings, monkeypatch, capsys):
    monkeypatch.setattr(settings, "ai_provider", "mock")
    assert cli.main(["ai-models"]) == 0
    out = capsys.readouterr().out
    assert "Active AI provider: mock" in out
    mock_line = next(line for line in out.splitlines() if "mock-vision" in line)
    assert "AVAILABLE" in mock_line
    openai_line = next(line for line in out.splitlines() if " vision-standard " in line)
    assert "gpt-6-sol" in openai_line and "price unknown" in openai_line
    banana = next(line for line in out.splitlines() if "vc-nano-banana-pro" in line)
    assert "vibecode" in banana and "$0.059049/image (default)" in banana


def test_ai_models_not_configured(settings, monkeypatch, capsys):
    monkeypatch.setattr(settings, "ai_provider", None)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    assert cli.main(["ai-models"]) == 0
    assert "Active AI provider: not configured" in capsys.readouterr().out
