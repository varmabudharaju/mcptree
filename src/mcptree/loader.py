"""YAML file loading. The only module that imports yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Tree, TreeParseError, tree_from_dict
from .validate import validate_tree


class TreeLoadError(Exception):
    """A tree file failed to parse or validate."""


def load_tree_file(path: Path) -> Tree:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise TreeLoadError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TreeLoadError(f"{path}: top level must be a mapping")
    try:
        tree = tree_from_dict(data)
    except TreeParseError as exc:
        raise TreeLoadError(f"{path}: {exc}") from exc
    issues = validate_tree(tree)
    if issues:
        lines = "; ".join(f"[{i.node or 'tree'}] {i.message}" for i in issues)
        raise TreeLoadError(f"{path}: invalid tree: {lines}")
    return tree


def load_trees_dir(path: Path) -> dict[str, Tree]:
    files = sorted(list(path.glob("*.yaml")) + list(path.glob("*.yml")))
    trees: dict[str, Tree] = {}
    for f in files:
        tree = load_tree_file(f)
        if tree.id in trees:
            raise TreeLoadError(f"{f}: duplicate tree id '{tree.id}'")
        trees[tree.id] = tree
    if not trees:
        raise TreeLoadError(f"{path}: no trees found (*.yaml, *.yml)")
    return trees
