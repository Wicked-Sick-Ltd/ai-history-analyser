"""Base parser interface"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, List, Optional
from pathlib import Path

from ..models import Conversation, Platform

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

