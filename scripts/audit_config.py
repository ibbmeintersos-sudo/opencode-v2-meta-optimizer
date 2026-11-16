#!/usr/bin/env python3
"""Read-only, dependency-free OpenCode config syntax auditor.

Supports JSON and the common JSONC comment/trailing-comma forms. This is a
syntax check plus conservative recommendations, not full schema validation.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path


def strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas while preserving strings."""
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            end = text.find("*/", i)
            if end < 0:
                raise ValueError("Unterminated block comment")
            i = end + 2
        else:
            out.append(ch)
            i += 1
    cleaned = "".join(out)
    # Remove commas immediately before a closing object/array token.
    return re.sub(r",\s*([}\]])", r"\1", cleaned)


def audit(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8")
        config = json.loads(strip_jsonc(raw))
    except FileNotFoundError:
        return [f"File not found: {path}"], notes
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return [f"Could not parse JSON/JSONC: {exc}"], notes

    if not isinstance(config, dict):
        return ["Top-level configuration must be a JSON object."], notes

    if not config.get("$schema"):
        notes.append("No $schema URL found; add the official schema for editor validation.")
    if "tools" in config:
        notes.append("Top-level 'tools' settings may be legacy/deprecated in v2; verify against installed-version docs and prefer permissions where applicable.")
    if "mcp" in config and isinstance(config["mcp"], dict):
        mcp = config["mcp"]
        if any(key not in {"servers", "timeout"} for key in mcp):
            notes.append("MCP config has direct keys other than 'servers'/'timeout'; verify v2 expects server definitions under mcp.servers.")
    for field in ("model", "small_model"):
        if field in config and not isinstance(config[field], str):
            errors.append(f"'{field}' should be a provider/model string.")

    agents = config.get("agent", {})
    if isinstance(agents, dict):
        for name, agent in agents.items():
            if not isinstance(agent, dict):
                errors.append(f"Agent '{name}' must be an object.")
                continue
            mode = agent.get("mode")
            if mode and mode not in {"primary", "subagent"}:
                notes.append(f"Agent '{name}' has mode '{mode}'; confirm it is valid for the installed version.")
            if "tools" in agent and "permission" not in agent:
                notes.append(f"Agent '{name}' uses legacy 'tools'; v2 recommends granular 'permission' rules.")

    print(f"File: {path}")
    print("Syntax: valid JSON/JSONC")
    print("Scope: syntax and limited static checks only; official schema/runtime validation not performed")
    for item in notes:
        print(f"NOTE: {item}")
    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="~/.config/opencode/opencode.json", help="Path to opencode.json or opencode.jsonc")
    args = parser.parse_args()
    errors, _ = audit(Path(args.config).expanduser())
    for item in errors:
        print(f"ERROR: {item}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
