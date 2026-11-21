"""Timestamp parsing in the platform parsers"""

import json
import time
from datetime import datetime

import pytest
from click.testing import CliRunner

from ai_history_analyser.cli import cli
from ai_history_analyser.parsers import get_parser

# dateutil turns a long digit token into an int via Decimal, which costs
# quadratic time in the token length on every supported CPython version.
LONG_DIGITS = "1" * 500_000 + " x"
DEADLINE_SECONDS = 1.0


def chatgpt_export(timestamp):
    return [{
        "id": "conv-1",
        "title": "Side project",
        "create_time": timestamp,
        "messages": [{
            "role": "user",
            "content": {"parts": ["I need to finish the website later"]},
            "create_time": timestamp,
        }],
    }]


def claude_export(timestamp):
    return [{
        "uuid": "conv-1",
        "name": "Side project",
        "created_at": timestamp,
        "chat_messages": [{
            "role": "user",
            "text": "I need to finish the website later",
            "created_at": timestamp,
        }],
    }]


def gemini_export(timestamp):
    return [{
        "conversation_id": "conv-1",
        "title": "Side project",
        "created_at": timestamp,
        "messages": [{
            "role": "user",
            "parts": [{"text": "I need to finish the website later"}],
            "created_at": timestamp,
        }],
    }]


EXPORTS = {
    "chatgpt": chatgpt_export,
    "claude": claude_export,
    "gemini": gemini_export,
}


def write_export(tmp_path, platform, timestamp, name="export.json"):
    path = tmp_path / name
    path.write_text(json.dumps(EXPORTS[platform](timestamp)), encoding="utf-8")
    return path


@pytest.mark.parametrize("platform", sorted(EXPORTS))
def test_oversized_timestamp_is_rejected_quickly(tmp_path, platform):
    path = write_export(tmp_path, platform, LONG_DIGITS)

    start = time.perf_counter()
    conversations = get_parser(platform).parse(path)
    elapsed = time.perf_counter() - start

    assert elapsed < DEADLINE_SECONDS
    assert len(conversations) == 1
    assert conversations[0].created_at is None
    assert conversations[0].messages[0].timestamp is None


@pytest.mark.parametrize("platform", sorted(EXPORTS))
def test_iso_timestamp_still_parsed(tmp_path, platform):
    path = write_export(tmp_path, platform, "2024-03-14T22:08:04.123456Z")

    conversation = get_parser(platform).parse(path)[0]

    assert conversation.created_at is not None
    assert conversation.created_at.replace(tzinfo=None) == datetime(2024, 3, 14, 22, 8, 4, 123456)
    assert conversation.messages[0].timestamp == conversation.created_at


@pytest.mark.parametrize("platform", sorted(EXPORTS))
def test_short_non_iso_timestamp_still_parsed(tmp_path, platform):
    path = write_export(tmp_path, platform, "March 14, 2024 10:30 PM")

    conversation = get_parser(platform).parse(path)[0]

    assert conversation.created_at == datetime(2024, 3, 14, 22, 30)
    assert conversation.messages[0].timestamp == datetime(2024, 3, 14, 22, 30)


def test_list_unfinished_moves_past_oversized_timestamp(tmp_path):
    hostile = write_export(tmp_path, "chatgpt", LONG_DIGITS, name="hostile.json")
    normal = write_export(tmp_path, "claude", "2024-03-14T22:08:04Z", name="normal.json")

    start = time.perf_counter()
    result = CliRunner().invoke(cli, [
        "list-unfinished",
        "-i", str(hostile), "-p", "chatgpt",
        "-i", str(normal), "-p", "claude",
        "--format", "json",
    ])
    elapsed = time.perf_counter() - start

    assert result.exit_code == 0, result.output
    assert elapsed < DEADLINE_SECONDS * 2
