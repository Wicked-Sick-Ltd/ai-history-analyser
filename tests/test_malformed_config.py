"""Spec for analyze's handling of config.yaml.

These tests invoke the real ``analyze`` command. They fail while a malformed
document is still used as nested settings: a non-mapping crashes with an
uncaught AttributeError after the chat export has been parsed, and a
wrong-typed keyword or minimum confidence is printed and then reported as
success (exit code 0) with no export.

Not covered here, because those commands never load config.yaml:

- ``list_unfinished`` (``ai_history_analyser/cli.py``) parses and analyzes
  with built-in defaults only.
- ``init_config`` writes a sample file and does not read settings.

Also left untested: a wrong-typed ``exporters.json`` value. The JSON exporter
does not read its config mapping, so that value never changes the run.
A parse error inside ``analyze`` uses the same "print and return" exit as a
failed analysis, but the reported input is config.yaml, not the chat file.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# content.parts is required. A string ``content`` makes ChatGPTParser raise
# before a conversation exists, and analyze then returns without ever reading
# the analysis section.
CHAT_EXPORT = [
    {
        "id": "c1",
        "title": "Ship the exporter",
        "messages": [
            {
                "role": "user",
                "content": {
                    "parts": ["We still need to finish the exporter. TODO implement the rest."]
                },
            }
        ],
    }
]

VALID_SETTINGS = """
analysis:
  unfinished-projects:
    keywords:
      - "this-will-not-match-zz"
    min_confidence: 0.5
"""


def _write_chat(directory: Path) -> Path:
    path = directory / "chat.json"
    path.write_text(json.dumps(CHAT_EXPORT), encoding="utf-8")
    return path


def _run_analyze(directory: Path, args):
    return subprocess.run(
        [sys.executable, "-m", "ai_history_analyser", "analyze", *args],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )


def _combined(result) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _assert_clean_failure(result, artifact: Path):
    """A rejected run exits non-zero, writes nothing, and does not crash."""
    text = _combined(result)
    assert result.returncode != 0, text
    assert "Traceback (most recent call last)" not in text
    assert "AttributeError" not in text
    assert "✓ Exported" not in text
    assert "Found " not in text
    assert "error" in text.lower() or "invalid" in text.lower()
    assert not artifact.exists()


def _assert_success(result, artifact: Path):
    text = _combined(result)
    assert result.returncode == 0, text
    assert "Traceback (most recent call last)" not in text
    assert artifact.exists()
    assert artifact.is_file()


def _analyze_to(tmp_path: Path, config_text, output: Path, extra_args=()):
    chat = _write_chat(tmp_path)
    if config_text is not None:
        (tmp_path / "config.yaml").write_text(config_text, encoding="utf-8")
    return _run_analyze(
        tmp_path,
        ["--input", str(chat), "--platform", "chatgpt", "--output", str(output), *extra_args],
    )


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("true\n", id="true"),
        pytest.param("false\n", id="false"),
        pytest.param("1\n", id="number"),
        pytest.param("0\n", id="zero"),
        pytest.param("planted\n", id="string"),
        pytest.param("[]\n", id="empty-list"),
        pytest.param("- planted\n", id="nonempty-list"),
        pytest.param("''\n", id="empty-string"),
    ],
)
def test_non_mapping_document_in_working_directory_is_rejected(tmp_path, document):
    output = tmp_path / "results.json"
    result = _analyze_to(tmp_path, document, output)
    _assert_clean_failure(result, output)


def test_explicit_config_path_rejects_non_mapping_even_if_cwd_config_is_valid(tmp_path):
    output = tmp_path / "results.json"
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    planted = tmp_path / "planted.yaml"
    planted.write_text("true\n", encoding="utf-8")
    result = _run_analyze(
        tmp_path,
        [
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--output",
            str(output),
            "--config",
            str(planted),
        ],
    )
    _assert_clean_failure(result, output)


def test_explicit_valid_config_is_used_instead_of_a_planted_cwd_file(tmp_path):
    output = tmp_path / "results.json"
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text("true\n", encoding="utf-8")
    chosen = tmp_path / "chosen.yaml"
    chosen.write_text(VALID_SETTINGS, encoding="utf-8")
    result = _run_analyze(
        tmp_path,
        [
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--output",
            str(output),
            "--config",
            str(chosen),
        ],
    )
    _assert_success(result, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["conversations_analyzed"] == 1
    assert payload["unfinished_projects"] == []


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(None, id="missing-file"),
        pytest.param("", id="empty-file"),
        pytest.param("null\n", id="null-document"),
        pytest.param("{}\n", id="empty-mapping"),
        pytest.param("analysis: null\n", id="null-analysis"),
        pytest.param("analysis: {}\n", id="empty-analysis"),
        pytest.param("exporters: null\n", id="null-exporters"),
        pytest.param(
            "analysis:\n  unfinished-projects: null\n",
            id="null-selected-analyzer",
        ),
        pytest.param(VALID_SETTINGS, id="valid-settings"),
    ],
)
def test_empty_or_null_sections_still_export(tmp_path, document):
    output = tmp_path / "results.json"
    result = _analyze_to(tmp_path, document, output)
    _assert_success(result, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["conversations_analyzed"] == 1
    if document == VALID_SETTINGS:
        assert payload["unfinished_projects"] == []
    else:
        assert payload["unfinished_projects"]


def test_null_obsidian_section_still_exports(tmp_path):
    vault = tmp_path / "vault"
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text("obsidian: null\n", encoding="utf-8")
    result = _run_analyze(
        tmp_path,
        [
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--output",
            str(vault),
        ],
    )
    index = vault / "AI Projects" / "Unfinished Projects Index.md"
    _assert_success(result, index)


def test_null_obsidian_exporter_entry_still_exports(tmp_path):
    vault = tmp_path / "vault"
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "exporters:\n  obsidian: null\n",
        encoding="utf-8",
    )
    result = _run_analyze(
        tmp_path,
        [
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--output",
            str(vault),
        ],
    )
    index = vault / "AI Projects" / "Unfinished Projects Index.md"
    _assert_success(result, index)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("analysis: []\n", id="analysis-list"),
        pytest.param("analysis: planted\n", id="analysis-string"),
        pytest.param("analysis: true\n", id="analysis-bool"),
        pytest.param("analysis: 1\n", id="analysis-number"),
        pytest.param("exporters: []\n", id="exporters-list"),
        pytest.param("exporters: planted\n", id="exporters-string"),
        pytest.param("exporters: true\n", id="exporters-bool"),
        pytest.param("exporters: 1\n", id="exporters-number"),
    ],
)
def test_non_mapping_section_is_rejected_before_export(tmp_path, document):
    output = tmp_path / "results.json"
    result = _analyze_to(tmp_path, document, output)
    _assert_clean_failure(result, output)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("obsidian: []\n", id="obsidian-list"),
        pytest.param("obsidian: planted\n", id="obsidian-string"),
        pytest.param("obsidian: true\n", id="obsidian-bool"),
        pytest.param("obsidian: 1\n", id="obsidian-number"),
        pytest.param("exporters:\n  obsidian: []\n", id="obsidian-exporter-list"),
        pytest.param("exporters:\n  obsidian: planted\n", id="obsidian-exporter-string"),
    ],
)
def test_non_mapping_obsidian_settings_are_rejected_without_creating_a_vault(tmp_path, document):
    vault = tmp_path / "vault"
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text(document, encoding="utf-8")
    result = _run_analyze(
        tmp_path,
        [
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--output",
            str(vault),
        ],
    )
    _assert_clean_failure(result, vault)


def _analyzer_document(analyzer_key: str, body: str) -> str:
    indented = "\n".join(f"    {line}" if line else "" for line in body.strip("\n").split("\n"))
    return f"analysis:\n  {analyzer_key}:\n{indented}\n"


@pytest.mark.parametrize(
    "analyzer_key, extra_args",
    [
        ("unfinished-projects", ()),
        ("unfinished_projects", ("--analyzer", "unfinished_projects")),
    ],
)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param("keywords: 5\n", id="keywords-number"),
        pytest.param("keywords: TODO\n", id="keywords-string"),
        pytest.param("keywords: null\n", id="keywords-null"),
        pytest.param("keywords:\n  - TODO\n  - 1\n", id="keywords-mixed-list"),
        pytest.param('keywords:\n  - "("\n', id="keywords-invalid-pattern"),
        pytest.param("min_confidence: high\n", id="confidence-string"),
        pytest.param("min_confidence:\n  - 0.5\n", id="confidence-list"),
        pytest.param("min_confidence: true\n", id="confidence-bool"),
        pytest.param("min_confidence: null\n", id="confidence-null"),
    ],
)
def test_wrong_typed_analyzer_settings_exit_nonzero_without_export(
    tmp_path, analyzer_key, extra_args, body
):
    output = tmp_path / "results.json"
    result = _analyze_to(tmp_path, _analyzer_document(analyzer_key, body), output, extra_args)
    _assert_clean_failure(result, output)


def test_wrong_typed_keywords_fail_even_when_no_output_was_requested(tmp_path):
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "analysis:\n  unfinished-projects:\n    keywords: 5\n",
        encoding="utf-8",
    )
    result = _run_analyze(
        tmp_path,
        ["--input", str(chat), "--platform", "chatgpt"],
    )
    text = _combined(result)
    assert result.returncode != 0, text
    assert "Traceback (most recent call last)" not in text
    assert "Analysis Summary" not in text
    assert "error" in text.lower() or "invalid" in text.lower()


def test_export_io_failure_exits_nonzero_and_writes_nothing(tmp_path):
    """Analysis may already have succeeded; the failed export must not."""
    output = tmp_path / "missing" / "results.json"
    result = _analyze_to(tmp_path, "{}\n", output)
    text = _combined(result)
    assert result.returncode != 0, text
    assert "Traceback (most recent call last)" not in text
    assert "✓ Exported" not in text
    assert "error" in text.lower()
    assert not output.exists()


def test_obsidian_folder_of_the_wrong_type_exits_nonzero_without_an_index(tmp_path):
    vault = tmp_path / "vault"
    chat = _write_chat(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "obsidian:\n  folder: 1\n",
        encoding="utf-8",
    )
    result = _run_analyze(
        tmp_path,
        [
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--output",
            str(vault),
        ],
    )
    text = _combined(result)
    assert result.returncode != 0, text
    assert "Traceback (most recent call last)" not in text
    assert "✓ Exported" not in text
    # The vault directory is created before export. A wrong-typed folder must
    # be refused before that directory exists.
    assert not vault.exists()
