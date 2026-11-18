"""Temporary export names must fit NAME_MAX for long Unicode destinations."""

import os
from pathlib import Path

import pytest

from ai_history_analyser.safe_io import OutputDir, write_text_file

# Bytes in ".{prefix}.{16 hex}.tmp" aside from prefix. The old code kept a
# 100-character slice of the destination, so this affix was added on top.
_OLD_TEMP_AFFIX_BYTES = len(".") + len(".") + 16 + len(".tmp")


def test_long_unicode_destination_name(tmp_path: Path) -> None:
    """Write a legal multibyte name that the old character slice could not."""
    try:
        limit = os.pathconf(tmp_path, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        pytest.skip("filesystem does not report NAME_MAX")
    if not isinstance(limit, int) or limit <= _OLD_TEMP_AFFIX_BYTES:
        pytest.skip("filesystem does not report a usable NAME_MAX")

    unit = "测"
    unit_bytes = len(os.fsencode(unit))
    # Smallest count that still fits, while ".{name[:100]}.{token}.tmp" does not.
    count = (limit - _OLD_TEMP_AFFIX_BYTES) // unit_bytes + 1
    if count < 1 or count > 100 or count * unit_bytes > limit:
        pytest.skip("NAME_MAX leaves no room for this edge case")

    name = unit * count
    old_temp = f".{name[:100]}.{'a' * 16}.tmp"
    assert len(os.fsencode(name)) <= limit
    assert len(os.fsencode(old_temp)) > limit

    with OutputDir(tmp_path) as out:
        tmp, fd = out._create_temp(name)
        os.close(fd)
        try:
            assert len(os.fsencode(tmp)) <= limit
            assert all(ord(char) < 0xD800 or ord(char) > 0xDFFF for char in tmp)
        finally:
            os.unlink(out._entry(tmp), dir_fd=out._fd)

    target = write_text_file(tmp_path / name, "ok")
    assert target == tmp_path / name
    assert target.read_text(encoding="utf-8") == "ok"
    assert [entry.name for entry in tmp_path.iterdir()] == [name]
