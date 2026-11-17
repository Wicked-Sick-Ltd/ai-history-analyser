"""Exporters must not write through links or special files planted at output names"""

import json
import os
import stat
import sys
import threading
from datetime import datetime

import pytest
from click.testing import CliRunner

from ai_history_analyser import fileio
from ai_history_analyser.cli import cli
from ai_history_analyser.exporters import JSONExporter, ObsidianExporter
from ai_history_analyser.models import AnalysisResult, Platform, UnfinishedProject

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks/FIFOs")

INDEX_NAME = "Unfinished Projects Index.md"
VICTIM_CONTENT = "export PATH=/usr/bin\n"


def _result(title="Build the parser"):
    project = UnfinishedProject(
        id="p1",
        title=title,
        description="still need to finish\necho injected",
        source_conversation_id="c1",
        source_platform=Platform.CLAUDE,
        detected_at=datetime(2026, 1, 1),
        confidence=0.9,
        priority_score=0.8,
    )
    return AnalysisResult(
        platform=Platform.CLAUDE, conversations_analyzed=1, unfinished_projects=[project]
    )


@pytest.fixture
def victim(tmp_path):
    path = tmp_path / "home" / ".bashrc"
    path.parent.mkdir()
    path.write_text(VICTIM_CONTENT)
    return path


@pytest.fixture
def export_dir(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "AI Projects"
    folder.mkdir(parents=True)
    return vault, folder


@posix_only
def test_cli_obsidian_export_does_not_follow_planted_index_symlink(tmp_path, victim, export_dir):
    vault, folder = export_dir
    (folder / INDEX_NAME).symlink_to(victim)
    chat = tmp_path / "chat.json"
    chat.write_text(
        json.dumps(
            [
                {
                    "uuid": "c1",
                    "title": "Side project\necho pwned",
                    "messages": [
                        {"role": "user", "content": "TODO: I need to finish the parser"},
                        {"role": "assistant", "content": "Next steps: implement the lexer"},
                    ],
                }
            ]
        )
    )

    CliRunner().invoke(
        cli,
        ["analyze", "-i", str(chat), "-p", "claude", "--obsidian", "--obsidian-path", str(vault)],
    )

    assert victim.read_text() == VICTIM_CONTENT
    assert (folder / INDEX_NAME).is_symlink()


@posix_only
def test_obsidian_index_symlink_is_refused(victim, export_dir):
    vault, folder = export_dir
    (folder / INDEX_NAME).symlink_to(victim)

    with pytest.raises(fileio.UnsafeDestinationError):
        ObsidianExporter().export(_result(), vault)

    assert victim.read_text() == VICTIM_CONTENT


@posix_only
def test_obsidian_index_fifo_does_not_block(export_dir):
    vault, folder = export_dir
    fifo = folder / INDEX_NAME
    os.mkfifo(fifo)
    outcome = {}

    def run():
        try:
            ObsidianExporter().export(_result(), vault)
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=5)
    blocked = worker.is_alive()
    if blocked:
        # Release the writer stuck in open() so the thread can exit.
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        os.close(reader)
        worker.join(timeout=5)

    assert not blocked, "export blocked opening a named pipe at the index path"
    assert isinstance(outcome.get("error"), fileio.UnsafeDestinationError)
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)


@posix_only
def test_obsidian_project_note_symlink_is_refused(victim, export_dir):
    vault, folder = export_dir
    (folder / "Build-the-parser.md").symlink_to(victim)

    with pytest.raises(fileio.UnsafeDestinationError):
        ObsidianExporter().export(_result(), vault)

    assert victim.read_text() == VICTIM_CONTENT


@posix_only
def test_json_default_output_symlink_is_refused(tmp_path, victim):
    out = tmp_path / "out"
    out.mkdir()
    (out / "analysis_results.json").symlink_to(victim)

    with pytest.raises(fileio.UnsafeDestinationError):
        JSONExporter().export(_result(), out)

    assert victim.read_text() == VICTIM_CONTENT


@posix_only
def test_symlink_swapped_in_during_write_is_not_followed(monkeypatch, victim, export_dir):
    vault, folder = export_dir
    index = folder / INDEX_NAME
    index.write_text("old index\n")
    real_fsync = os.fsync

    def swap_then_fsync(fd):
        index.unlink()
        index.symlink_to(victim)
        real_fsync(fd)

    monkeypatch.setattr(fileio.os, "fsync", swap_then_fsync)

    with pytest.raises(fileio.UnsafeDestinationError):
        ObsidianExporter().export(_result(), vault)

    assert victim.read_text() == VICTIM_CONTENT
    assert [p.name for p in folder.iterdir()] == [INDEX_NAME]


def test_cleanup_ignores_missing_temp_file(monkeypatch, tmp_path):
    dest = tmp_path / "note.md"
    dest.write_text("keep\n")

    def fail_fsync(_fd):
        raise OSError("disk full")

    def unlink_missing(*_args, **_kwargs):
        raise FileNotFoundError("temp already gone")

    monkeypatch.setattr(fileio.os, "fsync", fail_fsync)
    monkeypatch.setattr(fileio.os, "unlink", unlink_missing)

    with pytest.raises(OSError, match="disk full"):
        fileio.write_text_file(dest, "new\n")

    assert dest.read_text() == "keep\n"


def test_existing_regular_index_is_replaced(export_dir):
    vault, folder = export_dir
    index = folder / INDEX_NAME
    index.write_text("stale\n")
    if sys.platform != "win32":
        os.chmod(index, 0o600)

    returned = ObsidianExporter().export(_result(), vault)

    assert returned == index
    text = index.read_text(encoding="utf-8")
    assert text.startswith("# Unfinished Projects Index")
    assert "Build the parser" in text
    assert (
        (folder / "Build-the-parser.md")
        .read_text(encoding="utf-8")
        .startswith("# Build the parser")
    )
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(index).st_mode) == 0o600
    assert sorted(p.name for p in folder.iterdir()) == ["Build-the-parser.md", INDEX_NAME]


def test_json_export_writes_new_file(tmp_path):
    out = tmp_path / "results.json"

    assert JSONExporter().export(_result(), out) == out
    assert json.loads(out.read_text(encoding="utf-8"))["unfinished_projects"][0]["id"] == "p1"


@posix_only
def test_init_config_does_not_follow_planted_symlink(tmp_path, victim):
    config = tmp_path / "config.yaml"
    config.symlink_to(victim)

    outcome = CliRunner().invoke(cli, ["init-config", "--output", str(config)], input="y\n")

    assert outcome.exit_code != 0
    assert "not a regular file" in outcome.output
    assert victim.read_text() == VICTIM_CONTENT


def test_init_config_writes_config(tmp_path):
    config = tmp_path / "config.yaml"

    outcome = CliRunner().invoke(cli, ["init-config", "--output", str(config)])

    assert outcome.exit_code == 0
    assert "obsidian:" in config.read_text()


def test_directory_at_destination_is_refused(export_dir):
    vault, folder = export_dir
    (folder / INDEX_NAME).mkdir()

    with pytest.raises(fileio.UnsafeDestinationError):
        ObsidianExporter().export(_result(), vault)
