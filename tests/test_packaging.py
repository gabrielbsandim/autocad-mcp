"""The three install paths (MCPB bundle, Claude Code plugin, pip/uv) must ship the same version and settings."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from autocad_mcp import layer_standard

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_versions_match():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert load("manifest.json")["version"] == version
    assert load(".claude-plugin/plugin.json")["version"] == version


def test_manifest_env_only_uses_declared_settings():
    manifest = load("manifest.json")
    env = manifest["server"]["mcp_config"]["env"]
    used = {v.removeprefix("${user_config.").removesuffix("}") for v in env.values()}
    assert used == set(manifest["user_config"])


def test_built_in_standards_load_and_every_layer_is_unique():
    for name, path in layer_standard.BUILT_IN.items():
        rules = layer_standard.load_standard(path)
        names = [r.name.upper() for r in rules]
        assert len(names) == len(set(names)), name
    assert load("manifest.json")["user_config"]["layer_standard"]["default"] in layer_standard.BUILT_IN
