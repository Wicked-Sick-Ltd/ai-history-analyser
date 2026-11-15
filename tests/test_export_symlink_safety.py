"""Exporter and config writes must not follow a final symlink.

The output directory is chosen by the operator. A name inside that directory, or
the output path itself, may be a symlink planted by someone who can create
files there but cannot write the symlink target. Opening that path in write
mode must not create or truncate the target.

A check of the path before open is not enough. While these tests run,
lstat and stat(follow_symlinks=False) on the planted symlink report a regular
file, and unlinking that path puts the symlink back before the call returns.
The write still has to refuse or replace the directory entry without following
it. Path.is_symlink and os.path.islink both end in those stat calls
(pathlib.Path.lstat -> os.stat(follow_symlinks=False); genericpath.islink ->
os.lstat).
"""

import importlib
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from ai_history_analyser.analyzers import get_analyzer
from ai_history_analyser.exporters.obsidian import ObsidianExporter
from ai_history_analyser.parsers import get_parser

ORIGINAL = b"VICTIM-FILE-CONTENTS\n"
JSON_NAME = "analysis_results.json"
INDEX_NAME = "Unfinished Projects Index.md"
OBSIDIAN_FOLDER = "AI Projects"


def _chat(path: Path, content: str) -> None:
    payload = [
        {
            "id": "conv-1",
            "title": "Notes",
            "messages": [
                {"author": {"role": "user"}, "content": {"parts": [content]}}
            ],
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _key(path, dir_fd=None) -> str:
    text = os.fspath(path)
    if dir_fd is not None and not os.path.isabs(text):
        base = os.readlink(f"/proc/self/fd/{dir_fd}")
        text = os.path.join(base, text)
    return os.path.normpath(os.path.abspath(text))


@contextmanager
def _conceal_symlink(link: Path, target: Path):
    """Hide the planted symlink from lstat, and restore it if it is unlinked.

    Product modules are reloaded so a module-level ``from os import lstat``
    (or unlink) binds to these wrappers.
    """
    protected = {_key(link): os.fspath(target)}
    decoy = link.parent / ".decoy-regular-for-stat"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(b"decoy")
    regular = os.lstat(decoy)

    real_stat = os.stat
    real_lstat = os.lstat
    real_unlink = os.unlink
    real_remove = os.remove

    def _hidden(path, dir_fd=None) -> bool:
        try:
            return _key(path, dir_fd) in protected
        except OSError:
            return False

    def wrapped_stat(path, *, dir_fd=None, follow_symlinks=True):
        if not follow_symlinks and _hidden(path, dir_fd):
            return regular
        kwargs = {"follow_symlinks": follow_symlinks}
        if dir_fd is not None:
            kwargs["dir_fd"] = dir_fd
        return real_stat(path, **kwargs)

    def wrapped_lstat(path, *, dir_fd=None):
        if _hidden(path, dir_fd):
            return regular
        if dir_fd is None:
            return real_lstat(path)
        return real_lstat(path, dir_fd=dir_fd)

    def _restore_after(path, dir_fd, real):
        key = None
        try:
            if _hidden(path, dir_fd):
                key = _key(path, dir_fd)
        except OSError:
            key = None
        if dir_fd is None:
            real(path)
        else:
            real(path, dir_fd=dir_fd)
        if key is not None:
            os.symlink(protected[key], key)

    def wrapped_unlink(path, *, dir_fd=None):
        _restore_after(path, dir_fd, real_unlink)

    def wrapped_remove(path, *, dir_fd=None):
        _restore_after(path, dir_fd, real_remove)

    os.stat = wrapped_stat
    os.lstat = wrapped_lstat
    os.unlink = wrapped_unlink
    os.remove = wrapped_remove
    import posix

    posix.stat = wrapped_stat
    posix.lstat = wrapped_lstat
    posix.unlink = wrapped_unlink
    posix.remove = wrapped_remove

    import ai_history_analyser.cli as cli_mod
    import ai_history_analyser.exporters as exporters_pkg
    import ai_history_analyser.exporters.json as json_mod
    import ai_history_analyser.exporters.obsidian as obsidian_mod

    importlib.reload(json_mod)
    importlib.reload(obsidian_mod)
    importlib.reload(exporters_pkg)
    importlib.reload(cli_mod)
    try:
        # Wrapped lstat hides the link. readlink still sees the directory entry
        # the product is about to open.
        assert not stat.S_ISLNK(os.lstat(link).st_mode)
        assert os.path.normpath(os.readlink(link)) == str(target)
        yield cli_mod.cli
    finally:
        os.stat = real_stat
        os.lstat = real_lstat
        os.unlink = real_unlink
        os.remove = real_remove
        posix.stat = real_stat
        posix.lstat = real_lstat
        posix.unlink = real_unlink
        posix.remove = real_remove
        importlib.reload(json_mod)
        importlib.reload(obsidian_mod)
        importlib.reload(exporters_pkg)
        importlib.reload(cli_mod)


def _plant(link: Path, target: Path, *, dangling: bool) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        link.unlink()
    if not dangling:
        target.write_bytes(ORIGINAL)
    elif target.exists():
        target.unlink()
    link.symlink_to(target)


def _signaled_failure(result) -> bool:
    if result.exit_code != 0 or result.exception is not None:
        return True
    return "Error during export" in (result.output or "")


def _assert_not_followed(link: Path, target: Path, *, dangling: bool, result, marker: str) -> None:
    if dangling:
        assert not target.exists(), (
            f"write through {link} created {target}: "
            f"{target.read_bytes()!r}" if target.exists() else ""
        )
    else:
        assert target.read_bytes() == ORIGINAL, (
            f"write through {link} replaced {target} with {target.read_bytes()!r}"
        )

    if link.is_symlink():
        assert os.path.normpath(os.readlink(link)) == str(target)
        assert _signaled_failure(result), (
            "symlink was left in place but the command reported success:\n"
            f"exit={result.exit_code} output={result.output!r}"
        )
        return

    assert link.is_file(), f"output path {link} was removed"
    assert marker in link.read_text(encoding="utf-8")


def _analyze(cli, chat: Path, output: Path, exporter: str | None = None):
    args = ["analyze", "-i", str(chat), "-p", "chatgpt", "-o", str(output)]
    if exporter is not None:
        args.extend(["-e", exporter])
    return CliRunner().invoke(cli, args)


def test_analyze_json_directory_symlink_does_not_replace_outside_file(tmp_path: Path):
    chat = tmp_path / "chat.json"
    _chat(chat, "hello from the export test")
    output = tmp_path / "shared-out"
    output.mkdir()
    target = tmp_path / "outside" / "secret.txt"
    link = output / JSON_NAME
    _plant(link, target, dangling=False)

    with _conceal_symlink(link, target) as cli:
        result = _analyze(cli, chat, output)

    _assert_not_followed(
        link, target, dangling=False, result=result, marker='"conversations_analyzed"'
    )


def test_analyze_json_directory_dangling_symlink_does_not_create_outside_file(tmp_path: Path):
    chat = tmp_path / "chat.json"
    _chat(chat, "hello from the export test")
    output = tmp_path / "shared-out"
    output.mkdir()
    target = tmp_path / "outside" / "secret.txt"
    link = output / JSON_NAME
    _plant(link, target, dangling=True)

    with _conceal_symlink(link, target) as cli:
        result = _analyze(cli, chat, output)

    _assert_not_followed(
        link, target, dangling=True, result=result, marker='"conversations_analyzed"'
    )


def test_analyze_json_file_symlink_does_not_replace_outside_file(tmp_path: Path):
    chat = tmp_path / "chat.json"
    _chat(chat, "hello from the export test")
    output_parent = tmp_path / "shared-out"
    target = tmp_path / "outside" / "secret.txt"
    link = output_parent / "results.json"
    _plant(link, target, dangling=False)

    with _conceal_symlink(link, target) as cli:
        result = _analyze(cli, chat, link)

    _assert_not_followed(
        link, target, dangling=False, result=result, marker='"conversations_analyzed"'
    )


def test_analyze_json_dangling_file_symlink_does_not_create_outside_file(tmp_path: Path):
    chat = tmp_path / "chat.json"
    _chat(chat, "hello from the export test")
    target = tmp_path / "outside" / "secret.txt"
    link = tmp_path / "shared-out" / "results.json"
    _plant(link, target, dangling=True)

    with _conceal_symlink(link, target) as cli:
        result = _analyze(cli, chat, link)

    _assert_not_followed(
        link, target, dangling=True, result=result, marker='"conversations_analyzed"'
    )


def _project_export_name(chat: Path) -> str:
    conversations = get_parser("chatgpt").parse(chat)
    result = get_analyzer("unfinished-projects").analyze(conversations)
    assert result.unfinished_projects, "chat fixture produced no unfinished project"
    slug = ObsidianExporter()._get_project_filename(result.unfinished_projects[0])
    return f"{slug}.md"


def test_analyze_obsidian_index_symlink_does_not_replace_outside_file(tmp_path: Path):
    chat = tmp_path / "chat.json"
    _chat(chat, "hello from the export test")
    output = tmp_path / "vault"
    folder = output / OBSIDIAN_FOLDER
    folder.mkdir(parents=True)
    target = tmp_path / "outside" / "secret.txt"
    link = folder / INDEX_NAME
    _plant(link, target, dangling=False)

    with _conceal_symlink(link, target) as cli:
        result = _analyze(cli, chat, output, exporter="obsidian")

    _assert_not_followed(
        link, target, dangling=False, result=result, marker="Unfinished Projects Index"
    )


def test_analyze_obsidian_project_symlink_does_not_replace_outside_file(tmp_path: Path):
    chat = tmp_path / "chat.json"
    _chat(
        chat,
        "I need to finish the export safety work soon. I need to cover symlink writes too.",
    )
    output = tmp_path / "vault"
    folder = output / OBSIDIAN_FOLDER
    folder.mkdir(parents=True)
    target = tmp_path / "outside" / "secret.txt"
    link = folder / _project_export_name(chat)
    _plant(link, target, dangling=False)

    with _conceal_symlink(link, target) as cli:
        result = _analyze(cli, chat, output, exporter="obsidian")

    _assert_not_followed(link, target, dangling=False, result=result, marker="need to")


def test_init_config_dangling_symlink_does_not_create_outside_file(tmp_path: Path):
    target = tmp_path / "outside" / "secret.txt"
    link = tmp_path / "shared-out" / "config.yaml"
    _plant(link, target, dangling=True)

    with _conceal_symlink(link, target) as cli:
        result = CliRunner().invoke(cli, ["init-config", "-o", str(link)])

    _assert_not_followed(
        link, target, dangling=True, result=result, marker="unfinished_projects"
    )


def test_init_config_confirmed_symlink_does_not_replace_outside_file(tmp_path: Path):
    target = tmp_path / "outside" / "secret.txt"
    link = tmp_path / "shared-out" / "config.yaml"
    _plant(link, target, dangling=False)

    with _conceal_symlink(link, target) as cli:
        result = CliRunner().invoke(cli, ["init-config", "-o", str(link)], input="y\n")

    _assert_not_followed(
        link, target, dangling=False, result=result, marker="unfinished_projects"
    )

