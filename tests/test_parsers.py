"""Parser tests"""

import re

import pytest

from ai_history_analyser.parsers import get_parser

from helpers import LONE, PLATFORMS, write_export

SURROGATE = re.compile("[\ud800-\udfff]")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_lone_surrogates_are_replaced(tmp_path, platform):
    path = write_export(
        tmp_path,
        platform,
        [(f"conv-{LONE}", f"Project {LONE} alpha", f"We still need to build {LONE} it")],
    )

    [conv] = get_parser(platform).parse(path)

    assert conv.id == "conv-\ufffd"
    assert conv.title == "Project \ufffd alpha"
    assert conv.messages[0].content == "We still need to build \ufffd it"
    leftovers = [
        s
        for s in _strings([conv.metadata] + [m.metadata for m in conv.messages])
        if SURROGATE.search(s)
    ]
    assert leftovers == []


@pytest.mark.parametrize("platform", PLATFORMS)
def test_lone_surrogate_in_key_is_replaced(tmp_path, platform):
    path = tmp_path / "export.json"
    path.write_text(
        '[{"id": "c", "title": "t", "\\udc00": "x", "messages": []}]', encoding="utf-8"
    )

    [conv] = get_parser(platform).parse(path)

    assert "\ufffd" in conv.metadata
    assert not any(SURROGATE.search(s) for s in _strings(conv.metadata))


@pytest.mark.parametrize("platform", PLATFORMS)
def test_surrogate_pairs_still_decode(tmp_path, platform):
    path = write_export(tmp_path, platform, [("c", "Rocket \\ud83d\\ude80 launch", "TODO")])
    path.write_text(path.read_text().replace("\\\\u", "\\u"), encoding="utf-8")

    [conv] = get_parser(platform).parse(path)

    assert conv.title == "Rocket \U0001F680 launch"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_deeply_nested_export_still_parses(tmp_path, platform):
    depth = 900
    nested = "[" * depth + '"\\ud800"' + "]" * depth
    path = tmp_path / "export.json"
    path.write_text(
        '[{"id": "c", "title": "t", "extra": ' + nested + ', "messages": []}]',
        encoding="utf-8",
    )

    [conv] = get_parser(platform).parse(path)

    node = conv.metadata["extra"]
    for _ in range(depth - 1):
        node = node[0]
    assert node == ["\ufffd"]
