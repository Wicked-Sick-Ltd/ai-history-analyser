"""Helpers for opening files that must be regular files"""

import errno
import os
import stat
from pathlib import Path
from typing import IO


class NotRegularFileError(OSError):
    """Raised when a path does not resolve to a regular file"""


def open_regular_file(path: Path, flags: int, follow_symlinks: bool = True) -> int:
    """Open ``path`` without blocking and return the fd only if it is a regular file.

    The type check is made on the opened descriptor, so a FIFO, device or
    directory swapped in at ``path`` is rejected instead of being read or
    written. Callers must not truncate before this check, so ``O_TRUNC`` is
    not accepted in ``flags``.
    """
    if flags & os.O_TRUNC:
        raise ValueError("O_TRUNC is applied only after the file type is checked")

    nonblock = getattr(os, 'O_NONBLOCK', 0)
    flags |= nonblock | getattr(os, 'O_NOCTTY', 0) | getattr(os, 'O_CLOEXEC', 0)
    if not follow_symlinks:
        flags |= getattr(os, 'O_NOFOLLOW', 0)

    try:
        fd = os.open(path, flags, 0o666)
    except OSError as e:
        if e.errno == errno.ELOOP and not follow_symlinks:
            raise NotRegularFileError(f"{path} is a symlink") from e
        if e.errno == errno.ENXIO:
            raise NotRegularFileError(f"{path} is not a regular file") from e
        raise

    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegularFileError(f"{path} is not a regular file")
        if nonblock:
            os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_regular_for_read(path: Path, encoding: str = None) -> IO[str]:
    """Open ``path`` for reading as text, accepting a regular file only"""
    return os.fdopen(open_regular_file(path, os.O_RDONLY), 'r', encoding=encoding)


def open_regular_for_write(path: Path, encoding: str = None) -> IO[str]:
    """Open ``path`` for writing as text, creating or truncating a regular file only"""
    fd = open_regular_file(path, os.O_WRONLY | os.O_CREAT)
    f = os.fdopen(fd, 'w', encoding=encoding)
    try:
        f.truncate()
    except BaseException:
        f.close()
        raise
    return f
