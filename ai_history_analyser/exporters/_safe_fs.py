"""Symlink-safe filesystem primitives for exporters.

Chat-note exports contain conversation titles, identifiers, and excerpts that
may include private prompts. Writing them must never follow a symlink out of
the directory the user asked for: an attacker who can add a single directory
entry inside the vault could otherwise aim it at a directory they can read and
receive the notes.

The helpers below never trust a path across a lookup. A directory is opened
component by component with ``O_NOFOLLOW`` so a symlink at any component is
refused, and the resulting file descriptor is the authorized object: every
note is written relative to that descriptor, so a directory swapped in after
the check cannot receive it. Note files themselves are opened with
``O_NOFOLLOW`` as well, so a name swapped to a symlink just before the write
is refused rather than followed.
"""

import os
import stat
from pathlib import Path

__all__ = [
    "UnsafeExportPathError",
    "open_dir_secure",
    "makedirs_secure",
    "open_write_relative",
    "open_write_file_secure",
]

_DIR_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW


class UnsafeExportPathError(OSError):
    """Raised when an export path is, or passes through, a symlink."""


def _absolute_parts(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path(os.path.abspath(path))
    return path.parts


def open_dir_secure(path):
    """Open an existing directory, refusing any symlink in its path.

    Returns a file descriptor bound to the directory's inode. The caller owns
    it and must ``os.close`` it. The descriptor identifies the directory even
    if its name is later pointed elsewhere, so writes relative to it stay put.
    """
    parts = _absolute_parts(path)
    fd = os.open(parts[0], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[1:]:
            try:
                child = os.open(part, _DIR_OPEN_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno == getattr(os, "ELOOP", None) or _is_symlink_at(fd, part):
                    raise UnsafeExportPathError(
                        f"refusing to use symlinked path component {part!r} in {path}"
                    ) from exc
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def makedirs_secure(path):
    """Create ``path`` and any missing parents, refusing symlink components.

    Each missing level is created with ``mkdir`` relative to the bound parent
    descriptor and then reopened with ``O_NOFOLLOW``. A parent replaced by a
    symlink cannot receive the new directory, and a component swapped to a
    symlink between creation and reopen is refused instead of followed. Returns
    a descriptor bound to the final directory's inode; the caller must
    ``os.close`` it.
    """
    parts = _absolute_parts(path)
    fd = os.open(parts[0], os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[1:]:
            try:
                child = os.open(part, _DIR_OPEN_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    # Expected race: another writer created this component
                    # between the open and mkdir. The O_NOFOLLOW reopen below
                    # refuses that winner when it is a symlink.
                    pass
                try:
                    child = os.open(part, _DIR_OPEN_FLAGS, dir_fd=fd)
                except OSError as exc:
                    raise UnsafeExportPathError(
                        f"refusing to use symlinked path component {part!r} in {path}"
                    ) from exc
            except OSError as exc:
                if exc.errno == getattr(os, "ELOOP", None) or _is_symlink_at(fd, part):
                    raise UnsafeExportPathError(
                        f"refusing to use symlinked path component {part!r} in {path}"
                    ) from exc
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _is_symlink_at(dir_fd, name):
    try:
        mode = os.lstat(name, dir_fd=dir_fd).st_mode
    except OSError:
        return False
    return stat.S_ISLNK(mode)


def open_write_relative(dir_fd, name, encoding="utf-8"):
    """Open ``name`` for writing inside the directory held by ``dir_fd``.

    The file is opened with ``O_NOFOLLOW``, so if ``name`` has been pointed at a
    symlink it is refused rather than followed.
    """
    fd = os.open(name, _FILE_WRITE_FLAGS, 0o644, dir_fd=dir_fd)
    return os.fdopen(fd, "w", encoding=encoding)


def open_write_file_secure(path, encoding="utf-8"):
    """Open a file for writing after verifying its parent has no symlink.

    The parent is bound with ``open_dir_secure`` and the final component is
    opened relative to that descriptor with ``O_NOFOLLOW``. A parent swapped to
    a symlink after the check cannot redirect the write, and a name swapped to
    a symlink just before the write is refused rather than followed.
    """
    path = Path(path)
    name = path.name
    parent_fd = open_dir_secure(path.parent)
    try:
        fd = os.open(name, _FILE_WRITE_FLAGS, 0o644, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == getattr(os, "ELOOP", None) or _is_symlink_at(parent_fd, name):
            raise UnsafeExportPathError(f"refusing to write through symlinked path {path}") from exc
        raise
    finally:
        os.close(parent_fd)
    return os.fdopen(fd, "w", encoding=encoding)
