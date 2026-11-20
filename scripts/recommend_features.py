#!/usr/bin/env python3
"""Read official OpenCode docs/changelog and recommend features without changing config."""
from __future__ import annotations
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
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
import urllib.parse
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

OFFICIAL_SOURCES = {
    "OpenCode website": "https://opencode.ai/",
    "Docs inventory": "https://opencode.ai/docs/llms.txt",
    "Changelog": "https://opencode.ai/changelog",
    "GitHub repository": "https://github.com/anomalyco/opencode",
    "GitHub repository API": "https://api.github.com/repos/anomalyco/opencode",
    "GitHub releases": "https://github.com/anomalyco/opencode/releases",
    "GitHub releases API": "https://api.github.com/repos/anomalyco/opencode/releases",
    "Discord invite": "https://opencode.ai/discord",
    "X account": "https://x.com/opencode",
}
RELEASES_PER_PAGE = 20
GITHUB_RELEASES_API = f"https://api.github.com/repos/anomalyco/opencode/releases?per_page={RELEASES_PER_PAGE}&page={{page}}"
GITHUB_REPO_API = "https://api.github.com/repos/anomalyco/opencode"
MAX_RELEASE_PAGES = 5
MAX_SOURCE_BYTES = 2_000_000
MAX_V2_DOC_BYTES = 250_000
MAX_V2_DOC_FILES = 40
GITHUB_CONTENTS_API = "https://api.github.com/repos/anomalyco/opencode/contents/"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/anomalyco/opencode/"
V2_DOCS_ROOT = "services/site/src/docs/content/"
V2_CORE_DOC_NAMES = {
    "agents.mdx", "commands.mdx", "compaction.mdx", "config.mdx", "formatters.mdx",
    "instructions.mdx", "mcp-servers.mdx", "migrate-v1.mdx", "models.mdx", "permissions.mdx",
    "plugins.mdx", "policies.mdx", "providers.mdx", "skills.mdx", "snapshots.mdx", "tools.mdx",
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

def fetch_json(url: str, timeout: float = 15.0) -> tuple[object|None, str|None, dict]:
    """Fetch a bounded public JSON endpoint without credentials or arbitrary user URLs."""
    request=urllib.request.Request(url,headers={"User-Agent":"OpenCode-v2-Meta-Optimizer/0.5 (public official-source audit)","Accept":"application/vnd.github+json, application/json"})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            raw=response.read(MAX_SOURCE_BYTES+1)
            if len(raw)>MAX_SOURCE_BYTES: return None,"response exceeded size limit",{"status":response.status}
            data=json.loads(raw.decode("utf-8","replace"))
            meta={"status":response.status,"etag":response.headers.get("ETag"),"last_modified":response.headers.get("Last-Modified"),"rate_limit_remaining":response.headers.get("X-RateLimit-Remaining")}
            return data,None,meta
    except urllib.error.HTTPError as exc:
        return None,f"HTTP {exc.code}",{"status":exc.code,"rate_limit_remaining":exc.headers.get("X-RateLimit-Remaining") if exc.headers else None}
    except (urllib.error.URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
        return None,type(exc).__name__,{}


def version_tuple(tag: str) -> tuple[int,int,int]|None:
    """Accept only stable OpenCode v2 release tags, never prerelease/component tags."""
    match=re.fullmatch(r"v?2\.(\d+)\.(\d+)",str(tag).strip())
    return (2,int(match.group(1)),int(match.group(2))) if match else None


def latest_changelog_v2_version(text: str|None) -> str|None:
    if not text: return None
    for match in re.finditer(r"\bv?2\.\d+\.\d+\b",text):
        value=match.group(0)
        return value if value.startswith("v") else "v"+value
    return None


def select_latest_v2_release(releases) -> dict|None:
    if not isinstance(releases,list): return None
    candidates=[]
    for item in releases:
        if not isinstance(item,dict) or item.get("draft") is True or item.get("prerelease") is True: continue
        tag=item.get("tag_name","")
        parsed=version_tuple(tag)
        if parsed is None: continue
        candidates.append((parsed,item))
    if not candidates: return None
    candidates.sort(key=lambda pair:pair[0],reverse=True)
    tag=candidates[0][1].get("tag_name")
    safe_tag=urllib.parse.quote(str(tag),safe=".")
    return {"version":tag,"url":f"https://github.com/anomalyco/opencode/releases/tag/{safe_tag}","published_at":candidates[0][1].get("published_at"),"release_name":candidates[0][1].get("name") or tag,"prerelease":False,"draft":False}


def collect_official_source_data():
    """Fetch fixed official entry points and public GitHub metadata; never scrape Discord/X."""
    website,website_error=fetch_text(OFFICIAL_SOURCES["OpenCode website"],timeout=12)
    docs_index,docs_error=fetch_text(OFFICIAL_SOURCES["Docs inventory"],timeout=12)
    repo,repo_error,repo_meta=fetch_json(GITHUB_REPO_API,timeout=15)
    repo_ok=isinstance(repo,dict) and str(repo.get("full_name","")).casefold()=="anomalyco/opencode"
    readme,readme_error,readme_meta=fetch_json(GITHUB_REPO_API+"/readme",timeout=15)
    readme_headings=[]; readme_info={"ok":False,"error":readme_error}
    if isinstance(readme,dict) and readme.get("encoding")=="base64" and isinstance(readme.get("content"),str):
        try:
            raw_readme=base64.b64decode(readme["content"],validate=False)
            if len(raw_readme)<=MAX_SOURCE_BYTES:
                readme_text=raw_readme.decode("utf-8","replace")
                readme_headings=[re.sub(r"[^A-Za-z0-9 .()&/+_-]","",line.lstrip("# ")).strip()[:120] for line in readme_text.splitlines() if line.startswith("#")][:12]
                readme_info={"ok":True,"sha":readme.get("sha"),"size":readme.get("size",len(raw_readme)),"headings":readme_headings,"meta":readme_meta}
            else: readme_info={"ok":False,"error":"README exceeded size limit"}
        except (ValueError,base64.binascii.Error): readme_info={"ok":False,"error":"invalid Base64 README response"}
    elif not readme_error: readme_info={"ok":False,"error":"unexpected README response shape"}
    releases=[]; release_error=None; release_meta={}; pages_read=0; release_scan_complete=False
    for page in range(1,MAX_RELEASE_PAGES+1):
        data,error,meta=fetch_json(GITHUB_RELEASES_API.format(page=page),timeout=15)
        pages_read+=1
        if error:
            release_error=error
            if not releases: release_meta=meta
            break
        if not isinstance(data,list):
            release_error="unexpected API response shape"
            break
        releases.extend(data)
        release_meta=meta
        if len(data)<RELEASES_PER_PAGE:
            release_scan_complete=True
            break
    return {
        "website":{"ok":website is not None,"error":website_error},
        "docs_inventory":{"ok":docs_index is not None,"error":docs_error,"item_count":len(set(re.findall(r"https://opencode\.ai/docs/[^\s)\]>]+",docs_index or "")))},
        "github_repo":{"ok":repo_ok,"error":repo_error if repo_error else (None if repo_ok else "repository identity did not match anomalyco/opencode"),"full_name":repo.get("full_name") if isinstance(repo,dict) else None,"default_branch":repo.get("default_branch") if isinstance(repo,dict) else None,"html_url":repo.get("html_url") if isinstance(repo,dict) else None,"updated_at":repo.get("updated_at") if isinstance(repo,dict) else None,"meta":repo_meta},
        "github_readme":readme_info,
        "github_releases":{"ok":release_error is None,"error":release_error,"latest_v2":select_latest_v2_release(releases),"records_read":len(releases),"pages_read":pages_read,"scan_complete":release_scan_complete,"meta":release_meta},
        "discord":{"status":"manual/public invite link only; not crawled or joined","url":OFFICIAL_SOURCES["Discord invite"]},
        "x":{"status":"user-provided @opencode account link; official ownership not independently verified here; announcement signal only, not crawled or authenticated","url":OFFICIAL_SOURCES["X account"]},
    }


def fetch_raw_text(url: str, timeout: float = 12.0) -> tuple[str|None,str|None]:
    """Fetch only bounded public files from the fixed official OpenCode GitHub raw host."""
    if not url.startswith(GITHUB_RAW_BASE):
        return None,"untrusted source URL"
    request=urllib.request.Request(url,headers={"User-Agent":"OpenCode-v2-Meta-Optimizer/0.6 (version-pinned public docs)","Accept":"text/plain"})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            raw=response.read(MAX_V2_DOC_BYTES+1)
        if len(raw)>MAX_V2_DOC_BYTES: return None,"document exceeded size limit"
        return raw.decode("utf-8","replace"),None
    except urllib.error.HTTPError as exc:
        return None,f"HTTP {exc.code}"
    except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:
        return None,type(exc).__name__


def _v2_doc_record(path: str, text: str, tag: str) -> dict:
    headings=[re.sub(r"[^A-Za-z0-9 .()&/+_:#-]","",m.group(1)).strip()[:120] for m in re.finditer(r"^#{1,3}\s+(.+?)\s*#*\s*$",text,re.M)][:8]
    frontmatter=re.search(r"\A---\s*\n(.*?)\n---",text,re.S)
    title_match=re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$",frontmatter.group(1),re.M) if frontmatter else None
    title=(title_match.group(1).strip() if title_match else (headings[0] if headings else Path(path).name))
    quoted_path=urllib.parse.quote(path,safe="/._-")
    quoted_tag=urllib.parse.quote(tag,safe=".")
    kind="v2 specification" if path.startswith("specs/v2/") else "release documentation"
    return {"path":path,"kind":kind,"title":title,"headings":headings,"characters":len(text),"url":f"https://github.com/anomalyco/opencode/blob/{quoted_tag}/{quoted_path}","content":text}


def collect_v2_documentation(release_data: dict|None) -> dict:
    """Fetch core website docs and all v2 specs from the latest stable v2 release tag.

    Two small GitHub Contents API listings provide the manifest; file bodies are fetched
    from fixed raw GitHub URLs concurrently, bounded in count/size, and never written to disk.
    """
    candidate=(release_data or {}).get("latest_v2") if isinstance(release_data,dict) else None
    if not isinstance(candidate,dict) or not version_tuple(candidate.get("version","")):
        return {"status":"unavailable","error":"no stable v2 release candidate","version":None,"files":[],"manifest_count":0,"selected_count":0,"downloaded_count":0,"spec_count":0,"docs_count":0}
    tag=candidate["version"]
    encoded_tag=urllib.parse.quote(tag,safe=".")
    root=urllib.parse.quote(V2_DOCS_ROOT.rstrip("/"),safe="/._-")
    spec_root="specs/v2"
    docs_listing,docs_error,docs_meta=fetch_json(f"{GITHUB_CONTENTS_API}{root}?ref={encoded_tag}",timeout=20)
    specs_listing,specs_error,specs_meta=fetch_json(f"{GITHUB_CONTENTS_API}{spec_root}?ref={encoded_tag}",timeout=20)
    errors=[error for error in (docs_error,specs_error) if error]
    if not isinstance(docs_listing,list) or not isinstance(specs_listing,list):
        return {"status":"unavailable","error":"; ".join(errors) or "unexpected GitHub Contents API response","version":tag,"files":[],"manifest_count":0,"selected_count":0,"downloaded_count":0,"spec_count":0,"docs_count":0}
    manifest=[]
    for entry in docs_listing:
        if isinstance(entry,dict) and entry.get("type")=="file" and entry.get("name") in V2_CORE_DOC_NAMES and entry.get("name"," ").endswith(".mdx"):
            path=entry.get("path")
            if isinstance(path,str) and path.startswith(V2_DOCS_ROOT): manifest.append(path)
    for entry in specs_listing:
        if isinstance(entry,dict) and entry.get("type")=="file" and entry.get("name","").endswith((".md",".mdx")):
            path=entry.get("path")
            if isinstance(path,str) and path.startswith("specs/v2/"): manifest.append(path)
    manifest=sorted(set(manifest))
    selected=manifest[:MAX_V2_DOC_FILES]
    if not selected:
        return {"status":"unavailable","error":"no supported core docs or v2 specs found in tagged directories","version":tag,"files":[],"manifest_count":len(manifest),"selected_count":0,"downloaded_count":0,"spec_count":0,"docs_count":0}
    def read_one(path):
        url=GITHUB_RAW_BASE+encoded_tag+"/"+urllib.parse.quote(path,safe="/._-")
        text,fetch_error=fetch_raw_text(url)
        return path,text,fetch_error
    results=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(read_one,path) for path in selected]
        for future in as_completed(futures): results.append(future.result())
    records=[]; failures=[]
    for path,text,fetch_error in sorted(results):
        if text is None:
            failures.append({"path":path,"error":fetch_error or "unavailable"})
        else:
            records.append(_v2_doc_record(path,text,tag))
    specs=sum(1 for item in records if item["kind"]=="v2 specification")
    docs=len(records)-specs
    complete=not failures and len(selected)==len(manifest)
    status="complete" if complete else "partial"
    if errors: status="partial"
    return {"status":status,"error":"; ".join(errors) if errors else None,"version":tag,"tag_url":f"https://github.com/anomalyco/opencode/tree/{encoded_tag}","files":records,"manifest_count":len(manifest),"selected_count":len(selected),"downloaded_count":len(records),"spec_count":specs,"docs_count":docs,"failed_count":len(failures),"failures":failures[:10],"complete":complete and not errors,"contents_meta":{"docs":docs_meta,"specs":specs_meta}}


def analyze_v2_configuration(config: dict|None, documentation: dict|None, config_source="static file analysis") -> dict:
    """Compare visible config structures with explicit migration statements from the pinned docs.

    This is intentionally conservative: it reports only concrete legacy fields that the fetched
    release documentation says are ignored or map to a behaviorally different V2 field. It never
    includes config values, names of user-defined resources, or document bodies in findings.
    """
    if not isinstance(config,dict) or not config:
        return {"status":"configuration unavailable","findings":[]}
    if not isinstance(documentation,dict) or documentation.get("status") not in ("complete","partial"):
        return {"status":"release documentation unavailable","findings":[]}
    files=documentation.get("files",[])
    migration=next((item for item in files if str(item.get("path","")).endswith("/migrate-v1.mdx")),None)
    if not isinstance(migration,dict) or not isinstance(migration.get("content"),str):
        return {"status":"migration text unavailable","findings":[]}
    text=migration["content"]
    version=documentation.get("version","unknown")
    findings=[]

    def add(code,title,evidence,impact,action,section):
        url=migration.get("url",documentation.get("tag_url",""))
        if section: url += "#"+section
        findings.append({"code":code,"severity":"Medium","title":title,"evidence":evidence,"impact":impact,"action":action,"source":url,"documentation_version":version,"config_source":config_source})

    compaction=config.get("compaction")
    if isinstance(compaction,dict) and any(key in compaction for key in ("tail_turns","prune")) and all(token in text for token in ("tail_turns","prune","ignored with a warning")):
        add("V2_COMPACTION_LEGACY_IGNORED","Legacy compaction options are ignored in this v2 release","The resolved config contains a legacy `tail_turns` or `prune` option; the tagged migration guide says V2 has no native equivalent and ignores both.","The setting may not preserve the intended recent context or pruning behavior.","Review the compaction policy in OpenCode Desktop. If the same goal is needed, test the documented token-budget setting on a copied config; do not assume a one-to-one conversion.","compaction")

    mcp=config.get("mcp")
    if isinstance(mcp,dict) and "servers" in mcp and isinstance(mcp.get("servers"),dict):
        legacy_enabled=[]
        for server in mcp["servers"].values():
            if isinstance(server,dict) and "enabled" in server: legacy_enabled.append(server)
        if legacy_enabled and all(token in text for token in ("replaces `enabled` with the inverse `disabled`","disabled")):
            add("V2_MCP_ENABLED_INVERSION","MCP server uses the legacy enabled flag","At least one configured MCP entry uses `enabled`; the tagged migration guide documents the inverse V2 `disabled` flag.","A mechanical rename without inverting the boolean would change whether a server is active.","Confirm the resolved server state with the installed CLI/Desktop. If migrating, invert the boolean deliberately and test server connectivity; no change is made by this audit.","mcp-servers")
        if any(isinstance(server,dict) and isinstance(server.get("timeout"),(int,float)) for server in mcp["servers"].values()) and all(token in text for token in ("separates timeout purposes","timeout")):
            add("V2_MCP_TIMEOUT_SHAPE","MCP timeout uses a single legacy value","A configured MCP server has a numeric timeout; the tagged migration guide describes separate catalog and execution timeout purposes.","A single timeout may not express the distinct limits the native V2 shape supports.","Check whether the current CLI normalizes this value. Only migrate after confirming which timeout behavior you intend to preserve.","mcp-servers")

    # Conflicting old/new root maps have explicit precedence rules; report only the count, never IDs.
    for old,new,label in (("agent","agents","agent definitions"),("permission","permissions","permission rules"),("snapshot","snapshots","snapshot behavior")):
        old_value,new_value=config.get(old),config.get(new)
        if old not in config or new not in config: continue
        if old=="permission" and isinstance(old_value,dict) and isinstance(new_value,list):
            aliases={}
            for legacy,canonical in (("bash","shell"),("task","subagent"),("write","edit"),("patch","edit")):
                if f"`{legacy}` is now `{canonical}`" in text: aliases[legacy]=canonical
            old_actions={aliases.get(action,action) for action in old_value}
            new_actions={item.get("action") for item in new_value if isinstance(item,dict) and isinstance(item.get("action"),str)}
            overlap=len(old_actions.intersection(new_actions))
            if not overlap: continue
        elif isinstance(old_value,dict) and isinstance(new_value,dict):
            overlap=len(set(old_value).intersection(new_value))
            if not overlap: continue
        elif old=="snapshot":
            if old_value==new_value: continue
            overlap=1
        elif old_value == new_value:
            continue
        else:
            continue
        if "valid native V2 value takes precedence" not in text: continue
        add("V2_LEGACY_NATIVE_CONFLICT_"+old.upper(),f"Legacy and native V2 {label} are both configured",f"Both `{old}` and `{new}` forms are present, with {overlap} overlapping setting group(s); the tagged migration guide says a valid native V2 value takes precedence.","The effective behavior may differ from what a file-by-file reading suggests, especially if the legacy form is expected to override.","Use `opencode debug config` to confirm the resolved behavior. Consolidate only after backing up and comparing the overlapping rules; the audit does not rewrite either form.","configuration")

    return {"status":"completed" if documentation.get("complete") else "partial documentation","version":version,"config_source":config_source,"findings":findings}


def render_v2_docs_section(data: dict|None, limit=20) -> list[str]:
    if not isinstance(data,dict) or data.get("status") in (None,"unavailable","not checked offline"):
        status=data.get("status","unavailable") if isinstance(data,dict) else "unavailable"
        detail=data.get("error") if isinstance(data,dict) else None
        return ["## GitHub v2 documentation", "", f"Version-pinned GitHub docs: **{status}**"+(f" ({detail})" if detail else "")+".",""]
    files=data.get("files",[])
    lines=["## GitHub v2 documentation", "", f"Fetched from official OpenCode GitHub tag `{data.get('version','unknown')}`: **{data.get('downloaded_count',0)}/{data.get('manifest_count',data.get('selected_count',0))} files** ({data.get('docs_count',0)} core docs, {data.get('spec_count',0)} v2 specifications); status: **{data.get('status')}**. Sources are pinned to the release tag, not the moving development branch. File contents are inspected in memory only and are not saved or sent to an AI service."]
    for item in files[:limit]:
        title=html.escape(str(item.get("title") or item.get("path") or "document"))
        lines.append(f"- [{title}]({item['url']}) — {item['kind']}")
    if len(files)>limit: lines.append(f"- {len(files)-limit} additional fetched document(s) omitted from this report index.")
    if data.get("failed_count"): lines.append(f"- {data['failed_count']} selected source file(s) could not be retrieved; this is a partial documentation fetch.")
    if data.get("selected_count",0)>=MAX_V2_DOC_FILES and data.get("manifest_count",0)>data.get("selected_count",0): lines.append("- The safety file-count limit was reached; this fetch may not include every candidate document.")
    lines.append("")
    return lines


def source_error(status):
    return "available" if status.get("ok") else "unavailable"+(": "+str(status.get("error")) if status.get("error") else "")


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
    source_data=collect_official_source_data() if not offline else {"website":{"ok":False,"error":"offline mode"},"docs_inventory":{"ok":False,"error":"offline mode"},"github_repo":{"ok":False,"error":"offline mode"},"github_readme":{"ok":False,"error":"offline mode"},"github_releases":{"ok":False,"error":"offline mode","latest_v2":None,"records_read":0,"pages_read":0,"scan_complete":False},"discord":{"status":"not checked offline","url":OFFICIAL_SOURCES["Discord invite"]},"x":{"status":"not checked offline","url":OFFICIAL_SOURCES["X account"]}}
    github_v2=source_data["github_releases"].get("latest_v2")
    v2_docs=collect_v2_documentation(source_data.get("github_releases")) if not offline else {"status":"not checked offline","error":"offline mode","files":[],"version":None,"selected_count":0,"downloaded_count":0,"spec_count":0,"docs_count":0}
    changelog_v2=latest_changelog_v2_version(results.get("Changelog"))
    release_values_agree=bool(github_v2 and changelog_v2==github_v2.get("version"))
    changelog_confirms=bool(release_values_agree and source_data["github_releases"].get("scan_complete"))
    lines=["# OpenCode Feature Discovery Report","",f"Generated: {now}", "", "## Environment detected", "",f"- OpenCode version: **{version}** ({version_evidence})", f"- Leading release heading detected on official changelog: **{release or 'not detected'}**",f"- Leading v2 release label on the changelog page: **{changelog_v2 or 'not detected'}**",f"- Latest stable v2 candidate from official GitHub Releases API: **{github_v2['version'] if github_v2 else 'unknown/unavailable'}**"+(f" (published {github_v2['published_at'] or 'date not provided'}; [release]({github_v2['url'] or OFFICIAL_SOURCES['GitHub releases']}))" if github_v2 else ""),f"- Cross-source release status: **{'corroborated by changelog and complete scan' if changelog_confirms else ('matches changelog; bounded scan is incomplete' if release_values_agree else 'not corroborated / sources disagree')}**",f"- Config files found: **{len(paths)}**; successfully parsed: **{len(configs)}** (paths are intentionally not printed)", f"- Provider/model settings detected: **{'yes' if any(c.get('provider') or c.get('model') for c in configs) else 'no' if configs else 'unknown - no config parsed'}** (values are not displayed)",f"- Project directory: `{project_dir.name or '.'}` (the directory and parents up to its Git root plus standard global locations were inspected)",f"- Online documentation checks: **{len(results)}/{len(DOCS)}** pages fetched" if not offline else "- Online documentation checks: skipped (`--offline`)","", "> This is a recommendation report, not a full resolved-config audit. OpenCode's config precedence, custom directories, environment content, and Desktop-specific state may not be fully visible here. No configuration was changed."]
    if not configs:
        lines += ["", "> **Important:** No configuration file was parsed. Every ‘not detected’ feature status below means only that it was not found in the inspected locations; it does not prove the feature is absent or disabled. Check the config path and rerun before using these results as an audit."]
    lines += ["", "## Official source coverage", "",f"Checked at: {now} (retrieval time; distinct from upstream publication dates).",f"- OpenCode website: {source_error(source_data['website'])} — [open](https://opencode.ai/). The homepage is a navigation source, not a version authority.",f"- Official docs inventory (`llms.txt`): {source_error(source_data['docs_inventory'])}"+(f"; {source_data['docs_inventory'].get('item_count',0)} indexed doc links." if source_data['docs_inventory'].get('ok') else ""),f"- Official changelog: {'available' if 'Changelog' in results else 'unavailable'} — [open](https://opencode.ai/changelog); first detected release heading: {release or 'none detected'}.",f"- Official GitHub repository metadata: {source_error(source_data['github_repo'])}"+(f"; repository `{source_data['github_repo']['full_name']}`, default branch `{source_data['github_repo']['default_branch']}`." if source_data['github_repo'].get('ok') else ""),f"- Official GitHub repository README: {source_error(source_data['github_readme'])}"+(f"; readme blob `{source_data['github_readme'].get('sha')}`, size {source_data['github_readme'].get('size')} bytes; headings summarized below." if source_data['github_readme'].get('ok') else ""),f"- GitHub Releases API: {source_error(source_data['github_releases'])}; read {source_data['github_releases'].get('records_read',0)} release records across {source_data['github_releases'].get('pages_read',0)} bounded page(s) ({'complete history reached' if source_data['github_releases'].get('scan_complete') else 'bounded scan; history may continue'}). [Releases page]({OFFICIAL_SOURCES['GitHub releases']}).",f"- Discord: {source_data['discord']['status']} — [invite](https://opencode.ai/discord). No joining or private channel access.",f"- X `@opencode`: {source_data['x']['status']} — [profile](https://x.com/opencode). X is not treated as an authoritative release feed.","", "### Release verification", ""]
    if source_data.get("github_readme",{}).get("headings"):
        lines.append("Repository README headings (content is not copied wholesale): "+"; ".join(f"`{heading}`" for heading in source_data["github_readme"]["headings"][:10])+".")
    if github_v2:
        if changelog_confirms:
            lines.append(f"The Releases API and changelog both identify `{github_v2['version']}` as the leading v2 release signal. The report treats it as corroborated at this check time, not as the installed CLI version.")
        elif release_values_agree:
            lines.append(f"The Releases API and changelog both show `{github_v2['version']}`, but the scan reached its {MAX_RELEASE_PAGES}-page cap. Treat this as a strong matching signal, not a complete-history proof.")
        else:
            lines.append(f"GitHub Releases API candidate `{github_v2['version']}` does not match the changelog's first v2 label `{changelog_v2 or 'unavailable'}` (overall first release label: `{release or 'unavailable'}`). Treat the API tag as a candidate; verify the release page and notes before calling it confirmed latest.")
    else:
        lines.append("No stable v2 release could be verified from the official public Releases API in this run. The tool will not substitute a search result, Discord/X announcement, or unrelated latest endpoint.")
    lines += [""]
    lines += render_v2_docs_section(v2_docs)
    lines += ["## Recommendations", ""]
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
    lines += ["", "The report checks official OpenCode pages and public GitHub repository README/metadata/releases live. The latest-v2 result is a release candidate cross-checked against the changelog; disagreement is shown rather than hidden. Discord and X are linked for manual verification only because they are not stable public version APIs. The tool does not infer installation compatibility solely from upstream latest release, and it does not verify model credential validity, MCP connectivity, or every Desktop-specific state. No local configuration is uploaded and no configuration is changed.","", "## Next step", "", "Review upstream version claims against their linked release/changelog records, then review each recommendation before making changes. This tool never edits or installs OpenCode configuration.",""]
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
