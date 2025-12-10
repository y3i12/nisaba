"""YAML configuration utilities with comment preservation support."""

import os
from typing import Literal, overload
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _create_YAML(preserve_comments: bool = False) -> YAML:
    """
    Create YAML parser/dumper instance.

    Args:
        preserve_comments: If True, preserves comments and quotes in YAML

    Returns:
        Configured YAML instance
    """
    typ = None if preserve_comments else "safe"
    result = YAML(typ=typ)
    result.preserve_quotes = preserve_comments
    return result


@overload
def load_yaml(
    path: str | Path,
    preserve_comments: Literal[False] = False,
    encoding: str = "utf-8"
) -> dict: ...


@overload
def load_yaml(
    path: str | Path,
    preserve_comments: Literal[True],
    encoding: str = "utf-8"
) -> CommentedMap: ...


def load_yaml(
    path: str | Path,
    preserve_comments: bool = False,
    encoding: str = "utf-8"
) -> dict | CommentedMap:
    """
    Load YAML file with optional comment preservation.

    Args:
        path: Path to YAML file
        preserve_comments: If True, returns CommentedMap preserving structure
        encoding: File encoding (default: utf-8)

    Returns:
        Parsed YAML data (dict or CommentedMap)
    """
    with open(path, encoding=encoding) as f:
        yaml = _create_YAML(preserve_comments)
        return yaml.load(f)


def save_yaml(
    path: str | Path,
    data: dict | CommentedMap,
    preserve_comments: bool = False,
    encoding: str = "utf-8"
) -> None:
    """
    Save data to YAML file with optional comment preservation.

    Auto-creates parent directories if they don't exist.

    Args:
        path: Path to YAML file
        data: Data to save
        preserve_comments: If True, preserves comments and quotes
        encoding: File encoding (default: utf-8)
    """
    yaml = _create_YAML(preserve_comments)

    # Auto-create parent directories
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding=encoding) as f:
        yaml.dump(data, f)
