"""Command-line interface for AI History Analyser"""

import click
import yaml
from pathlib import Path
from typing import List, Tuple
from tqdm import tqdm

from .parsers import get_parser
from .analyzers import get_analyzer
from .exporters import get_exporter
from .models import Conversation, Platform


class ConfigError(click.ClickException):
    """Raised when the configuration file cannot be used"""


def load_config(config_path: Path = None) -> dict:
    """Load configuration from file"""
    explicit = config_path is not None
    if not explicit:
        config_path = Path.cwd() / "config.yaml"
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"Configuration file not found: {config_path}")
        return {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise ConfigError(f"Invalid configuration file {config_path}: {e}")

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid configuration file {config_path}: expected a mapping of settings, "
            f"not {type(data).__name__}"
        )
    return data


def _config_section(mapping: dict, key: str, name: str) -> dict:
    """Return mapping[key] as a mapping, treating a missing or null value as empty"""
    value = mapping.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Invalid configuration: '{name}' must be a mapping, not {type(value).__name__}")
    return value


def resolve_settings(config_data: dict, analyzer_name: str, analyzer_instance,
                     exporter_name: str, exporter_instance=None) -> Tuple[dict, dict]:
    """Validate loaded configuration and return (analyzer_config, exporter_config)"""
    analysis = _config_section(config_data, 'analysis', 'analysis')
    exporters = _config_section(config_data, 'exporters', 'exporters')
    obsidian = _config_section(config_data, 'obsidian', 'obsidian')

    analyzer_config = _config_section(analysis, analyzer_name, f"analysis.{analyzer_name}")
    try:
        analyzer_config = analyzer_instance.validate_config(analyzer_config)
    except ValueError as e:
        raise ConfigError(f"Invalid configuration for 'analysis.{analyzer_name}': {e}")

    exporter_config = {}
    if exporter_instance is not None:
        exporter_config = dict(_config_section(exporters, exporter_name, f"exporters.{exporter_name}"))
        if exporter_name == 'obsidian':
            exporter_config.update(obsidian)
        try:
            exporter_config = exporter_instance.validate_config(exporter_config)
        except ValueError as e:
            raise ConfigError(f"Invalid configuration for exporter '{exporter_name}': {e}")
    
    return analyzer_config, exporter_config


def _fail(message: str, verbose: bool = False):
    """Report an error and exit with a non-zero status"""
    click.echo(message, err=True)
    if verbose:
        import traceback
        traceback.print_exc()
    click.get_current_context().exit(1)


@click.group()
@click.version_option()
def cli():
    """AI History Analyser - Analyze chat histories from AI assistants"""
    pass


@cli.command()
@click.option('--input', '-i', 'input_files', multiple=True, required=True,
              type=click.Path(exists=True, path_type=Path),
              help='Input file(s) to analyze')
@click.option('--platform', '-p', 'platforms', multiple=True, required=True,
              help='Platform for each input file (chatgpt, claude, gemini)')
@click.option('--analyzer', '-a', default='unfinished-projects',
              help='Analyzer to use (default: unfinished-projects)')
@click.option('--output', '-o', type=click.Path(path_type=Path),
              help='Output path (file or directory)')
@click.option('--exporter', '-e', default='json',
              help='Exporter to use (json, obsidian)')
@click.option('--obsidian', is_flag=True,
              help='Export to Obsidian (shortcut for --exporter obsidian)')
@click.option('--obsidian-path', type=click.Path(path_type=Path),
              help='Path to Obsidian vault')
@click.option('--config', '-c', type=click.Path(exists=True, path_type=Path),
              help='Configuration file path')
@click.option('--verbose', '-v', is_flag=True,
              help='Verbose output')
def analyze(input_files, platforms, analyzer, output, exporter, obsidian, obsidian_path, config, verbose):
    """Analyze chat history files"""
    
    # Load configuration
    config_data = load_config(config)
    
    # Handle Obsidian shortcut
    if obsidian:
        exporter = 'obsidian'
        if obsidian_path:
            output = obsidian_path
    
    # Validate inputs
    if len(input_files) != len(platforms):
        _fail("Error: Number of input files must match number of platforms")

    try:
        analyzer_instance = get_analyzer(analyzer)
        exporter_instance = get_exporter(exporter) if output else None
    except ValueError as e:
        _fail(f"Error: {e}")

    analyzer_config, exporter_config = resolve_settings(
        config_data, analyzer, analyzer_instance, exporter, exporter_instance
    )
    
    # Parse all conversations
    all_conversations = []
    
    click.echo("Parsing chat histories...")
    for input_file, platform in tqdm(list(zip(input_files, platforms)), disable=not verbose):
        try:
            parser = get_parser(platform)
            conversations = parser.parse(input_file)
            all_conversations.extend(conversations)
            if verbose:
                click.echo(f"  Parsed {len(conversations)} conversations from {input_file}")
        except Exception as e:
            click.echo(f"Error parsing {input_file}: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            continue
    
    if not all_conversations:
        _fail("No conversations found to analyze")
    
    click.echo(f"\nAnalyzing {len(all_conversations)} conversations...")
    
    # Run analysis
    try:
        result = analyzer_instance.analyze(all_conversations, analyzer_config)
    except Exception as e:
        _fail(f"Error during analysis: {e}", verbose)
    click.echo(f"Found {len(result.unfinished_projects)} unfinished projects")
    
    # Export results
    if output:
        click.echo(f"\nExporting results to {output}...")
        
        try:
            if exporter == 'obsidian':
                if not output.exists() or not output.is_dir():
                    click.echo(f"Warning: Obsidian path should be a directory. Creating: {output}")
                    output.mkdir(parents=True, exist_ok=True)
            output_path = exporter_instance.export(result, output, exporter_config)
        except Exception as e:
            _fail(f"Error during export: {e}", verbose)
        click.echo(f"✓ Exported to {output_path}")
    else:
        # Print summary
        click.echo("\n=== Analysis Summary ===")
        click.echo(f"Platform: {result.platform.value}")
        click.echo(f"Conversations Analyzed: {result.conversations_analyzed}")
        click.echo(f"Unfinished Projects Found: {len(result.unfinished_projects)}")
        
        if result.unfinished_projects:
            click.echo("\nTop 5 Projects by Priority:")
            for i, project in enumerate(result.unfinished_projects[:5], 1):
                click.echo(f"\n{i}. {project.title}")
                click.echo(f"   Priority: {project.priority_score:.2f} | Confidence: {project.confidence:.2f}")
                click.echo(f"   {project.description[:100]}...")


@cli.command()
@click.option('--input', '-i', 'input_files', multiple=True, required=True,
              type=click.Path(exists=True, path_type=Path),
              help='Input file(s) to analyze')
@click.option('--platform', '-p', 'platforms', multiple=True, required=True,
              help='Platform for each input file')
@click.option('--min-priority', type=float, default=0.0,
              help='Minimum priority score to show')
@click.option('--format', '-f', type=click.Choice(['table', 'list', 'json']), default='table',
              help='Output format')
def list_unfinished(input_files, platforms, min_priority, format):
    """List unfinished projects from chat histories"""
    
    # Parse conversations
    all_conversations = []
    for input_file, platform in zip(input_files, platforms):
        try:
            parser = get_parser(platform)
            conversations = parser.parse(input_file)
            all_conversations.extend(conversations)
        except Exception as e:
            click.echo(f"Error parsing {input_file}: {e}", err=True)
            continue
    
    if not all_conversations:
        click.echo("No conversations found", err=True)
        return
    
    # Analyze
    analyzer = get_analyzer('unfinished-projects')
    result = analyzer.analyze(all_conversations)
    
    # Filter by priority
    projects = [p for p in result.unfinished_projects if p.priority_score >= min_priority]
    
    if not projects:
        click.echo("No unfinished projects found")
        return
    
    # Output
    if format == 'json':
        import json
        data = [
            {
                'title': p.title,
                'priority': p.priority_score,
                'confidence': p.confidence,
                'platform': p.source_platform.value,
                'tags': p.tags
            }
            for p in projects
        ]
        click.echo(json.dumps(data, indent=2))
    elif format == 'list':
        for i, project in enumerate(projects, 1):
            click.echo(f"{i}. {project.title} (Priority: {project.priority_score:.2f})")
    else:  # table
        click.echo(f"\n{'#':<4} {'Title':<50} {'Priority':<10} {'Confidence':<10} {'Platform':<10}")
        click.echo("-" * 90)
        for i, project in enumerate(projects, 1):
            title = project.title[:47] + "..." if len(project.title) > 50 else project.title
            click.echo(f"{i:<4} {title:<50} {project.priority_score:<10.2f} {project.confidence:<10.2f} {project.source_platform.value:<10}")


@cli.command()
@click.option('--output', '-o', type=click.Path(path_type=Path), default='config.yaml',
              help='Output path for config file')
def init_config(output):
    """Create a default configuration file"""
    
    if output.exists():
        if not click.confirm(f"{output} already exists. Overwrite?"):
            return
    
    config = {
        'analysis': {
            'unfinished_projects': {
                'keywords': [
                    r'\\bTODO\\b',
                    r'\\bneed to\\b',
                    r'\\bshould implement\\b',
                    r'\\bnext steps\\b'
                ],
                'min_confidence': 0.5
            }
        },
        'obsidian': {
            'vault_path': '~/Documents/ObsidianVault',
            'folder': 'AI Projects'
        },
        'exporters': {
            'obsidian': {
                'folder': 'AI Projects'
            }
        }
    }
    
    with open(output, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    click.echo(f"Created configuration file: {output}")


if __name__ == '__main__':
    cli()

