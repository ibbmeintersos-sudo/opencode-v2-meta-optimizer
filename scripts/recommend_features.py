#!/usr/bin/env python3
"""Read official OpenCode docs/changelog and recommend features without changing config."""
from __future__ import annotations
import argparse
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

DOCS = {
    "Configuration": "https://opencode.ai/docs/config/",
    "CLI commands": "https://opencode.ai/docs/cli/commands/",
    "Agents": "https://opencode.ai/docs/agents/",
    "Skills": "https://opencode.ai/docs/skills/",
    "MCP servers": "https://opencode.ai/docs/mcp-servers/",
    "Tools and permissions": "https://opencode.ai/docs/tools/",
    "Custom tools": "https://opencode.ai/docs/custom-tools/",
    "Plugins": "https://opencode.ai/docs/plugins/",
    "Formatters": "https://opencode.ai/docs/formatters/",
    "LSP servers": "https://opencode.ai/docs/lsp/",
    "Changelog": "https://opencode.ai/changelog",
}

FEATURES = [
    {"id":"permissions", "title":"Agent-specific least-privilege permissions", "need":"agents", "summary":"Use per-agent permissions so read-only reviewers cannot edit files or run shell commands.", "benefit":"Reduces accidental changes and limits the impact of a misdirected agent.", "risk":"Overly restrictive rules can block legitimate work; verify the resolved config and test in a disposable project.", "source":"Agents"},
    {"id":"skills", "title":"Reusable, discoverable Skills", "need":"skills", "summary":"Store focused workflows in .opencode/skills/<id>/SKILL.md rather than repeating long instructions in every prompt.", "benefit":"Improves repeatability while keeping unrelated instructions out of each task context.", "risk":"Third-party or project instructions can be untrusted; review each skill and its supporting scripts.", "source":"Skills"},
    {"id":"mcp", "title":"Selective MCP integration and Code Mode", "need":"mcp", "summary":"Add only MCP servers required for a workflow. OpenCode documents MCP Code Mode settings; verify per-agent scoping against your installed version before relying on it.", "benefit":"Connects the agent to relevant tools and may reduce unnecessary tool descriptions in context.", "risk":"MCP tools may access external services or transmit data. Credentials and server provenance need careful review.", "source":"MCP servers"},
    {"id":"plugins", "title":"Plugins and lifecycle hooks", "need":"plugin", "summary":"Use reviewed plugins only when a required capability cannot be met by built-in features or an MCP server.", "benefit":"Can add integrations, hooks, or custom tools.", "risk":"Plugins execute code and may run at startup; dependency and supply-chain risk is higher than for declarative config.", "source":"Plugins"},
    {"id":"formatter", "title":"Automatic formatting", "need":"formatter", "summary":"Configure formatters for languages present in the project so formatting runs after edits where supported.", "benefit":"Keeps style consistent and avoids spending model turns on mechanical formatting.", "risk":"Formatters can rewrite files; test configuration and review diffs before committing.", "source":"Formatters"},
    {"id":"lsp", "title":"Language Server Protocol integration", "need":"lsp", "summary":"Enable/configure the relevant LSP server for the project languages where supported.", "benefit":"Provides diagnostics and code intelligence to help catch issues early.", "risk":"Requires compatible local language tooling and can add startup/resource overhead; availability varies by language.", "source":"LSP servers"},
    {"id":"worktree", "title":"Isolated Git worktree workflows", "need":"worktree", "summary":"For parallel or risky feature work, use Desktop worktree support or documented CLI worktree runs after confirming the installed version supports the workflow.", "benefit":"Separates changes across tasks and reduces conflicts with the primary working tree.", "risk":"Consumes extra disk space and can complicate branch cleanup or shared development state.", "source":"Changelog"},
    {"id":"compaction", "title":"Context compaction and pruning", "need":"compaction", "summary":"Review compaction settings for long sessions; pruning can reduce retained tool-output context.", "benefit":"Can help avoid context-window pressure in extended sessions.", "risk":"Pruned detail may no longer be available to the model; tune for your task and model.", "source":"Configuration"},
    {"id":"snapshot", "title":"Change snapshots and recovery", "need":"snapshot", "summary":"Keep snapshots enabled unless disk/indexing costs make a deliberate tradeoff necessary.", "benefit":"Supports undo/revert of agent changes through OpenCode's session workflow.", "risk":"May consume storage or slow very large repositories; disabling removes a recovery path.", "source":"Configuration"},
]

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, data):
        value=" ".join(data.split())
        if value: self.parts.append(value)
    def text(self): return " ".join(self.parts)

def fetch_text(url: str, timeout: float = 15.0) -> tuple[str|None, str|None]:
    request=urllib.request.Request(url, headers={"User-Agent":"OpenCode-Meta-Optimizer/0.2 (documentation audit; contact via GitHub)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw=response.read(5_000_000).decode("utf-8", "replace")
        parser=TextExtractor(); parser.feed(raw)
        return html.unescape(parser.text()), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, str(exc)

def detect_version() -> tuple[str, str]:
    """Return version text and evidence; never installs or updates the CLI."""
    try:
        run=subprocess.run(["opencode","--version"], capture_output=True, text=True, timeout=5, check=False)
        value=(run.stdout or run.stderr).strip().splitlines()
        if run.returncode == 0 and value:
            return value[0][:160], "detected via `opencode --version`"
        return "Not detected", "OpenCode CLI command returned no version (it may not be on PATH)"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "Not detected", "OpenCode CLI was not found on PATH; Desktop installation was not independently inspected"

def global_config_dirs() -> list[Path]:
    """Return documented and common global config dirs, without assuming one OS path."""
    roots=[]
    xdg=os.environ.get("XDG_CONFIG_HOME")
    if xdg: roots.append(Path(xdg).expanduser())
    roots.append(Path.home()/".config")  # documented OpenCode global config location
    if os.name == "nt":
        appdata=os.environ.get("APPDATA")
        if appdata: roots.append(Path(appdata)/"opencode")
    result=[]; seen=set()
    for root in roots:
        directory=root if root.name.lower()=="opencode" else root/"opencode"
        key=str(directory).casefold()
        if key not in seen: seen.add(key); result.append(directory)
    return result

def config_candidates(project_dir: Path) -> list[Path]:
    paths=[]
    custom=os.environ.get("OPENCODE_CONFIG")
    if custom: paths.append(Path(custom).expanduser())
    for directory in global_config_dirs():
        paths.extend([directory/"opencode.json", directory/"opencode.jsonc"])
    # Include the supplied project folder and ascend only through its Git root.
    directory=project_dir
    while True:
        paths.extend([directory/"opencode.json", directory/"opencode.jsonc"])
        if (directory/".git").exists() or directory.parent==directory: break
        directory=directory.parent
    result=[]; seen=set()
    for path in paths:
        try: key=str(path.resolve()).casefold()
        except OSError: key=str(path).casefold()
        if key not in seen and path.is_file(): seen.add(key); result.append(path)
    return result

def load_jsonc(path: Path) -> dict|None:
    # Reuse the project's syntax cleaner without executing or importing config code.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from audit_config import strip_jsonc
        value=json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
        return value if isinstance(value,dict) else None
    except Exception:
        return None

def detect_signals(configs: list[dict], project_dir: Path) -> dict:
    merged={}
    for config in configs: merged.update(config)
    opencode_dir=project_dir/".opencode"
    agent_dirs=[opencode_dir/"agents"]+[d/"agents" for d in global_config_dirs()]
    skill_dirs=[opencode_dir/"skills"]+[d/"skills" for d in global_config_dirs()]
    plugin_dirs=[opencode_dir/"plugins"]+[d/"plugins" for d in global_config_dirs()]
    agents=any(d.is_dir() and any(d.glob("*.md")) for d in agent_dirs)
    skills=any(d.is_dir() and any(d.rglob("SKILL.md")) for d in skill_dirs)
    return {
      "agents": bool(merged.get("agent")) or agents,
      "skills": bool(merged.get("skills")) or skills,
      "mcp": bool(merged.get("mcp",{}).get("servers")) if isinstance(merged.get("mcp",{}),dict) else False,
      "plugin": bool(merged.get("plugin")) or bool(merged.get("plugins")) or any(d.is_dir() and any(d.glob("*.js")) for d in plugin_dirs),
      "formatter": bool(merged.get("formatter")), "lsp": bool(merged.get("lsp")),
      "worktree": bool(merged.get("worktree")) or bool(merged.get("experimental",{}).get("worktree")) if isinstance(merged.get("experimental",{}),dict) else bool(merged.get("worktree")),
      "compaction": bool(merged.get("compaction")), "snapshot": "snapshot" in merged,
      "model": bool(merged.get("model")), "provider": bool(merged.get("provider")),
    }

def latest_changelog_version(text: str|None) -> str|None:
    if not text: return None
    match=re.search(r"\bv(\d+\.\d+\.\d+)\b", text)
    return "v"+match.group(1) if match else None

def build_report(project_dir: Path, offline: bool=False) -> str:
    now=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    version, version_evidence=detect_version()
    paths=config_candidates(project_dir)
    configs=[c for p in paths if (c:=load_jsonc(p)) is not None]
    signals=detect_signals(configs, project_dir)
    results={}
    if offline:
        errors={name:"offline mode" for name in DOCS}
    else:
        errors={}
        for name,url in DOCS.items():
            text,error=fetch_text(url)
            if text: results[name]=text
            else: errors[name]=error or "unavailable"
    release=latest_changelog_version(results.get("Changelog"))
    lines=["# OpenCode Feature Discovery Report","",f"Generated: {now}","", "## Environment detected", "",f"- OpenCode version: **{version}** ({version_evidence})", f"- Latest version listed on official changelog: **{release or 'not detected'}**",f"- Config files found: **{len(paths)}**; successfully parsed: **{len(configs)}** (paths are intentionally not printed)", f"- Provider/model settings detected: **{'yes' if any(c.get('provider') or c.get('model') for c in configs) else 'no' if configs else 'unknown - no config parsed'}** (values are not displayed)",f"- Project directory: `{project_dir.name or '.'}` (the directory and parents up to its Git root plus standard global locations were inspected)",f"- Online documentation checks: **{len(results)}/{len(DOCS)}** pages fetched" if not offline else "- Online documentation checks: skipped (`--offline`)","", "> This is a recommendation report, not a full resolved-config audit. OpenCode's config precedence, custom directories, environment content, and Desktop-specific state may not be fully visible here. No configuration was changed."]
    if not configs:
        lines += ["", "> **Important:** No configuration file was parsed. Every ‘not detected’ feature status below means only that it was not found in the inspected locations; it does not prove the feature is absent or disabled. Check the config path and rerun before using these results as an audit."]
    lines += ["", "## Recommendations", ""]
    for feature in FEATURES:
        if feature["source"] not in results:
            continue
        configured=signals.get(feature["need"],False)
        if feature["id"]=="worktree":
            state="Documented OpenCode workflow; not a config flag to detect"
        elif not configs and feature["id"]!="worktree":
            state="Unknown - no local config was parsed"
        elif feature["id"]=="snapshot" and configured: state="Explicitly configured"
        elif configured: state="Detected in inspected config/files"
        else: state="Not detected in inspected config/files"
        if feature["id"]=="worktree":
            recommendation=f"The official changelog documents Desktop and CLI worktree workflows. Check that they are available in detected version {version}, then test with a disposable Git repository. No config change is suggested."
        elif not configs:
            recommendation="Cannot make a configuration-specific recommendation until the correct config path is detected; this is a general feature candidate only."
        elif feature["id"]=="snapshot" and not configured:
            recommendation="Check whether snapshots are enabled by the installed OpenCode default; absence from config does not prove snapshots are disabled."
        elif configured:
            recommendation="Review whether the existing setup meets your use case; avoid adding duplicates."
        else:
            recommendation="Consider a small, project-scoped trial only if this feature solves a real workflow need."
        lines += [f"### {feature['title']}","",f"- Status: **{state}**", "- Version compatibility: documented capability, but its first supported release is not inferred automatically; check the linked changelog for the detected version before adopting it.",f"- Recommendation: {recommendation}",f"- What it does: {feature['summary']}",f"- Potential impact: {feature['benefit']}",f"- Risks / checks: {feature['risk']}",f"- Official source: [{feature['source']}]({DOCS[feature['source']]})",""]
    lines += ["## Recent release notes",""]
    changelog=results.get("Changelog","")
    versions=[]
    for match in re.finditer(r"\bv\d+\.\d+\.\d+\b",changelog):
        v=match.group(0)
        if v not in versions: versions.append(v)
        if len(versions)==5: break
    if versions:
        lines.append("The official changelog page currently lists: " + ", ".join(f"`{v}`" for v in versions) + ". This report does not assume every release feature applies to the detected installation.")
        lines.append("")
        # Surface a compact set of release labels matching desktop/workflow improvements.
        candidates=[]
        for term in ("Worktree", "MCP", "agent", "Desktop", "formatter", "LSP", "permission", "plugin", "compaction"):
            for m in re.finditer(r"[^.!?\n]{0,90}"+re.escape(term)+r"[^.!?\n]{0,130}",changelog,re.I):
                snippet=" ".join(m.group(0).split())
                if len(snippet)>18 and snippet not in candidates: candidates.append(snippet)
                if len(candidates)>=8: break
            if len(candidates)>=8: break
        if candidates:
            lines.append("Potentially relevant changelog excerpts (review the linked entry for full context):")
            lines.extend(f"- {html.escape(item)}" for item in candidates)
            lines.append("")
    else:
        lines.append("The changelog could not be read or its release headings could not be recognized.")
        lines.append("")
    lines += ["## Sources and limitations","", "Official pages successfully fetched:"]
    if results:
        for name in results: lines.append(f"- [{name}]({DOCS[name]})")
    else: lines.append("- None")
    if errors:
        lines += ["", "Pages unavailable during this run:"]
        for name,error in errors.items(): lines.append(f"- [{name}]({DOCS[name]}): {error[:180]}")
    lines += ["", "The tool reads official documentation pages live and makes conservative recommendations from locally visible configuration signals. It reports the installed CLI version but does not automatically map every feature to its minimum supported release, nor verify remote provider compatibility, MCP health, secret validity, or complete configuration precedence. Check version compatibility in the linked changelog. Review each source and recommendation before making changes.","", "## Next step", "", "Choose recommendations that fit your workflow, then ask for an implementation plan. This tool never edits or installs OpenCode configuration.",""]
    return "\n".join(lines)

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir",default=".",help="Project directory to inspect (read-only)")
    parser.add_argument("--output",help="Optional Markdown report path; defaults to stdout")
    parser.add_argument("--offline",action="store_true",help="Skip network fetches (no live source recommendations)")
    args=parser.parse_args()
    report=build_report(Path(args.project_dir).expanduser().resolve(), args.offline)
    if args.output:
        destination=Path(args.output).expanduser().resolve()
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(report,encoding="utf-8")
        print(f"Report saved to {destination}")
    else: print(report)
    return 0

if __name__=="__main__": raise SystemExit(main())
