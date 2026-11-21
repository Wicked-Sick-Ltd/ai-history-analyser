"""Builders for synthetic chat exports used by the tests"""

import json

LONE = "<LONE>"
LONE_ESCAPE = "\\ud800"

PLATFORMS = ["chatgpt", "claude", "gemini"]


def _conversation(platform, conv_id, title, text):
    if platform == "chatgpt":
        return {
            "id": conv_id,
            "title": title,
            "create_time": "2026-11-01T10:00:00",
            "messages": [{"role": "user", "content": {"parts": [text]}}],
        }
    if platform == "claude":
        return {
            "uuid": conv_id,
            "title": title,
            "created_at": "2026-11-01T10:00:00",
            "chat_messages": [{"role": "user", "text": text}],
        }
    if platform == "gemini":
        return {
            "conversation_id": conv_id,
            "title": title,
            "created_at": "2026-11-01T10:00:00",
            "messages": [{"role": "user", "parts": [{"text": text}]}],
        }
    raise ValueError(platform)


def export_text(platform, conversations):
    """Return export JSON text, turning each LONE marker into a lone surrogate escape.

    ``conversations`` is a list of ``(id, title, message_text)`` tuples.
    """
    data = [_conversation(platform, *conv) for conv in conversations]
    return json.dumps(data).replace(LONE, LONE_ESCAPE)


def write_export(tmp_path, platform, conversations):
    path = tmp_path / f"{platform}.json"
    path.write_text(export_text(platform, conversations), encoding="utf-8")
    return path
