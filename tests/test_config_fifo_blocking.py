"""Spec tests for implicit and explicit config loads on non-regular files.

These tests fail while ``load_config`` trusts ``Path.exists`` and then opens
that path with a blocking read. A FIFO counts as existing, and that open waits
until another process opens the write end. Re-checking the path with stat or
lstat between the check and the open is not enough: the lied-stat cases still
report a regular file for every path lookup and still require the load to
finish without applying FIFO contents.

They pass once a load accepts only a regular file it has already opened, and
``analyze`` still parses the chat export.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TIMEOUT_SECONDS = 2
CHAT_EXPORT = json.dumps(
    [
        {
            "id": "conv-1",
            "title": "Notes",
            "messages": [
                {
                    "author": {"role": "user"},
                    "content": {"parts": ["Remember to finish the export"]},
                    "create_time": 1700000000,
                }
            ],
        }
    ]
)
REGULAR_CONFIG = "analysis:\n  unfinished_projects:\n    min_confidence: 0.25\n"
FIFO_PAYLOAD = b"marker: from-fifo\n"
THIS_FILE = str(Path(__file__).resolve())


def _chat(directory: Path) -> Path:
    path = directory / "chat.json"
    path.write_text(CHAT_EXPORT)
    return path


def _mkfifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def _snapshot(directory: Path):
    found = {}
    for entry in directory.iterdir():
        st = os.lstat(entry)
        found[entry.name] = (st.st_dev, st.st_ino, st.st_mode)
    return found


def _path_key(path) -> str:
    text = os.fspath(path)
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _config_stat_keys(config_path: Path):
    """Spellings of ``config_path`` that a load might stat.

    The parent is resolved and the final name is not. ``Path.cwd()`` can be
    the physical directory while the test path stays logical (``/var`` versus
    ``/private/var``). Following the final component would also treat a
    symlink named ``config.yaml`` as its target.
    """
    logical = _path_key(config_path)
    parent = os.path.dirname(os.fspath(config_path)) or os.curdir
    name = os.path.basename(os.fspath(config_path))
    if isinstance(name, bytes):
        name = os.fsdecode(name)
    physical = os.path.normcase(
        os.path.normpath(os.path.join(os.path.realpath(parent), name))
    )
    return {logical, physical}


def _install_regular_file_stat_lie(config_path: Path):
    """Report a regular file for every stat/lstat of ``config_path``.

    The directory entry is left unchanged. A later open of that path therefore
    sees the real inode, which is what a check-then-open race does.

    ``os.stat`` is patched, and so is ``Path._accessor`` when this Python
    captured ``os.stat`` there at import (3.8 through 3.10). ``Path.exists``,
    ``Path.is_file``, and ``Path.is_symlink`` then see the lie too.

    Returns a restore callable. The child that loads config can ignore it.
    """
    import pathlib
    import posix

    real_stat = os.stat
    real_lstat = os.lstat
    real_posix_stat = posix.stat
    real_posix_lstat = posix.lstat
    decoy_dir = Path(tempfile.mkdtemp(prefix="config-stat-decoy-"))
    decoy = decoy_dir / "regular"
    decoy.write_text("placeholder: true\n")
    regular_stat = real_lstat(decoy)
    keys = _config_stat_keys(config_path)

    def same(path):
        try:
            return _path_key(path) in keys
        except (TypeError, ValueError, OSError):
            return False

    def lying_stat(path, *args, **kwargs):
        if same(path):
            return regular_stat
        return real_stat(path, *args, **kwargs)

    def lying_lstat(path, *args, **kwargs):
        if same(path):
            return regular_stat
        return real_lstat(path, *args, **kwargs)

    os.stat = lying_stat
    os.lstat = lying_lstat
    posix.stat = lying_stat
    posix.lstat = lying_lstat

    accessor = getattr(pathlib.Path, "_accessor", None)
    saved_accessor = {}
    if accessor is not None:
        for name, replacement in (("stat", lying_stat), ("lstat", lying_lstat)):
            if hasattr(accessor, name):
                saved_accessor[name] = getattr(accessor, name)
                setattr(accessor, name, replacement)

    def restore():
        os.stat = real_stat
        os.lstat = real_lstat
        posix.stat = real_posix_stat
        posix.lstat = real_posix_lstat
        for name, original in saved_accessor.items():
            setattr(accessor, name, original)

    return restore


def _run_mode(cwd: Path, mode: str) -> dict:
    os.chdir(cwd)
    explicit = "explicit" in mode
    config_path = cwd / ("supplied.yaml" if explicit else "config.yaml")
    if mode.startswith("lie-"):
        _install_regular_file_stat_lie(config_path)

    from click.testing import CliRunner

    from ai_history_analyser.cli import cli, load_config

    passed = config_path if explicit else None
    try:
        loaded = load_config(passed)
    except Exception as exc:
        return {"error": f"load_config: {type(exc).__name__}: {exc}"}

    args = ["analyze", "-i", str(cwd / "chat.json"), "-p", "chatgpt"]
    if explicit:
        args.extend(["--config", str(config_path)])
    result = CliRunner().invoke(cli, args)
    output = result.output or ""
    stderr = getattr(result, "stderr", None) or ""
    if stderr:
        output = f"{output}{stderr}"
    payload = {
        "config": loaded,
        "output": output,
        "exit_code": result.exit_code,
    }
    if result.exception is not None:
        payload["error"] = f"analyze: {type(result.exception).__name__}: {result.exception}"
    elif result.exit_code != 0:
        payload["error"] = f"analyze exit {result.exit_code}"
    return payload


def _start_child(cwd: Path, mode: str):
    return subprocess.Popen(
        [sys.executable, THIS_FILE, str(cwd), mode],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_child(proc, directory: Path, before, held_writer=None):
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=TIMEOUT_SECONDS)
        after = _snapshot(directory)
        held = "" if held_writer is None else " with the write end already open"
        pytest.fail(f"config load blocked{held}; directory before={before} after={after}")
    # The caller closes held_writer. This helper used to close it, which
    # closed the descriptor twice once the test's own finally ran.

    after = _snapshot(directory)
    assert after == before, f"config load changed the directory: {before} -> {after}"
    assert proc.returncode == 0, stderr
    data = json.loads(stdout)
    assert "error" not in data, data
    return data


def _assert_analysis_ran(data):
    assert data["config"] == {}
    assert "from-fifo" not in data["output"]
    assert "Parsing chat histories..." in data["output"]
    assert "Conversations Analyzed: 1" in data["output"]


def _run_cli(directory: Path, args):
    return subprocess.Popen(
        [sys.executable, "-m", "ai_history_analyser", *args],
        cwd=directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_missing_config_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from ai_history_analyser.cli import load_config

    assert load_config(None) == {}


def test_regular_config_file_is_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(REGULAR_CONFIG)
    from ai_history_analyser.cli import load_config

    assert load_config(None) == {"analysis": {"unfinished_projects": {"min_confidence": 0.25}}}


def test_regular_config_still_reaches_analysis(tmp_path):
    _chat(tmp_path)
    (tmp_path / "config.yaml").write_text(REGULAR_CONFIG)
    before = _snapshot(tmp_path)
    proc = _start_child(tmp_path, "regular")
    data = _finish_child(proc, tmp_path, before)
    assert data["config"]["analysis"]["unfinished_projects"]["min_confidence"] == 0.25
    assert "Conversations Analyzed: 1" in data["output"]


def test_implicit_fifo_config_does_not_block_analyze(tmp_path):
    _chat(tmp_path)
    _mkfifo(tmp_path / "config.yaml")
    output = tmp_path / "result.json"
    before = _snapshot(tmp_path)
    proc = _run_cli(
        tmp_path,
        ["analyze", "-i", "chat.json", "-p", "chatgpt", "-o", str(output)],
    )
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=TIMEOUT_SECONDS)
        assert not output.exists()
        assert _snapshot(tmp_path) == before
        pytest.fail("analyze blocked on implicit config.yaml FIFO before export")

    assert _snapshot(tmp_path) == {**before, "result.json": _snapshot(tmp_path)["result.json"]}
    assert stat.S_ISFIFO(os.lstat(tmp_path / "config.yaml").st_mode)
    assert proc.returncode == 0, stderr
    assert "Parsing chat histories..." in stdout
    assert "Exported to" in stdout
    exported = json.loads(output.read_text())
    assert exported["conversations_analyzed"] == 1
    assert "from-fifo" not in output.read_text()


def test_symlink_to_fifo_does_not_block_obsidian_export(tmp_path):
    _chat(tmp_path)
    fifo = _mkfifo(tmp_path / "hidden.pipe")
    config = tmp_path / "config.yaml"
    config.symlink_to(fifo.name)
    vault = tmp_path / "vault"
    before = _snapshot(tmp_path)
    proc = _run_cli(
        tmp_path,
        [
            "analyze",
            "-i",
            "chat.json",
            "-p",
            "chatgpt",
            "--obsidian",
            "--obsidian-path",
            str(vault),
        ],
    )
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=TIMEOUT_SECONDS)
        assert not vault.exists()
        assert _snapshot(tmp_path) == before
        pytest.fail("analyze blocked on a symlink to a FIFO before Obsidian export")

    assert os.path.islink(config)
    assert os.readlink(config) == fifo.name
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)
    assert proc.returncode == 0, stderr
    assert "Parsing chat histories..." in stdout
    assert "Exported to" in stdout
    indexes = list(vault.rglob("Unfinished Projects Index.md"))
    assert len(indexes) == 1
    assert "**Conversations Analyzed:** 1" in indexes[0].read_text()


def test_explicit_fifo_config_does_not_block_analyze(tmp_path):
    _chat(tmp_path)
    fifo = _mkfifo(tmp_path / "supplied.yaml")
    before = _snapshot(tmp_path)
    proc = _run_cli(
        tmp_path,
        ["analyze", "-i", "chat.json", "-p", "chatgpt", "--config", str(fifo)],
    )
    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=TIMEOUT_SECONDS)
        assert _snapshot(tmp_path) == before
        pytest.fail("analyze blocked on an explicit FIFO --config path")

    assert _snapshot(tmp_path) == before
    assert proc.returncode == 0, stderr
    assert "Parsing chat histories..." in stdout
    assert "Conversations Analyzed: 1" in stdout


def test_fifo_stat_lie_does_not_block_or_apply_config(tmp_path):
    _chat(tmp_path)
    fifo = _mkfifo(tmp_path / "config.yaml")
    before = _snapshot(tmp_path)
    proc = _start_child(tmp_path, "lie-implicit")
    data = _finish_child(proc, tmp_path, before)
    _assert_analysis_ran(data)
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)


def test_fifo_with_writer_and_stat_lie_is_not_loaded(tmp_path):
    _chat(tmp_path)
    fifo = _mkfifo(tmp_path / "config.yaml")
    holder = os.open(fifo, os.O_RDWR)
    try:
        os.write(holder, FIFO_PAYLOAD)
        before = _snapshot(tmp_path)
        proc = _start_child(tmp_path, "lie-implicit")
        # The write end stays open until the child has finished. Closing it
        # here, including when the child blocks and the assertion fails, is
        # what releases the descriptor.
        data = _finish_child(proc, tmp_path, before, held_writer=holder)
        _assert_analysis_ran(data)
        assert stat.S_ISFIFO(os.lstat(fifo).st_mode)
    finally:
        os.close(holder)


def test_symlink_stat_lie_does_not_block_analyze(tmp_path):
    _chat(tmp_path)
    fifo = _mkfifo(tmp_path / "hidden.pipe")
    config = tmp_path / "config.yaml"
    config.symlink_to(fifo.name)
    before = _snapshot(tmp_path)
    proc = _start_child(tmp_path, "lie-implicit")
    data = _finish_child(proc, tmp_path, before)
    _assert_analysis_ran(data)
    assert os.path.islink(config)
    assert os.readlink(config) == fifo.name
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)


def test_stat_lie_fools_pathlib_for_both_directory_spellings(tmp_path):
    """The lie has to cover pathlib and a resolved parent, not only os.stat."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    fifo = link / "config.yaml"
    _mkfifo(fifo)
    other = real / "other.txt"
    other.write_text("keep\n")
    physical = Path(os.path.realpath(real)) / "config.yaml"
    restore = _install_regular_file_stat_lie(fifo)
    try:
        for path in (fifo, physical):
            assert Path(path).exists()
            assert Path(path).is_file()
            assert not Path(path).is_symlink()
        assert Path(other).is_file()
        assert Path(other).read_text() == "keep\n"
        assert not Path(real / "missing").exists()
    finally:
        restore()
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)


def test_explicit_fifo_stat_lie_does_not_block_analyze(tmp_path):
    _chat(tmp_path)
    fifo = _mkfifo(tmp_path / "supplied.yaml")
    before = _snapshot(tmp_path)
    proc = _start_child(tmp_path, "lie-explicit")
    data = _finish_child(proc, tmp_path, before)
    _assert_analysis_ran(data)
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)


if __name__ == "__main__":
    result = _run_mode(Path(sys.argv[1]), sys.argv[2])
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()
    raise SystemExit(0 if "error" not in result else 1)
