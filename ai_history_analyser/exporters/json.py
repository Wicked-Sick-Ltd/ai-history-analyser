"""JSON exporter"""

import json
import os
from pathlib import Path
from typing import Dict, Any

from .base import BaseExporter
from . import _safe_fs
from ..fileio import write_text_in_dir
from ..models import AnalysisResult


class JSONExporter(BaseExporter):
    """Export analysis results to JSON format"""
    
    def get_name(self) -> str:
        return "json"
    
    def export(self, result: AnalysisResult, output_path: Path, config: dict = None) -> Path:
        """Export to JSON format"""
        config = config or {}
        output_path = Path(output_path)
        if output_path.is_dir():
            directory = output_path
            name = "analysis_results.json"
        else:
            directory = output_path.parent
            name = output_path.name

        data = {
            'platform': result.platform.value,
            'conversations_analyzed': result.conversations_analyzed,
            'analysis_date': result.analysis_date.isoformat(),
            'unfinished_projects': [
                {
                    'id': p.id,
                    'title': p.title,
                    'description': p.description,
                    'source_conversation_id': p.source_conversation_id,
                    'source_platform': p.source_platform.value,
                    'detected_at': p.detected_at.isoformat() if p.detected_at else None,
                    'confidence': p.confidence,
                    'priority_score': p.priority_score,
                    'keywords_found': p.keywords_found,
                    'tags': p.tags,
                    'context': p.context,
                    'metadata': p.metadata
                }
                for p in result.unfinished_projects
            ],
            'metadata': result.metadata
        }

        # The directory is bound without following a symlink component. The
        # final name is then written as a new regular file, or refused when it
        # is a symlink, FIFO, device, or directory.
        dir_fd = _safe_fs.open_dir_secure(directory)
        try:
            return write_text_in_dir(
                dir_fd, directory, name, json.dumps(data, indent=2, ensure_ascii=False)
            )
        finally:
            os.close(dir_fd)
