"""Base parser interface"""

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, List, Optional
from pathlib import Path

from ..models import Conversation, Platform

_LONE_SURROGATE = re.compile('[\ud800-\udfff]')


def _replace_surrogates(text: str) -> str:
    return _LONE_SURROGATE.sub('\ufffd', text)


def replace_lone_surrogates(data: Any) -> Any:
    """Replace lone UTF-16 surrogates in every string and key of decoded JSON with U+FFFD.

    json.load turns escapes such as "\\ud800" into str characters that cannot be
    encoded as UTF-8, so any later print or file write of them would raise.
    Walks iteratively so deeply nested exports cannot exhaust the recursion limit.
    """
    if isinstance(data, str):
        return _replace_surrogates(data)

    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            items = list(node.items())
            node.clear()
            for key, value in items:
                if isinstance(key, str):
                    key = _replace_surrogates(key)
                if isinstance(value, str):
                    value = _replace_surrogates(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
                node[key] = value
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str):
                    node[index] = _replace_surrogates(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
    return data


# dateutil's cost grows quadratically with the length of a numeric token, so
# export fields longer than any real timestamp are never handed to it.
MAX_TIMESTAMP_LENGTH = 64


class BaseParser(ABC):
    """Base class for platform-specific parsers"""
    
    @property
    @abstractmethod
    def platform(self) -> Platform:
        """The platform this parser handles"""
        pass
    
    @abstractmethod
    def parse(self, file_path: Path) -> List[Conversation]:
        """
        Parse a chat history export file
        
        Args:
            file_path: Path to the export file
            
        Returns:
            List of Conversation objects
        """
        pass
    
    def _load_json(self, file_path: Path) -> Any:
        """Load a JSON export with lone surrogates replaced"""
        with open(file_path, 'r', encoding='utf-8') as f:
            return replace_lone_surrogates(json.load(f))

    def validate(self, file_path: Path) -> bool:
        """
        Validate that a file can be parsed by this parser
        
        Args:
            file_path: Path to the file to validate
            
        Returns:
            True if the file appears to be in the correct format
        """
        try:
            return file_path.exists() and file_path.is_file()
        except Exception:
            return False

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        """Parse timestamp from various formats"""
        if not value:
            return None

        if isinstance(value, (int, float)):
            # Unix timestamp
            try:
                return datetime.fromtimestamp(value)
            except (ValueError, OSError):
                return None

        if isinstance(value, str):
            if len(value) > MAX_TIMESTAMP_LENGTH:
                return None
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                try:
                    from dateutil.parser import parse as date_parse
                    return date_parse(value)
                except Exception:
                    return None

        return None

