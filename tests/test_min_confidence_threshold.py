"""min_confidence must be a finite number in the closed range 0 through 1.

The analyzer currently copies the configured threshold and keeps a hit only when
``confidence >= threshold``. These tests describe the required behavior: an
invalid threshold aborts the analysis with ``ValueError`` before any report is
written, and a valid threshold still reports the unfinished project in the
export. They fail while the silent drop is present.

``list-unfinished`` is not covered. It never reads ``min_confidence`` from
configuration (``ai_history_analyser/cli.py`` ``list_unfinished``). The JSON and
Obsidian exporters are not covered as separate decisions: both write the
``AnalysisResult`` they are given and do not interpret the threshold.
"""

import builtins
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_history_analyser.analyzers.unfinished_projects import UnfinishedProjectsAnalyzer
from ai_history_analyser.cli import cli
from ai_history_analyser.models import Conversation, Message, Platform

# One short message hits several default patterns, so the calculated
# confidence is 1.0. A threshold above 1, NaN, or +infinity then drops it.
EXPORT_TEXT = (
    "TODO FIXME XXX HACK need to implement this. needs to add tests. "
    "next steps are unfinished and incomplete. not done. not yet complete. "
    "still need work. will implement the export. planning to build it. "
    "going to fix it. intend to create it. work in progress. WIP. "
    "partially done. missing pieces. not implemented."
)

EXPORT_DOCUMENT = {
    "id": "conv-1",
    "title": "Widget export",
    "messages": [{"role": "user", "text": EXPORT_TEXT}],
}

# YAML scalars the product's PyYAML safe loader accepts. The attack uses
# .nan, .inf, and a number above 1. The rest are the same unchecked setting:
# non-finite values, values outside 0..1, booleans (YAML 1.1 yes/no included),
# and non-numbers.
INVALID_YAML_SCALARS = [
    ".nan",
    ".NaN",
    ".inf",
    ".Inf",
    "+.inf",
    "-.inf",
    "2",
    "2.0",
    "1.01",
    "-0.1",
    "true",
    "false",
    "yes",
    "no",
    "'0.5'",
    "null",
]

VALID_YAML_SCALARS = ["0", "0.0", "0.5", "1", "1.0"]

# CLI analyzer option and the analysis: key it looks up. All three aliases
# construct UnfinishedProjectsAnalyzer and pass that section through.
ANALYZER_KEYS = ["unfinished-projects", "unfinished_projects", "unfinished"]


def _conversation():
    return Conversation(
        id="conv-1",
        title="Widget export",
        platform=Platform.CHATGPT,
        messages=[Message(role="user", content=EXPORT_TEXT)],
    )


def _section(scalar):
    loaded = yaml.safe_load("min_confidence: %s\n" % scalar)
    return loaded


def _config_text(analyzer_key, scalar):
    return "analysis:\n  %s:\n    min_confidence: %s\n" % (analyzer_key, scalar)


def _assert_hit(result):
    assert len(result.unfinished_projects) == 1
    assert result.unfinished_projects[0].confidence == 1.0
    assert result.conversations_analyzed == 1


class _ThresholdThatChanges(dict):
    """First read of min_confidence is 0.0. Every later read is NaN.

    A check that stores the value it validated keeps the hit. A check that
    looks the setting up again and filters with the later value drops it.
    """

    def __init__(self):
        super().__init__()
        self.reads = 0

    def __bool__(self):
        # An empty dict is falsy, and analyze() replaces a falsy config with {}.
        return True

    def __len__(self):
        return 1

    def __contains__(self, key):
        return key == "min_confidence"

    def __getitem__(self, key):
        if key != "min_confidence":
            raise KeyError(key)
        self.reads += 1
        if self.reads == 1:
            return 0.0
        return float("nan")

    def get(self, key, default=None):
        if key == "min_confidence":
            return self[key]
        return default

    def __eq__(self, other):
        if isinstance(other, _ThresholdThatChanges):
            return dict.__eq__(self, other) and self.reads == other.reads
        if isinstance(other, dict):
            return dict.__eq__(self, other)
        return NotImplemented

    def __ne__(self, other):
        # dict's C inequality does not call a Python __eq__, so != would
        # ignore ``reads`` unless it is defined here too.
        equal = self.__eq__(other)
        if equal is NotImplemented:
            return NotImplemented
        return not equal


class _OpenSpy:
    """Record output writes, and optionally change later reads of one config."""

    def __init__(self, real_open, output_path, config_path=None, reread_text=None):
        self.real_open = real_open
        self.output_path = Path(output_path).resolve()
        self.config_path = None if config_path is None else Path(config_path).resolve()
        self.reread_text = reread_text
        self.config_reads = 0
        self.output_writes = []

    def __call__(self, file, mode="r", *args, **kwargs):
        path = None
        if isinstance(file, (str, os.PathLike)):
            path = Path(file).resolve()
        mode_text = mode if isinstance(mode, str) else "r"
        if path == self.output_path and any(flag in mode_text for flag in ("w", "a", "x", "+")):
            self.output_writes.append(path)
        if (
            self.config_path is not None
            and path == self.config_path
            and "r" in mode_text
            and "w" not in mode_text
            and "a" not in mode_text
        ):
            self.config_reads += 1
            if self.config_reads > 1:
                return io.StringIO(self.reread_text)
        return self.real_open(file, mode, *args, **kwargs)


def _invoke(tmp_path, monkeypatch, export_path, output_path, config_text, args, reread_text=None):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text)
    spy = _OpenSpy(
        builtins.open,
        output_path,
        config_path=config_path if reread_text is not None else None,
        reread_text=reread_text,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "open", spy)
    result = CliRunner().invoke(cli, args, catch_exceptions=False)
    return result, spy


def _analyze_args(export_path, output_path, analyzer_key=None, config_path=None):
    args = [
        "analyze",
        "--input",
        str(export_path),
        "--platform",
        "chatgpt",
        "--output",
        str(output_path),
    ]
    if analyzer_key is not None and analyzer_key != "unfinished-projects":
        args.extend(["--analyzer", analyzer_key])
    if config_path is not None:
        args.extend(["--config", str(config_path)])
    return args


@pytest.fixture
def conversation():
    return _conversation()


@pytest.fixture
def export_path(tmp_path):
    path = tmp_path / "chatgpt.json"
    path.write_text(json.dumps(EXPORT_DOCUMENT))
    return path


def test_default_threshold_reports_the_unfinished_project(conversation):
    result = UnfinishedProjectsAnalyzer().analyze([conversation], {})
    _assert_hit(result)


@pytest.mark.parametrize("scalar", VALID_YAML_SCALARS)
def test_finite_threshold_inside_unit_interval_keeps_the_hit(conversation, scalar):
    result = UnfinishedProjectsAnalyzer().analyze([conversation], _section(scalar))
    _assert_hit(result)


@pytest.mark.parametrize("scalar", INVALID_YAML_SCALARS)
def test_analyze_rejects_threshold_that_would_drop_or_bypass_hits(
    conversation, scalar, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="min_confidence"):
        UnfinishedProjectsAnalyzer().analyze([conversation], _section(scalar))
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("scalar", INVALID_YAML_SCALARS)
def test_constructor_threshold_is_rejected_before_filtering(conversation, scalar):
    """Rejection may happen in the constructor or in analyze, before a hit is dropped."""
    value = _section(scalar)["min_confidence"]
    with pytest.raises(ValueError, match="min_confidence"):
        analyzer = UnfinishedProjectsAnalyzer(min_confidence=value)
        analyzer.analyze([conversation], {})


def test_config_value_is_checked_after_it_replaces_the_constructor_value(conversation):
    """Validating the original attribute and then copying config leaves the bad value in use."""
    analyzer = UnfinishedProjectsAnalyzer(min_confidence=0.5)
    with pytest.raises(ValueError, match="min_confidence"):
        analyzer.analyze([conversation], _section(".nan"))


def test_threshold_spy_equality_includes_its_read_count():
    first = _ThresholdThatChanges()
    second = _ThresholdThatChanges()
    assert first == second
    assert first == {}
    assert first.__eq__("min_confidence") is NotImplemented
    first["min_confidence"]
    assert first != second
    assert second != first
    assert first == {}


def test_threshold_is_not_read_again_between_the_check_and_the_filter(conversation):
    config = _ThresholdThatChanges()
    result = UnfinishedProjectsAnalyzer().analyze([conversation], config)
    _assert_hit(result)
    assert config.reads == 1


@pytest.mark.parametrize("analyzer_key", ANALYZER_KEYS)
@pytest.mark.parametrize("scalar", [".nan", ".inf", "2"])
def test_cli_cwd_config_does_not_publish_an_empty_success(
    tmp_path, monkeypatch, export_path, analyzer_key, scalar
):
    output_path = tmp_path / "results.json"
    result, spy = _invoke(
        tmp_path,
        monkeypatch,
        export_path,
        output_path,
        _config_text(analyzer_key, scalar),
        _analyze_args(export_path, output_path, analyzer_key=analyzer_key),
    )
    assert spy.output_writes == []
    assert not output_path.exists()
    assert result.exit_code != 0
    assert "min_confidence" in result.output
    assert "Found 0 unfinished projects" not in result.output
    assert "Exported" not in result.output


def test_cli_explicit_config_is_the_file_that_is_checked(tmp_path, monkeypatch, export_path):
    """``--config`` selects the file. A cwd config.yaml must not be substituted."""
    output_path = tmp_path / "results.json"
    cwd_config = tmp_path / "config.yaml"
    cwd_config.write_text(_config_text("unfinished-projects", ".nan"))
    chosen = tmp_path / "chosen.yaml"
    chosen.write_text(_config_text("unfinished-projects", "0.5"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtins, "open", _OpenSpy(builtins.open, output_path))
    result = CliRunner().invoke(
        cli,
        _analyze_args(export_path, output_path, config_path=chosen),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    written = json.loads(output_path.read_text())
    assert len(written["unfinished_projects"]) == 1


def test_cli_explicit_invalid_config_writes_nothing(tmp_path, monkeypatch, export_path):
    output_path = tmp_path / "results.json"
    chosen = tmp_path / "chosen.yaml"
    chosen.write_text(_config_text("unfinished-projects", ".inf"))
    # A harmless cwd file would report the project if the command ignored --config.
    (tmp_path / "config.yaml").write_text(_config_text("unfinished-projects", "0.5"))
    monkeypatch.chdir(tmp_path)
    spy = _OpenSpy(builtins.open, output_path)
    monkeypatch.setattr(builtins, "open", spy)
    result = CliRunner().invoke(
        cli,
        _analyze_args(export_path, output_path, config_path=chosen),
        catch_exceptions=False,
    )
    assert spy.output_writes == []
    assert not output_path.exists()
    assert result.exit_code != 0
    assert "min_confidence" in result.output
    assert "Found 0 unfinished projects" not in result.output


def test_cli_does_not_reread_the_config_file_for_the_threshold(tmp_path, monkeypatch, export_path):
    output_path = tmp_path / "results.json"
    result, spy = _invoke(
        tmp_path,
        monkeypatch,
        export_path,
        output_path,
        _config_text("unfinished-projects", "0.5"),
        _analyze_args(export_path, output_path),
        reread_text=_config_text("unfinished-projects", ".nan"),
    )
    assert spy.config_reads == 1
    assert result.exit_code == 0, result.output
    written = json.loads(output_path.read_text())
    assert len(written["unfinished_projects"]) == 1


def test_cli_valid_cwd_config_writes_the_hit(tmp_path, monkeypatch, export_path):
    output_path = tmp_path / "results.json"
    result, spy = _invoke(
        tmp_path,
        monkeypatch,
        export_path,
        output_path,
        _config_text("unfinished-projects", "0.5"),
        _analyze_args(export_path, output_path),
    )
    assert result.exit_code == 0, result.output
    assert spy.output_writes == [output_path.resolve()]
    written = json.loads(output_path.read_text())
    assert written["conversations_analyzed"] == 1
    assert len(written["unfinished_projects"]) == 1
    assert written["unfinished_projects"][0]["confidence"] == 1.0


def test_module_entry_point_refuses_nan_without_writing_a_report(tmp_path, export_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_config_text("unfinished-projects", ".nan"))
    output_path = tmp_path / "results.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_history_analyser",
            "analyze",
            "--input",
            str(export_path),
            "--platform",
            "chatgpt",
            "--output",
            str(output_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert not output_path.exists()
    assert "min_confidence" in combined
    assert "Found 0 unfinished projects" not in combined
    assert "Exported" not in combined
