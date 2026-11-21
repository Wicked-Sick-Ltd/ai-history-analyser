"""Base exporter interface"""

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import AnalysisResult


class BaseExporter(ABC):
    """Base class for exporters"""
    
    @abstractmethod
    def export(self, result: AnalysisResult, output_path: Path, config: dict = None) -> Path:
        """
        Export analysis results
        
        Args:
            result: AnalysisResult to export
            output_path: Path to export to
            config: Optional configuration dictionary
            
        Returns:
            Path to the exported file(s)
        """
        pass
    
    def validate_config(self, config: dict) -> dict:
        """
        Check exporter settings and return a normalized copy

        Raises:
            ValueError: If the settings are not a mapping or a value has the wrong type
        """
        if not isinstance(config, dict):
            raise ValueError(f"exporter settings must be a mapping, not {type(config).__name__}")
        return dict(config)

    @abstractmethod
    def get_name(self) -> str:
        """Get the name of this exporter"""
        pass

