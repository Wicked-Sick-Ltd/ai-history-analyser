"""Writing output files without following links planted at the destination name"""

import os
import secrets
import stat
from pathlib import Path
from typing import Optional, Tuple

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
# rename(2) replaces the destination name itself, so binding the directory by fd keeps
# every step inside the directory that was opened, even if its path is swapped later.
_USE_DIR_FD = {os.open, os.stat, os.rename, os.unlink} <= os.supports_dir_fd


class UnsafeDestinationError(OSError):
    """The output name is held by something other than a regular file"""


def write_text_file(path: Path, text: str, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` as a new regular file.

    The content goes to a freshly created file next to ``path`` that is then renamed
    over it. Symlinks, named pipes, devices and directories at ``path`` are refused and
    never opened; an existing regular file is replaced rather than written through.
    """
    path = Path(path)
    data = text.encode(encoding)
    if _USE_DIR_FD:
        dir_fd = os.open(str(path.parent), os.O_RDONLY | _DIRECTORY)
        try:
            _write(path, data, dir_fd, path.name)
        finally:
            os.close(dir_fd)
    else:
        _write(path, data, None, str(path))
    return path


def _write(path: Path, data: bytes, dir_fd: Optional[int], target: str) -> None:
    expected, mode = _regular_file_identity(path, dir_fd, target)
    tmp = os.path.join(os.path.dirname(target), f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o666, dir_fd=dir_fd
    )
    try:
        with os.fdopen(fd, "wb") as f:
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _regular_file_identity(path, dir_fd, target)[0] != expected:
            raise UnsafeDestinationError(f"{path} changed while it was being written")
        if dir_fd is not None:
            os.rename(tmp, target, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        else:
            os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass  # Temp file may not exist during cleanup; this is safe to ignore.
        raise


def _regular_file_identity(
    path: Path, dir_fd: Optional[int], target: str
) -> Tuple[Optional[Tuple[int, int]], Optional[int]]:
    try:
        st = os.stat(target, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeDestinationError(f"Refusing to write {path}: it is not a regular file")
    return (st.st_dev, st.st_ino), st.st_mode & 0o777
