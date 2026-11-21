"""Base analyzer interface"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any

from ..models import Conversation, AnalysisResult


class BaseAnalyzer(ABC):
    """Base class for analysis engines"""
    
    @abstractmethod
    def analyze(self, conversations: List[Conversation], config: Dict[str, Any] = None) -> AnalysisResult:
        """
        Analyze conversations and return results
        
        Args:
            conversations: List of conversations to analyze
            config: Optional configuration dictionary
            
        Returns:
            AnalysisResult with findings
        """
        pass
    
    def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check analyzer settings and return a normalized copy

        Raises:
            ValueError: If the settings are not a mapping or a value has the wrong type
        """
        if not isinstance(config, dict):
            raise ValueError(f"analyzer settings must be a mapping, not {type(config).__name__}")
        return dict(config)

    @abstractmethod
    def get_name(self) -> str:
        """Get the name of this analyzer"""
        pass

