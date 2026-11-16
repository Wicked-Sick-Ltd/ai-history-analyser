"""Spec for bounded parsing of config.yaml.

These tests fail while analyze loads configuration with yaml.safe_load and no
depth or node limit. They pass once configuration parsing enforces the limits
below, rejects an implicit config.yaml that is not a regular file, and reports
failure without a traceback.

Contract:
- Maximum collection nesting depth is 16. The root collection counts as depth 1.
  Scalar keys do not add a level. config.yaml.example is depth 4 and must load.
- Maximum node count is 1024. Every scalar, sequence, and mapping counts as one
  node, including mapping keys. A document of 1024 nodes must load. 1025 must not.
- Either limit is a hard failure. The process prints exactly the line
  "Error: configuration rejected" on stderr, exits non-zero, does not print a
  traceback or any bytes from the file, and does not read chats or write a report.
- The same rules apply to the implicit ./config.yaml path and to --config.
- The implicit path is used only when it is a regular file (symlink, directory,
  and fifo are rejected without opening or following them). --config is the
  caller's explicit path and is not subject to that regular-file rule.
- A missing config file is empty configuration, not an error.

list-unfinished and init-config do not parse YAML configuration. They are not
covered here. A YAML sequence or scalar that is under both limits is also not
covered: analyze still assumes the loaded value is a mapping.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SENTINEL = "SENTINEL_CONFIG_BYTES_7f3a"
REJECTED = "Error: configuration rejected"
MAX_DEPTH = 16
MAX_NODES = 1024
# Flow and block nesting both overflow the default recursion ceiling (1000)
# inside PyYAML 6.0.3's composer at depth 493. 800 stays under the node budget
# so a node limit alone cannot stop it, and it is past that ceiling.
CRASH_DEPTH = 800

EXPORT = {
    "id": "c1",
    "title": "Ship the widget",
    "create_time": 1700000000,
    "mapping": {
        "m1": {
            "message": {
                "author": {"role": "user"},
                "content": {"parts": ["TODO finish the widget"]},
                "create_time": 1700000000,
            }
        }
    },
}


def _flow_sequence(depth: int) -> str:
    return "[" * depth + "0" + "]" * depth + "\n"


def _block_sequence(depth: int) -> str:
    lines = ["  " * i + "-" for i in range(depth)]
    lines.append("  " * depth + "0")
    return "\n".join(lines) + "\n"


def _block_mapping(depth: int, leaf: str = "1") -> str:
    lines = ["  " * i + "k:" for i in range(depth)]
    lines.append("  " * depth + leaf)
    return "\n".join(lines) + "\n"


def _flow_mapping(depth: int, leaf: str = "1") -> str:
    return "{k: " * depth + leaf + "}" * depth + "\n"


def _wide_mapping(scalar_count: int) -> str:
    """Root mapping plus a sequence of ``scalar_count`` scalars.

    Node total is scalar_count + 5 (root, padding key, sequence, analysis key,
    empty analysis mapping). 1019 scalars is 1024 nodes. 1020 scalars is 1025.
    """
    lines = [f"# {SENTINEL}", "padding:"]
    lines.extend(f"  - {index}" for index in range(scalar_count))
    lines.append("analysis: {}")
    return "\n".join(lines) + "\n"


def _marked(body: str) -> str:
    return f"# {SENTINEL}\n" + body


def _prepare(tmp_path: Path) -> Path:
    (tmp_path / "export.json").write_text(json.dumps(EXPORT), encoding="utf-8")
    return tmp_path / "report.json"


def _run(tmp_path: Path, output: Path, extra: list[str] | None = None, timeout: float = 20):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    command = [
        sys.executable,
        "-m",
        "ai_history_analyser",
        "analyze",
        "-i",
        "export.json",
        "-p",
        "chatgpt",
        "-o",
        str(output),
        *(extra or []),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return subprocess.CompletedProcess(command, -1, stdout, stderr)
    return completed


def _assert_rejected(completed: subprocess.CompletedProcess, output: Path) -> None:
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert REJECTED in completed.stderr
    assert "Parsing chat histories" not in completed.stdout
    assert "RecursionError" not in combined
    assert "maximum recursion depth" not in combined
    assert "Traceback" not in combined
    assert SENTINEL not in combined
    assert "[" * 40 not in combined
    assert not output.exists()


def _assert_analyzed(completed: subprocess.CompletedProcess, output: Path) -> None:
    assert completed.returncode == 0, completed.stderr
    assert "Analyzing 1 conversations..." in completed.stdout
    assert output.is_file()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["conversations_analyzed"] == 1
    assert SENTINEL not in completed.stdout + completed.stderr


def test_implicit_crash_depth_flow_sequence_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_marked(_flow_sequence(CRASH_DEPTH)), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_crash_depth_block_sequence_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_marked(_block_sequence(CRASH_DEPTH)), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_flow_sequence_just_over_depth_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_marked(_flow_sequence(MAX_DEPTH + 1)), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_block_sequence_just_over_depth_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_marked(_block_sequence(MAX_DEPTH + 1)), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_block_mapping_just_over_depth_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(
        _marked(_block_mapping(MAX_DEPTH + 1, leaf=SENTINEL)),
        encoding="utf-8",
    )
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_flow_mapping_just_over_depth_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(
        _marked(_flow_mapping(MAX_DEPTH + 1, leaf=SENTINEL)),
        encoding="utf-8",
    )
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_mapping_at_depth_limit_still_analyzes(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_block_mapping(MAX_DEPTH), encoding="utf-8")
    _assert_analyzed(_run(tmp_path, output), output)


def test_node_budget_one_over_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_wide_mapping(MAX_NODES - 4), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output), output)


def test_node_budget_at_limit_still_analyzes(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_wide_mapping(MAX_NODES - 5), encoding="utf-8")
    _assert_analyzed(_run(tmp_path, output), output)


def test_explicit_config_crash_depth_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    given = tmp_path / "given.yaml"
    given.write_text(_marked(_flow_sequence(CRASH_DEPTH)), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output, ["--config", str(given)]), output)


def test_explicit_config_just_over_depth_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    given = tmp_path / "given.yaml"
    given.write_text(_marked(_block_mapping(MAX_DEPTH + 1, leaf=SENTINEL)), encoding="utf-8")
    _assert_rejected(_run(tmp_path, output, ["-c", str(given)]), output)


def test_explicit_config_ignores_implicit_crash_file(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(_marked(_flow_sequence(CRASH_DEPTH)), encoding="utf-8")
    given = tmp_path / "given.yaml"
    given.write_text("analysis: {}\n", encoding="utf-8")
    _assert_analyzed(_run(tmp_path, output, ["--config", str(given)]), output)


def test_missing_config_still_analyzes(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    _assert_analyzed(_run(tmp_path, output), output)


def test_empty_config_still_analyzes(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    _assert_analyzed(_run(tmp_path, output), output)


def test_shipped_example_config_still_analyzes(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    example = (REPO / "config.yaml.example").read_text(encoding="utf-8")
    (tmp_path / "config.yaml").write_text(example, encoding="utf-8")
    _assert_analyzed(_run(tmp_path, output), output)


def test_invalid_yaml_does_not_echo_file_bytes(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").write_text(f"{SENTINEL}: :::\n", encoding="utf-8")
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_symlink_to_regular_file_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    target = tmp_path / "actual.yaml"
    target.write_text("analysis: {}\n", encoding="utf-8")
    (tmp_path / "config.yaml").symlink_to(target)
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_symlink_to_deep_document_is_not_followed(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    target = tmp_path / "actual.yaml"
    target.write_text(_marked(_flow_sequence(CRASH_DEPTH)), encoding="utf-8")
    (tmp_path / "config.yaml").symlink_to(target)
    _assert_rejected(_run(tmp_path, output), output)


def test_implicit_directory_is_rejected(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    (tmp_path / "config.yaml").mkdir()
    completed = _run(tmp_path, output)
    _assert_rejected(completed, output)
    assert "IsADirectoryError" not in completed.stdout + completed.stderr
    assert "Is a directory" not in completed.stdout + completed.stderr


def test_implicit_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    output = _prepare(tmp_path)
    os.mkfifo(tmp_path / "config.yaml")
    completed = _run(tmp_path, output, timeout=5)
    assert completed.returncode != -1
    _assert_rejected(completed, output)
