"""Regression tests for symlink confinement in export filesystem helpers."""

import errno
import os

import pytest

from ai_history_analyser.exporters import _safe_fs


def test_open_write_file_secure_writes_into_real_parent(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "note.txt"

    with _safe_fs.open_write_file_secure(target) as handle:
        handle.write("private prompt")

    assert target.read_text(encoding="utf-8") == "private prompt"
    assert not target.is_symlink()


def test_open_write_file_secure_refuses_symlinked_file(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    leak = tmp_path / "leak.txt"
    leak.write_text("original", encoding="utf-8")
    link = parent / "note.txt"
    link.symlink_to(leak)

    with pytest.raises(_safe_fs.UnsafeExportPathError):
        _safe_fs.open_write_file_secure(link)

    assert leak.read_text(encoding="utf-8") == "original"


def test_open_write_file_secure_refuses_symlinked_parent(tmp_path):
    leak = tmp_path / "leak"
    leak.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(leak)

    with pytest.raises(_safe_fs.UnsafeExportPathError):
        _safe_fs.open_write_file_secure(parent / "note.txt")

    assert list(leak.iterdir()) == []


def test_open_write_file_secure_ignores_parent_swapped_after_bind(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    leak = tmp_path / "leak"
    leak.mkdir()
    kept = tmp_path / "real_kept"
    target = real / "note.txt"
    real_open_dir = _safe_fs.open_dir_secure

    def open_then_swap(path):
        fd = real_open_dir(path)
        os.rename(real, kept)
        os.symlink(leak, real)
        return fd

    monkeypatch.setattr(_safe_fs, "open_dir_secure", open_then_swap)

    with _safe_fs.open_write_file_secure(target) as handle:
        handle.write("private prompt")

    assert not (leak / "note.txt").exists()
    assert (kept / "note.txt").read_text(encoding="utf-8") == "private prompt"


def test_open_write_file_secure_classifies_symlink_without_eloop(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    leak = tmp_path / "leak.txt"
    leak.write_text("untouched", encoding="utf-8")
    link = parent / "note.txt"
    link.symlink_to(leak)
    real_open = os.open

    def refuse_creat(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and flags & os.O_CREAT:
            raise OSError(errno.EACCES, "mocked non-eloop failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", refuse_creat)

    with pytest.raises(_safe_fs.UnsafeExportPathError):
        _safe_fs.open_write_file_secure(link)

    assert leak.read_text(encoding="utf-8") == "untouched"


def test_open_write_file_secure_propagates_non_symlink_errors(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "note.txt"
    real_open = os.open

    def refuse_creat(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and flags & os.O_CREAT:
            raise PermissionError(errno.EACCES, "denied")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", refuse_creat)

    with pytest.raises(PermissionError):
        _safe_fs.open_write_file_secure(target)

    assert not target.exists()


def test_makedirs_secure_creates_missing_directories(tmp_path):
    target = tmp_path / "vault" / "AI Projects"
    fd = _safe_fs.makedirs_secure(target)
    try:
        with _safe_fs.open_write_relative(fd, "index.md") as handle:
            handle.write("ok")
    finally:
        os.close(fd)

    assert (target / "index.md").read_text(encoding="utf-8") == "ok"


def test_makedirs_secure_refuses_symlinked_component(tmp_path):
    leak = tmp_path / "leak"
    leak.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "folder").symlink_to(leak)

    fd = None
    try:
        with pytest.raises(_safe_fs.UnsafeExportPathError):
            fd = _safe_fs.makedirs_secure(parent / "folder" / "nested")
    finally:
        if fd is not None:
            os.close(fd)

    assert list(leak.iterdir()) == []


def test_makedirs_secure_creates_relative_to_bound_parent(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    leak = tmp_path / "leak"
    leak.mkdir()
    kept = tmp_path / "base_kept"
    swapped = {"done": False}
    real_mkdir = os.mkdir

    def swapping_mkdir(path, mode=0o777, *, dir_fd=None):
        if not swapped["done"]:
            swapped["done"] = True
            os.rename(base, kept)
            os.symlink(leak, base)
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", swapping_mkdir)

    fd = _safe_fs.makedirs_secure(base / "created")
    os.close(fd)

    assert swapped["done"]
    assert not (leak / "created").exists()
    assert (kept / "created").is_dir()
    assert not (kept / "created").is_symlink()


def test_makedirs_secure_tolerates_existing_directory_race(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    real_open = os.open
    real_mkdir = os.mkdir
    missed = {"done": False}

    def miss_child_once(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and path == "child" and not missed["done"]:
            missed["done"] = True
            raise FileNotFoundError(errno.ENOENT, "miss")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def mkdir_then_exists(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        raise FileExistsError(errno.EEXIST, "exists")

    monkeypatch.setattr(os, "open", miss_child_once)
    monkeypatch.setattr(os, "mkdir", mkdir_then_exists)

    fd = _safe_fs.makedirs_secure(child)
    os.close(fd)

    assert missed["done"]
    assert child.is_dir()
    assert not child.is_symlink()


def test_makedirs_secure_refuses_symlink_that_wins_create_race(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    leak = tmp_path / "leak"
    leak.mkdir()
    child = parent / "child"
    real_open = os.open
    missed = {"done": False}

    def miss_child_once(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and path == "child" and not missed["done"]:
            missed["done"] = True
            raise FileNotFoundError(errno.ENOENT, "miss")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def plant_symlink(path, mode=0o777, *, dir_fd=None):
        os.symlink(leak, child)
        raise FileExistsError(errno.EEXIST, "symlink won")

    monkeypatch.setattr(os, "open", miss_child_once)
    monkeypatch.setattr(os, "mkdir", plant_symlink)

    fd = None
    try:
        with pytest.raises(_safe_fs.UnsafeExportPathError):
            fd = _safe_fs.makedirs_secure(child)
    finally:
        if fd is not None:
            os.close(fd)

    assert child.is_symlink()
    assert list(leak.iterdir()) == []
