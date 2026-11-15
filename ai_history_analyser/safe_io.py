"""Write output files without following a symlink at the destination.

Each file is written to a new temporary file created with O_EXCL inside the
destination directory and then renamed over the final name. Whatever entry
already sits at that name (symlink, hard link, FIFO) is replaced, never opened,
so its target is neither created nor truncated.

On POSIX the destination directory is opened once and every later operation is
relative to that descriptor, so swapping a path component afterwards cannot
redirect the write.
"""

import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, TextIO, Union

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)

_USE_DIR_FD = os.open in os.supports_dir_fd and bool(_O_DIRECTORY)

_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC


class UnsafeOutputPath(OSError):
    """Raised when an output location is not a plain directory entry."""


def _check_name(name: str, *, allow_parent: bool = False) -> None:
    if (
        not name
        or name == "."
        or (name == ".." and not allow_parent)
        or os.sep in name
        or (os.altsep and os.altsep in name)
    ):
        raise ValueError(f"invalid output name: {name!r}")


class OutputDir:
    """A destination directory bound once; files are written relative to it.

    The path passed in is resolved like any user-supplied path, including
    symlinks in it. Names added through :meth:`subdir` and :meth:`open_text`
    are never followed if they are symlinks.
    """

    def __init__(self, path: Union[str, Path], *, _fd: Optional[int] = None):
        self.path = Path(path)
        if _fd is not None:
            self._fd: Optional[int] = _fd
        elif _USE_DIR_FD:
            self._fd = os.open(self.path, _DIR_FLAGS)
        else:
            if not self.path.exists():
                raise FileNotFoundError(f"No such directory: {self.path}")
            if not self.path.is_dir():
                raise NotADirectoryError(f"Not a directory: {self.path}")
            self._fd = None

    def __enter__(self) -> "OutputDir":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _entry(self, name: str) -> str:
        return name if _USE_DIR_FD else str(self.path / name)

    def subdir(self, name: str, *, create: bool = True) -> "OutputDir":
        """Open (and optionally create) a child directory that is not a symlink."""
        _check_name(name, allow_parent=True)
        if create and name != "..":
            try:
                os.mkdir(self._entry(name), 0o777, dir_fd=self._fd)
            except FileExistsError:
                pass
        if not _USE_DIR_FD:
            return OutputDir(self.path / name)
        try:
            fd = os.open(name, _DIR_FLAGS | _O_NOFOLLOW, dir_fd=self._fd)
        except FileNotFoundError:
            raise
        except OSError as e:
            raise UnsafeOutputPath(
                e.errno,
                "refusing to use an output folder that is a symlink or not a directory",
                str(self.path / name),
            ) from e
        return OutputDir(self.path / name, _fd=fd)

    def _restrict_mode_to_existing(self, fd: int, name: str) -> None:
        # Only ever removes permission bits, so a racing swap of the entry
        # cannot widen access to the new file.
        try:
            existing = os.stat(self._entry(name), dir_fd=self._fd, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return
        if not stat.S_ISREG(existing.st_mode):
            return
        current = stat.S_IMODE(os.fstat(fd).st_mode)
        restricted = current & stat.S_IMODE(existing.st_mode)
        if restricted != current:
            os.chmod(fd, restricted)

    def _create_temp(self, name: str):
        for _ in range(100):
            tmp = f".{name[:100]}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(self._entry(tmp), _TEMP_FLAGS, 0o666, dir_fd=self._fd)
            except FileExistsError:
                continue
            return tmp, fd
        raise FileExistsError(f"could not create a temporary file in {self.path}")

    @contextmanager
    def open_text(self, name: str, encoding: str = "utf-8") -> Iterator[TextIO]:
        """Yield a text file that atomically replaces ``name`` on success."""
        _check_name(name)
        tmp, fd = self._create_temp(name)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                if os.chmod in os.supports_fd:
                    self._restrict_mode_to_existing(f.fileno(), name)
                yield f
                f.flush()
                os.fsync(f.fileno())
            os.replace(
                self._entry(tmp), self._entry(name),
                src_dir_fd=self._fd, dst_dir_fd=self._fd,
            )
        except BaseException:
            try:
                os.unlink(self._entry(tmp), dir_fd=self._fd)
            except OSError:
                pass
            raise

    def write_text(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        with self.open_text(name, encoding=encoding) as f:
            f.write(text)
        return self.path / name


def open_output_target(output_path: Path, default_name: str):
    """Bind the directory and file name an exporter should write to.

    A directory gets ``default_name`` inside it; anything else (including a
    missing path or a symlink to a non-directory) names the file itself.
    """
    output_path = Path(output_path)
    try:
        return OutputDir(output_path), default_name
    except (NotADirectoryError, FileNotFoundError):
        pass
    _check_name(output_path.name)
    return OutputDir(output_path.parent), output_path.name


def write_text_file(path: Path, text: str, encoding: str = "utf-8") -> Path:
    """Replace the file at ``path`` without following a symlink there."""
    path = Path(path)
    _check_name(path.name)
    with OutputDir(path.parent) as out:
        out.write_text(path.name, text, encoding=encoding)
    return path
