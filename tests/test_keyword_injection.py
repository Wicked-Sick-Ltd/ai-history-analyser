"""Regression tests for terminal control-character injection via config keywords.

A config.yaml placed in the working directory can set
``analysis.unfinished-projects.keywords`` to an invalid regular expression.
When such a keyword is compiled, Python's ``re`` module raises an error whose
text can contain raw bytes taken from the pattern (for example the
``unknown extension ?<char>`` message echoes the character after ``(?``).
If that error text is printed straight to the terminal, config-controlled
control characters reach the terminal and are interpreted by it.

These tests exercise the real analyze code path and assert that no raw
control characters escape, while the user still gets a signal that analysis
stopped.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_history_analyser.cli import cli
from ai_history_analyser.analyzers.unfinished_projects import (
    UnfinishedProjectsAnalyzer,
)
from ai_history_analyser.models import Conversation, Message, Platform

ESC = "\x1b"

# Invalid patterns that, unsanitised, make ``re.error`` carry a raw control
# byte. ``(?`` followed by a byte is reported as ``unknown extension ?<byte>``
# with the byte left raw.
MALICIOUS_KEYWORDS = [
    "(?" + ESC + "[2J[3J",       # ESC -> terminal clear-screen attempt
    "(?" + "\r" + "ALL GOOD",    # CR  -> overwrite/hide the error line
    "(?" + "\x07",               # BEL
]


def _control_bytes(text):
    """Return the raw control characters in ``text`` (newlines/tabs allowed)."""
    return [c for c in text if ord(c) < 0x20 and c not in "\n\t"]


def _combined_output(result):
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except ValueError:
        err = ""
    return out + err


def _write_export(path):
    export = [
        {
            "id": "c1",
            "title": "notes",
            "messages": [{"role": "user", "text": "I still need to finish this"}],
        }
    ]
    path.write_text(json.dumps(export), encoding="utf-8")


@pytest.mark.parametrize("payload", MALICIOUS_KEYWORDS)
def test_analyze_does_not_emit_control_bytes_from_config(tmp_path, monkeypatch, payload):
    """The exact reported vector: config.yaml in the working directory."""
    _write_export(tmp_path / "export.json")
    (tmp_path / "config.yaml").write_text(
        "analysis:\n  unfinished-projects:\n    keywords:\n    - "
        + json.dumps(payload)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["analyze", "-i", "export.json", "-p", "chatgpt"])

    output = _combined_output(result)
    leaked = _control_bytes(output)
    assert not leaked, f"control bytes leaked to the terminal: {leaked!r}"
    # The failure must still be reported so the user is not misled.
    assert "Error during analysis" in output


def _conversation():
    return Conversation(
        id="c1",
        title="notes",
        platform=Platform.CHATGPT,
        messages=[Message(role="user", content="I still need to finish this")],
    )


@pytest.mark.parametrize("payload", MALICIOUS_KEYWORDS)
def test_analyze_raises_clean_error_for_invalid_keyword(payload):
    analyzer = UnfinishedProjectsAnalyzer()
    with pytest.raises(ValueError) as excinfo:
        analyzer.analyze([_conversation()], {"keywords": [payload]})

    message = str(excinfo.value)
    assert not _control_bytes(message), message
    assert ESC not in message
    # The clean error points at the offending keyword by index, not content.
    assert "0" in message


@pytest.mark.parametrize("payload", MALICIOUS_KEYWORDS)
def test_constructor_raises_clean_error_for_invalid_keyword(payload):
    with pytest.raises(ValueError) as excinfo:
        UnfinishedProjectsAnalyzer(keywords=[payload])

    message = str(excinfo.value)
    assert not _control_bytes(message), message
    assert ESC not in message


def test_valid_custom_keywords_still_work(tmp_path, monkeypatch):
    """A valid config must keep working after the fix."""
    _write_export(tmp_path / "export.json")
    (tmp_path / "config.yaml").write_text(
        "analysis:\n  unfinished-projects:\n    keywords:\n"
        "    - \"\\\\bneed to\\\\b\"\n"
        "    - \"\\\\bfinish\\\\b\"\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["analyze", "-i", "export.json", "-p", "chatgpt"])

    assert result.exit_code == 0
    assert "Error during analysis" not in _combined_output(result)
