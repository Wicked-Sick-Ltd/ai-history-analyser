"""CLI tests for exports that contain lone surrogate escapes"""

import json

import pytest
from click.testing import CliRunner

from ai_history_analyser import cli as cli_module
from ai_history_analyser.cli import cli
from ai_history_analyser.models import Conversation, Message, Platform

from helpers import LONE, PLATFORMS, write_export

SECOND = ("conv-2", "Second project", "TODO. FIXME. WIP.")

# Title falls back to the conversation title because no matched sentence is long enough.
TITLE_IN_CONVERSATION = [("conv-1", f"Project {LONE} alpha", "TODO. FIXME. WIP."), SECOND]
# Title is taken from the sentence around the keyword.
TITLE_IN_SENTENCE = [
    ("conv-1", "Plain", f"We still need to build the {LONE} export pipeline. TODO. FIXME."),
    SECOND,
]
SURROGATE_IN_ID = [(f"conv-{LONE}", "Project alpha", "TODO. FIXME. WIP."), SECOND]

COMMANDS = {
    "analyze": ["analyze"],
    "table": ["list-unfinished"],
    "list": ["list-unfinished", "--format", "list"],
}


def _run(args):
    return CliRunner().invoke(cli, args, catch_exceptions=True)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("command", sorted(COMMANDS))
@pytest.mark.parametrize(
    "conversations",
    [TITLE_IN_CONVERSATION, TITLE_IN_SENTENCE, SURROGATE_IN_ID],
    ids=["title-from-conversation", "title-from-sentence", "surrogate-in-id"],
)
def test_lone_surrogate_does_not_abort_output(tmp_path, platform, command, conversations):
    path = write_export(tmp_path, platform, conversations)

    result = _run(COMMANDS[command] + ["-i", str(path), "-p", platform])

    assert result.exception is None, repr(result.exception)
    assert result.exit_code == 0
    assert "Second project" in result.output
    if conversations is not SURROGATE_IN_ID:
        assert "\ufffd" in result.output


@pytest.mark.parametrize("platform", PLATFORMS)
def test_json_export_with_lone_surrogate(tmp_path, platform):
    path = write_export(tmp_path, platform, TITLE_IN_CONVERSATION)
    out = tmp_path / "out.json"

    result = _run(["analyze", "-i", str(path), "-p", platform, "-o", str(out)])

    assert result.exception is None, repr(result.exception)
    assert "Error during export" not in result.output
    titles = [p["title"] for p in json.loads(out.read_text(encoding="utf-8"))["unfinished_projects"]]
    assert "Project \ufffd alpha - TODO" in titles


class _StubParser:
    def parse(self, file_path):
        return [
            Conversation(
                id="conv-1",
                title="Built \ud800 in code",
                platform=Platform.CHATGPT,
                messages=[Message(role="user", content="TODO. FIXME. WIP. \udfff")],
            )
        ]


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_unencodable_project_text_is_replaced_when_printed(tmp_path, monkeypatch, command):
    monkeypatch.setattr(cli_module, "get_parser", lambda platform: _StubParser())
    path = tmp_path / "unused.json"
    path.write_text("[]", encoding="utf-8")

    result = _run(COMMANDS[command] + ["-i", str(path), "-p", "chatgpt"])

    assert result.exception is None, repr(result.exception)
    assert "Built ? in code" in result.output
