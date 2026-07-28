"""YAML file loading. The only module that imports yaml."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .models import Tree, TreeParseError, tree_from_dict
from .validate import validate_tree


class TreeLoadError(Exception):
    """A tree file failed to parse or validate."""


def _reject_non_string_keys(obj: object, path: str) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TreeLoadError(
                    f"non-string mapping key {key!r} at {path or 'top level'} — "
                    "quote YAML boolean-like keys ('on', 'yes', ...) and other non-string scalars"
                )
            _reject_non_string_keys(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _reject_non_string_keys(item, f"{path}[{i}]")


def load_tree_file(path: Path) -> Tree:
    try:
        text = path.read_text()
    except OSError as exc:
        raise TreeLoadError(f"{path}: cannot read file: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise TreeLoadError(f"{path}: not valid UTF-8: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise TreeLoadError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TreeLoadError(f"{path}: top level must be a mapping")
    # Normalize YAML-1.1 boolean 'on' key: bare 'on:' in YAML parses as True key
    nodes = data.get("nodes", {})
    if isinstance(nodes, dict):
        for node in nodes.values():
            if isinstance(node, dict) and True in node and "on" not in node:
                node["on"] = node.pop(True)
    try:
        _reject_non_string_keys(data, "")
    except TreeLoadError as exc:
        raise TreeLoadError(f"{path}: {exc}") from exc
    try:
        # sort_keys=True deliberately: this must exercise exactly what
        # Tree.content_hash() does later, so non-JSON values (dates etc.)
        # fail fast here at load time instead of at tree_start.
        json.dumps(data, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TreeLoadError(
            f"{path}: tree contains non-JSON values (quote dates and other scalars): {exc}"
        ) from exc
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
