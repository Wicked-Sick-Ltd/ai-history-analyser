"""Symlink confinement for Obsidian and JSON exports.

These tests are the expected behavior for chat-note export. They fail while
the exporter follows a directory or file symlink and writes conversation
excerpts through it. A later fix should make them pass.

A check that the path is safe, followed by another lookup of that same path,
is not enough. The open and mkdir hooks below replace the looked-up entry
after any earlier stat and before the kernel uses the path.
"""

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

from ai_history_analyser.analyzers.unfinished_projects import UnfinishedProjectsAnalyzer
from ai_history_analyser.cli import cli
from ai_history_analyser.exporters.json import JSONExporter
from ai_history_analyser.exporters.obsidian import ObsidianExporter
from ai_history_analyser.models import AnalysisResult, Platform, UnfinishedProject
from ai_history_analyser.parsers.chatgpt import ChatGPTParser

MARKER = "PRIVATE_PROMPT_MARKER"
INDEX_NAME = "Unfinished Projects Index.md"
JSON_NAME = "analysis_results.json"
PROJECT_TITLE = "Widget plan"
PROJECT_NOTE = "Widget-plan.md"

# Armed only inside attack(). The hook stays installed because audit hooks
# cannot be removed, and it does nothing while mode is None.
_ATTACK = {
    "mode": None,
    "export_dir": None,
    "outside": None,
    "note_names": (),
    "mkdir_path": None,
    "done": False,
}


def _path_text(value):
    if isinstance(value, bytes):
        return os.fsdecode(value)
    if isinstance(value, int):
        return ""
    return os.fspath(value)


def _audit(event, args):
    mode = _ATTACK["mode"]
    if mode is None or _ATTACK["done"]:
        return
    if mode == "swap-dir-on-mkdir":
        if event != "os.mkdir":
            return
        text = _path_text(args[0])
        if text != str(_ATTACK["mkdir_path"]):
            return
        os.symlink(
            _ATTACK["outside"],
            text,
            target_is_directory=True,
        )
        _ATTACK["done"] = True
        return
    if event != "open":
        return
    text = _path_text(args[0])
    if mode == "swap-exact-on-open":
        if text != str(_ATTACK["target_path"]):
            return
        if not os.path.lexists(text):
            os.symlink(_ATTACK["outside_file"], text)
        _ATTACK["done"] = True
        return
    name = os.path.basename(text)
    if name not in _ATTACK["note_names"]:
        return
    parent = os.path.dirname(text)
    export_dir = _ATTACK["export_dir"]
    if parent and parent != str(export_dir):
        return
    if mode == "swap-dir-on-open":
        if not export_dir.is_symlink():
            held = export_dir.with_name(export_dir.name + ".held")
            export_dir.rename(held)
            export_dir.symlink_to(_ATTACK["outside"], target_is_directory=True)
            _ATTACK["held"] = held
        _ATTACK["done"] = True
        return
    if mode == "swap-file-on-open":
        # Plant every note symlink before this open returns so a later note
        # is already a symlink when its own open looks the name up again.
        for note in _ATTACK["note_names"]:
            dest = export_dir / note
            if not dest.exists() and not dest.is_symlink():
                os.symlink(_ATTACK["outside"] / note, dest)
        _ATTACK["done"] = True


sys.addaudithook(_audit)


@contextmanager
def attack(mode, **kwargs):
    previous = dict(_ATTACK)
    _ATTACK.update(previous)
    _ATTACK["mode"] = mode
    _ATTACK["done"] = False
    _ATTACK["held"] = None
    _ATTACK.update(kwargs)
    # A separate dict so the caller can read `done` after this block resets
    # the hook. The hook writes `_ATTACK`, and `finally` copies that flag here.
    observed = {"done": False}
    try:
        yield observed
    finally:
        observed["done"] = _ATTACK["done"]
        _ATTACK.clear()
        _ATTACK.update(previous)
        _ATTACK["mode"] = None
        _ATTACK["done"] = False


def tree_snapshot(root: Path) -> dict:
    """Record symlink targets and file bytes without following directory links."""
    recorded = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        for name in dirnames:
            path = current / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                recorded[rel] = ("symlink", os.readlink(path))
            else:
                recorded[rel] = ("dir",)
        for name in filenames:
            path = current / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                recorded[rel] = ("symlink", os.readlink(path))
            else:
                info = path.lstat()
                recorded[rel] = ("file", info.st_ino, info.st_mode, path.read_bytes())
    return recorded


def assert_untouched(root: Path, before: dict, label: str):
    after = tree_snapshot(root)
    if after == before:
        return
    leaked = []
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if MARKER.encode() in data:
                leaked.append(f"{path} mode={oct(path.stat().st_mode)}")
    raise AssertionError(
        f"{label} changed files outside the vault.\n"
        f"leaked={leaked}\nbefore={before!r}\nafter={after!r}"
    )


def assert_symlink(link: Path, target: Path):
    assert link.is_symlink(), f"{link} was replaced or removed"
    assert os.readlink(link) == os.fspath(target)


def sample_result() -> AnalysisResult:
    project = UnfinishedProject(
        id="proj-1",
        title=PROJECT_TITLE,
        description=f"{MARKER} need to implement the widget",
        source_conversation_id="conv-secret",
        source_platform=Platform.CHATGPT,
        detected_at=datetime(2024, 1, 2, 3, 4, 5),
        confidence=0.9,
        keywords_found=["TODO"],
        context=f"user pasted {MARKER} into the chat",
        priority_score=0.91,
        tags=["chatgpt"],
    )
    return AnalysisResult(
        platform=Platform.CHATGPT,
        conversations_analyzed=1,
        unfinished_projects=[project],
        analysis_date=datetime(2024, 1, 2, 3, 4, 5),
    )


def write_chat(path: Path) -> str:
    path.write_text(
        json.dumps(
            [
                {
                    "id": "conv-1",
                    "title": "Widget plan",
                    "create_time": 1700000000,
                    "update_time": 1700000000,
                    "mapping": {
                        "m1": {
                            "id": "m1",
                            "create_time": 1700000000,
                            "message": {
                                "author": {"role": "user"},
                                "content": {
                                    "content_type": "text",
                                    "parts": [
                                        f"{MARKER} need to implement the widget. "
                                        "TODO finish it. not done."
                                    ],
                                },
                                "create_time": 1700000000,
                            },
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    conversations = ChatGPTParser().parse(path)
    result = UnfinishedProjectsAnalyzer().analyze(conversations)
    assert result.unfinished_projects, "fixture must produce a project note"
    return ObsidianExporter()._get_project_filename(result.unfinished_projects[0]) + ".md"


def seed_files(directory: Path, names: list) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o755)
    for name in names:
        (directory / name).write_text(f"SENTINEL-{name}", encoding="utf-8")
    return tree_snapshot(directory)


def call_export(action):
    """Run an export and keep going so side effects are visible if it returns."""
    try:
        action()
    except Exception as exc:
        return exc
    return None


def test_cli_obsidian_rejects_ai_projects_directory_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    chat = tmp_path / "chat.json"
    project_note = write_chat(chat)
    before = seed_files(outside, [INDEX_NAME, project_note])
    link = vault / "AI Projects"
    link.symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--obsidian-path",
            str(vault),
        ],
    )

    assert_untouched(outside, before, "default AI Projects symlink")
    assert_symlink(link, outside)
    assert "✓ Exported" not in result.output


def test_cli_obsidian_rejects_configured_folder_directory_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    chat = tmp_path / "chat.json"
    project_note = write_chat(chat)
    before = seed_files(outside, [INDEX_NAME, project_note])
    link = vault / "Custom Notes"
    link.symlink_to(outside, target_is_directory=True)
    config = tmp_path / "config.yaml"
    config.write_text("obsidian:\n  folder: Custom Notes\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--obsidian-path",
            str(vault),
            "--config",
            str(config),
        ],
    )

    assert_untouched(outside, before, "configured folder symlink")
    assert_symlink(link, outside)
    assert "✓ Exported" not in result.output


def test_cli_obsidian_rejects_nested_folder_symlink_component(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    chat = tmp_path / "chat.json"
    project_note = write_chat(chat)
    nested_outside = outside / "Child"
    seed_files(nested_outside, [INDEX_NAME, project_note])
    before = tree_snapshot(outside)
    link = vault / "linked"
    link.symlink_to(outside, target_is_directory=True)
    config = tmp_path / "config.yaml"
    config.write_text("obsidian:\n  folder: linked/Child\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--obsidian-path",
            str(vault),
            "--config",
            str(config),
        ],
    )

    assert_untouched(outside, before, "nested folder symlink")
    assert_symlink(link, outside)
    assert "✓ Exported" not in result.output


def test_cli_obsidian_rejects_symlink_vault_path(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)
    vault = tmp_path / "vault-link"
    vault.symlink_to(outside, target_is_directory=True)
    chat = tmp_path / "chat.json"
    write_chat(chat)

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--obsidian-path",
            str(vault),
        ],
    )

    assert_untouched(outside, before, "vault path symlink")
    assert_symlink(vault, outside)
    assert "✓ Exported" not in result.output


def test_cli_obsidian_rejects_parent_symlink_when_creating_vault(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)
    parent = tmp_path / "parent"
    parent.mkdir()
    link = parent / "linked"
    link.symlink_to(outside, target_is_directory=True)
    vault = link / "NewVault"
    chat = tmp_path / "chat.json"
    write_chat(chat)

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--obsidian",
            "--obsidian-path",
            str(vault),
        ],
    )

    assert_untouched(outside, before, "parent symlink while creating the vault")
    assert_symlink(link, outside)
    assert "✓ Exported" not in result.output
    assert not (outside / "NewVault").exists()


def test_cli_obsidian_directory_swapped_to_symlink_before_create(tmp_path):
    """The vault path is absent at the exists() check and is a symlink at mkdir."""
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)
    vault = tmp_path / "vault"
    chat = tmp_path / "chat.json"
    write_chat(chat)

    with attack("swap-dir-on-mkdir", mkdir_path=vault, outside=outside) as state:
        result = CliRunner().invoke(
            cli,
            [
                "analyze",
                "--input",
                str(chat),
                "--platform",
                "chatgpt",
                "--obsidian",
                "--obsidian-path",
                str(vault),
            ],
        )

    assert state["done"], "mkdir was not observed; the swap could not run"
    assert_untouched(outside, before, "vault swapped to a symlink before mkdir")
    assert "✓ Exported" not in result.output


def test_obsidian_export_does_not_create_notes_through_empty_directory_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)
    link = vault / "AI Projects"
    link.symlink_to(outside, target_is_directory=True)

    exc = call_export(lambda: ObsidianExporter().export(sample_result(), vault, {}))

    assert_untouched(outside, before, "empty directory symlink")
    assert_symlink(link, outside)
    assert exc is not None, "export completed without refusing the symlink"


def test_obsidian_export_rejects_directory_symlink_and_preserves_target_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    before = seed_files(outside, [INDEX_NAME, PROJECT_NOTE])
    link = vault / "AI Projects"
    link.symlink_to(outside, target_is_directory=True)

    exc = call_export(lambda: ObsidianExporter().export(sample_result(), vault, {}))

    assert_untouched(outside, before, "exporter directory symlink")
    assert_symlink(link, outside)
    assert exc is not None, "export completed without refusing the symlink"


def test_obsidian_export_rejects_configured_and_nested_symlinks(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    child = outside / "Child"
    seed_files(child, [INDEX_NAME, PROJECT_NOTE])
    before = tree_snapshot(outside)
    link = vault / "linked"
    link.symlink_to(outside, target_is_directory=True)

    exc = call_export(
        lambda: ObsidianExporter().export(
            sample_result(),
            vault,
            {"folder": "linked/Child"},
        )
    )

    assert_untouched(outside, before, "exporter nested folder symlink")
    assert_symlink(link, outside)
    assert exc is not None, "export completed without refusing the symlink"


def test_obsidian_export_rejects_note_file_symlinks(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "AI Projects"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    before = seed_files(outside, [INDEX_NAME, PROJECT_NOTE])
    index_link = folder / INDEX_NAME
    project_link = folder / PROJECT_NOTE
    index_link.symlink_to(outside / INDEX_NAME)
    project_link.symlink_to(outside / PROJECT_NOTE)

    exc = call_export(lambda: ObsidianExporter().export(sample_result(), vault, {}))

    assert_untouched(outside, before, "note file symlinks")
    assert_symlink(index_link, outside / INDEX_NAME)
    assert_symlink(project_link, outside / PROJECT_NOTE)
    assert exc is not None, "export completed without refusing the symlink"


def test_obsidian_export_rejects_symlink_parent_of_file_output(tmp_path):
    outside = tmp_path / "outside"
    before = seed_files(outside, [INDEX_NAME, PROJECT_NOTE])
    link = tmp_path / "linked-parent"
    link.symlink_to(outside, target_is_directory=True)
    output_file = link / "notes.md"

    exc = call_export(lambda: ObsidianExporter().export(sample_result(), output_file, {}))

    assert_untouched(outside, before, "file output whose parent is a symlink")
    assert_symlink(link, outside)
    assert exc is not None, "export completed without refusing the symlink"


def test_obsidian_export_directory_swapped_to_symlink_before_write(tmp_path):
    vault = tmp_path / "vault"
    export_dir = vault / "AI Projects"
    export_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)

    with attack(
        "swap-dir-on-open",
        export_dir=export_dir,
        outside=outside,
        note_names=(INDEX_NAME, PROJECT_NOTE),
    ) as state:
        try:
            ObsidianExporter().export(sample_result(), vault, {})
        except OSError:
            pass

    assert state["done"], "note open was not observed; the swap could not run"
    assert_untouched(outside, before, "export directory swapped before write")


def test_obsidian_export_note_swapped_to_symlink_before_write(tmp_path):
    vault = tmp_path / "vault"
    export_dir = vault / "AI Projects"
    export_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    before = seed_files(outside, [INDEX_NAME, PROJECT_NOTE])

    with attack(
        "swap-file-on-open",
        export_dir=export_dir,
        outside=outside,
        note_names=(INDEX_NAME, PROJECT_NOTE),
    ) as state:
        try:
            ObsidianExporter().export(sample_result(), vault, {})
        except OSError:
            pass

    assert state["done"], "note open was not observed; the file swap could not run"
    assert_untouched(outside, before, "note path swapped to a symlink before write")


def test_cli_obsidian_directory_swapped_before_note_write(tmp_path):
    vault = tmp_path / "vault"
    export_dir = vault / "AI Projects"
    export_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)
    chat = tmp_path / "chat.json"
    project_note = write_chat(chat)

    with attack(
        "swap-dir-on-open",
        export_dir=export_dir,
        outside=outside,
        note_names=(INDEX_NAME, project_note),
    ) as state:
        CliRunner().invoke(
            cli,
            [
                "analyze",
                "--input",
                str(chat),
                "--platform",
                "chatgpt",
                "--obsidian",
                "--obsidian-path",
                str(vault),
            ],
        )

    assert state["done"], "CLI note open was not observed; the swap could not run"
    assert_untouched(outside, before, "CLI export directory swapped before write")


def test_cli_json_rejects_output_directory_symlink(tmp_path):
    outside = tmp_path / "outside"
    before = seed_files(outside, [JSON_NAME])
    output = tmp_path / "json-out"
    output.symlink_to(outside, target_is_directory=True)
    chat = tmp_path / "chat.json"
    write_chat(chat)

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--exporter",
            "json",
            "--output",
            str(output),
        ],
    )

    assert_untouched(outside, before, "JSON output directory symlink")
    assert_symlink(output, outside)
    assert "✓ Exported" not in result.output


def test_cli_json_rejects_output_file_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    target = outside / "stolen.json"
    target.write_text("SENTINEL-JSON", encoding="utf-8")
    before = tree_snapshot(outside)
    output = tmp_path / "result.json"
    output.symlink_to(target)
    chat = tmp_path / "chat.json"
    write_chat(chat)

    result = CliRunner().invoke(
        cli,
        [
            "analyze",
            "--input",
            str(chat),
            "--platform",
            "chatgpt",
            "--exporter",
            "json",
            "--output",
            str(output),
        ],
    )

    assert_untouched(outside, before, "JSON output file symlink")
    assert_symlink(output, target)
    assert "✓ Exported" not in result.output


def test_json_export_directory_swapped_to_symlink_before_write(tmp_path):
    output = tmp_path / "json-out"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    before = tree_snapshot(outside)

    with attack(
        "swap-dir-on-open",
        export_dir=output,
        outside=outside,
        note_names=(JSON_NAME,),
    ) as state:
        try:
            JSONExporter().export(sample_result(), output, {})
        except OSError:
            pass

    assert state["done"], "JSON open was not observed; the swap could not run"
    assert_untouched(outside, before, "JSON directory swapped before write")


def test_json_export_file_swapped_to_symlink_before_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    target = outside / JSON_NAME
    target.write_text("SENTINEL-JSON", encoding="utf-8")
    before = tree_snapshot(outside)
    output = tmp_path / "result.json"

    with attack(
        "swap-exact-on-open",
        target_path=output,
        outside_file=target,
    ) as state:
        try:
            JSONExporter().export(sample_result(), output, {})
        except OSError:
            pass

    assert state["done"], "JSON open was not observed; the file swap could not run"
    assert_untouched(outside, before, "JSON file swapped to a symlink before write")
    assert not output.exists() or output.is_symlink()
