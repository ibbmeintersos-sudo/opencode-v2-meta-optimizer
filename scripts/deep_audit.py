#!/usr/bin/env python3
"""Deep, read-only OpenCode environment audit with local evidence and official sources."""
from __future__ import annotations
import argparse
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recommend_features as base

SCHEMA_URL = "https://opencode.ai/config.json"
SECRET_WORDS = ("key", "secret", "token", "password", "credential", "authorization", "cookie")
LANG_TOOLS = {"Python":["python","python3","ruff","pyright"],"JavaScript":["node","npm","pnpm","eslint","prettier"],"JavaScript/React":["node","npm","eslint","prettier"],"TypeScript":["node","npm","tsc","eslint","prettier"],"TypeScript/React":["node","npm","tsc","eslint","prettier"],"Go":["go","gopls","gofmt"],"Rust":["cargo","rust-analyzer","rustfmt"],"Java":["java","javac","jdtls"],"Kotlin":["java","kotlinc"],"C#":["dotnet"],"PHP":["php","composer"],"Ruby":["ruby","bundle"],"Shell":["sh","shellcheck"],"PowerShell":["pwsh"]}
LANG_EXT = {
    ".py":"Python", ".pyi":"Python", ".js":"JavaScript", ".jsx":"JavaScript/React", ".mjs":"JavaScript", ".cjs":"JavaScript",
    ".ts":"TypeScript", ".tsx":"TypeScript/React", ".go":"Go", ".rs":"Rust", ".java":"Java", ".kt":"Kotlin",
    ".cs":"C#", ".cpp":"C++", ".c":"C", ".h":"C/C++", ".php":"PHP", ".rb":"Ruby", ".swift":"Swift",
    ".sh":"Shell", ".ps1":"PowerShell", ".vue":"Vue", ".svelte":"Svelte"
}
SKIP_DIRS={".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", "target", "__pycache__", ".next", ".cache"}


def active_global_dir() -> Path:
    """Official active default, honoring XDG_CONFIG_HOME when it is set."""
    root=os.environ.get("XDG_CONFIG_HOME")
    return (Path(root).expanduser() if root else Path.home()/".config")/"opencode"


def managed_config_dirs() -> list[Path]:
    if os.name=="nt":
        root=os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA")
        return [Path(root)/"opencode"] if root else []
    if sys.platform=="darwin": return [Path("/Library/Application Support/opencode")]
    return [Path("/etc/opencode")]


def source_roots(project_dir: Path, global_only=False) -> list[tuple[str, Path]]:
    roots=[] if global_only else [("project", project_dir/".opencode")]
    current=project_dir
    while not global_only:
        if current != project_dir and (current/".opencode").is_dir(): roots.append(("ancestor project", current/".opencode"))
        if (current/".git").exists() or current.parent==current: break
        current=current.parent
    roots.extend(("global",directory) for directory in base.global_config_dirs())
    roots.extend(("managed",directory) for directory in managed_config_dirs())
    custom_dir=os.environ.get("OPENCODE_CONFIG_DIR")
    if custom_dir: roots.append(("custom directory",Path(custom_dir).expanduser()))
    output=[]; seen=set()
    for label,path in roots:
        try: ident=str(path.resolve()).casefold()
        except OSError: ident=str(path).casefold()
        if ident not in seen and path.is_dir(): seen.add(ident); output.append((label,path))
    return output


def skill_roots(project_dir: Path, global_only=False):
    roots=[]
    roots.extend(("global skill compatibility",Path.home()/name/"skills") for name in (".claude",".agents"))
    if not global_only:
        current=project_dir
        ancestors=[]
        while True:
            ancestors.append(current)
            if (current/".git").exists() or current.parent==current: break
            current=current.parent
        for directory in reversed(ancestors):
            roots.append(("project compatibility",directory/".claude"/"skills"))
            roots.append(("project compatibility",directory/".agents"/"skills"))
    return roots


def configured_skill_roots(merged, project_dir):
    roots=[]; remote=0
    value=merged.get("skills",[])
    if isinstance(value,dict):
        local_items=value.get("paths",[]) if isinstance(value.get("paths",[]),list) else []
        url_items=value.get("urls",[]) if isinstance(value.get("urls",[]),list) else []
        remote=sum(1 for item in url_items if isinstance(item,str) and item.startswith(("http://","https://")))
        value=local_items
    if not isinstance(value,list): return roots,remote
    for item in value:
        if not isinstance(item,str): continue
        if item.startswith(("http://","https://")):
            remote+=1
            continue
        path=Path(item).expanduser()
        if not path.is_absolute(): path=project_dir/path
        if path.is_dir(): roots.append(("configured skill path",path))
    return roots,remote


def config_sources(project_dir: Path, global_only=False):
    candidates=[]
    active=active_global_dir()
    for filename in ("opencode.json","opencode.jsonc"):
        candidates.append(("global",active/filename))
    # AppData is retained only as a fallback candidate; official docs point to ~/.config/opencode.
    if os.name=="nt":
        appdata=os.environ.get("APPDATA")
        fallback=Path(appdata)/"opencode" if appdata else None
        if fallback and fallback.resolve()!=active.resolve():
            for filename in ("opencode.json","opencode.jsonc"):
                candidates.append(("alternate global candidate",fallback/filename))
    custom=os.environ.get("OPENCODE_CONFIG")
    if custom: candidates.append(("custom override",Path(custom).expanduser()))
    if not global_only:
        current=project_dir
        while True:
            local=[( "project",current/name) for name in ("opencode.json","opencode.jsonc") if (current/name).is_file()]
            if local:
                candidates.extend(local)
                break  # Desktop resolves from the project directory toward its Git root.
            if (current/".git").exists() or current.parent==current: break
            current=current.parent
    for directory in managed_config_dirs():
        for filename in ("opencode.json","opencode.jsonc"):
            candidates.append(("managed",directory/filename))
    found=[]; seen=set()
    for scope,path in candidates:
        try: ident=str(path.resolve()).casefold()
        except OSError: ident=str(path).casefold()
        if ident in seen or not path.is_file(): continue
        seen.add(ident)
        found.append({"scope":scope,"path":path,"data":base.load_jsonc(path)})
    return found

def merge_dicts(target: dict, override: dict, path=()) -> dict:
    """Approximate documented merge rules while respecting Skills/MCP special cases."""
    result=dict(target)
    for key,value in override.items():
        prior=result.get(key)
        if path==() and key=="skills" and isinstance(prior,list) and isinstance(value,list):
            result[key]=prior+value
        elif path==("mcp",) and key=="servers" and isinstance(prior,dict) and isinstance(value,dict):
            merged_servers=dict(prior)
            # Higher-precedence config replaces a same-name server definition as one unit.
            merged_servers.update(value)
            result[key]=merged_servers
        elif isinstance(prior,dict) and isinstance(value,dict):
            result[key]=merge_dicts(prior,value,path+(key,))
        else:
            result[key]=value
    return result


def redact_secret_values(value):
    """Return a copy with values under credential-like keys removed."""
    if isinstance(value,dict):
        return {key:("[redacted]" if is_secret_key(str(key)) and child not in (None,"",False) else redact_secret_values(child)) for key,child in value.items()}
    if isinstance(value,list): return [redact_secret_values(child) for child in value]
    return value


def parse_debug_config_output(text):
    """Parse the JSON object emitted by `opencode debug config`; never expose raw text."""
    decoder=json.JSONDecoder()
    for index,char in enumerate(text):
        if char!="{": continue
        try: value,end=decoder.raw_decode(text[index:])
        except (json.JSONDecodeError,ValueError): continue
        if isinstance(value,dict) and (index==len(text)-len(text.lstrip()) or "$schema" in value or any(key in value for key in ("model","agents","agent","mcp","permission","permissions","provider","providers"))):
            return redact_secret_values(value)
    return None


def debug_config(project_dir: Path, global_only=False, cli_path=None, timeout=35):
    """Read resolved config from the installed CLI; raw stdout/stderr stay in memory and are discarded."""
    candidate=cli_path or os.environ.get("OPENCODE_CLI") or shutil.which("opencode")
    if not candidate:
        return {"status":"CLI unavailable","config":None,"version":None}
    executable=str(candidate)
    try:
        version_result=subprocess.run([executable,"--version"],capture_output=True,text=True,timeout=8,check=False,stdin=subprocess.DEVNULL)
        version=(version_result.stdout or version_result.stderr).strip().splitlines()
        version=version[0][:120] if version_result.returncode==0 and version else None
    except (OSError,subprocess.TimeoutExpired): version=None
    try:
        if global_only:
            # A fresh neutral directory prevents a nearby project config from entering a global-only audit.
            with tempfile.TemporaryDirectory(prefix="opencode-audit-") as neutral_dir:
                result=subprocess.run([executable,"debug","config"],cwd=neutral_dir,capture_output=True,text=True,timeout=timeout,check=False,stdin=subprocess.DEVNULL)
        else:
            result=subprocess.run([executable,"debug","config"],cwd=str(project_dir),capture_output=True,text=True,timeout=timeout,check=False,stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"status":"timed out","config":None,"version":version}
    except OSError:
        return {"status":"could not launch","config":None,"version":version}
    config=parse_debug_config_output(result.stdout or "") if result.returncode==0 else None
    if config is not None:
        return {"status":"resolved config loaded from installed CLI","config":config,"version":version}
    return {"status":"CLI returned non-JSON config or an error (details withheld)","config":None,"version":version}


def type_ok(value, schema_type):
    allowed=schema_type if isinstance(schema_type,list) else [schema_type]
    checks={"object":lambda x:isinstance(x,dict),"array":lambda x:isinstance(x,list),"string":lambda x:isinstance(x,str),"boolean":lambda x:isinstance(x,bool),"integer":lambda x:isinstance(x,int) and not isinstance(x,bool),"number":lambda x:isinstance(x,(int,float)) and not isinstance(x,bool),"null":lambda x:x is None}
    return any(checks[t](value) for t in allowed if t in checks)


def fetch_schema(timeout=12):
    request=urllib.request.Request(SCHEMA_URL,headers={"User-Agent":"OpenCode-Meta-Optimizer/0.4 (read-only schema check)"})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            return json.loads(response.read(2_000_000).decode("utf-8","replace")),None
    except Exception as exc:
        return None,str(exc)


def top_schema_check(records, schema):
    findings=[]
    props=schema.get("properties",{}) if isinstance(schema,dict) else {}
    if not props: return findings,False
    for record in records:
        data=record["data"]
        label=f"{record['scope']} config"
        if data is None:
            findings.append({"severity":"High","code":"CONFIG_PARSE","title":"Config file could not be parsed","evidence":f"A {record['scope']} OpenCode JSON/JSONC file exists but was not valid or readable.","impact":"The effective setup may be incomplete in this audit.","action":"Check the file syntax and permissions, then rerun the audit.","source":base.DOCS["Configuration"]})
            continue
        for key,value in data.items():
            if key not in props and key!="$schema":
                findings.append({"severity":"Medium","code":"UNKNOWN_CONFIG_KEY","title":f"Config key not found in current official schema: {key}","evidence":f"{label} contains key `{key}`.","impact":"It may be a typo, an extension, or a field removed/renamed in this release.","action":"Verify the field against the installed-version docs before changing it; do not delete it automatically.","source":base.DOCS["Configuration"]})
            elif key in props and props[key].get("deprecated") is True:
                paired={"agent":"agents","permission":"permissions","snapshot":"snapshots","plugin":"plugins","provider":"providers","autoupdate":"update","command":"commands","reference":"references","tools":"permissions"}.get(key)
                next_step=f"Review `{paired}` as the current-schema counterpart." if paired else "Check the schema for an equivalent replacement; none is assumed."
                findings.append({"severity":"Medium","code":"DEPRECATED_CONFIG_KEY","title":f"Deprecated config field: {key}","evidence":f"{label} uses `{key}`, which the current official schema marks deprecated."+(" Schema note: "+props[key].get("description","") if props[key].get("description") else ""),"impact":"It may continue to work today, but future versions may remove or change it; legacy and current examples can differ.","action":f"Do not rewrite blindly. {next_step} Test migration in a copied config, then validate with the installed OpenCode build.","source":SCHEMA_URL})
            elif key in props and isinstance(props[key],dict) and "type" in props[key] and not type_ok(value,props[key]["type"]):
                expected=props[key]["type"]
                findings.append({"severity":"High","code":"CONFIG_TYPE","title":f"Unexpected type for config key: {key}","evidence":f"{label}: `{key}` is {type(value).__name__}; current schema expects {expected}.","impact":"OpenCode may reject or ignore this setting.","action":"Check the official schema and correct only after reviewing the resolved config.","source":base.DOCS["Configuration"]})
    return findings,True


def parse_frontmatter(path: Path) -> dict:
    try: text=path.read_text(encoding="utf-8",errors="replace")
    except OSError: return {}
    if not text.startswith("---"): return {}
    parts=text.split("---",2)
    if len(parts)<3: return {}
    result={}; context=None; current_rule=None
    for line in parts[1].splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        if not line[:1].isspace():
            match=re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$",line)
            if not match: context=None; continue
            key,value=match.groups(); context=key
            if key=="permissions": result["_permissions"]=[]; current_rule=None
            elif key=="permission": result["_permission"]={}; current_rule=None
            elif value: result[key]=value.strip("\"'")
            continue
        stripped=line.strip()
        if context=="permission":
            match=re.match(r"^([A-Za-z0-9_*.-]+):\s*(.*?)\s*$",stripped)
            if match: result.setdefault("_permission",{})[match.group(1)]=match.group(2).strip("\"'")
        elif context=="permissions":
            if stripped.startswith("- "):
                current_rule={}; result.setdefault("_permissions",[]).append(current_rule)
                stripped=stripped[2:]
            match=re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$",stripped)
            if match and current_rule is not None:
                current_rule[match.group(1)]=match.group(2).strip("\"'")
    return result


def skill_files(root):
    """Mirror documented local skill discovery: root-level markdown or nested SKILL.md, bounded depth."""
    root=Path(root)
    if not root.is_dir(): return
    for current,dirs,files in os.walk(root):
        current_path=Path(current)
        depth=len(current_path.relative_to(root).parts)
        dirs[:]=[name for name in dirs if name!="node_modules" and not name.startswith(".")]
        if depth>6:
            dirs[:]=[]
            continue
        if "SKILL.md" in files:
            yield current_path/"SKILL.md"
            dirs[:]=[]
        elif depth==0:
            for name in files:
                if name.endswith(".md") and name!="SKILL.md": yield current_path/name


def inventory_files(roots, extra_skill_roots=None):
    agents=[]; skills=[]; commands=[]; plugins=[]; custom_tools=[]
    skill_sources=list(roots)+(extra_skill_roots or [])
    for scope,root in roots:
        for path in (root/"agents").glob("*.md") if (root/"agents").is_dir() else []:
            fm=parse_frontmatter(path)
            agents.append({"id":path.stem,"scope":scope,"mode":fm.get("mode","unspecified/default"),"description":bool(fm.get("description")),"role_text":fm.get("description",""),"permission":fm.get("_permission",{}),"permissions":fm.get("_permissions",[]),"tools":fm.get("tools"),"model":fm.get("model"),"classification":"custom","definition_source":"file"})
        for path in (root/"commands").glob("*.md") if (root/"commands").is_dir() else []: commands.append(path.stem)
        for path in (root/"plugins").glob("*.js") if (root/"plugins").is_dir() else []: plugins.append(path.name)
        for path in (root/"plugins").glob("*.ts") if (root/"plugins").is_dir() else []: plugins.append(path.name)
        for ext in ("*.ts","*.js"):
            for path in (root/"tools").glob(ext) if (root/"tools").is_dir() else []:
                custom_tools.append({"id":path.stem,"scope":scope,"filename":path.name})
    for scope,root in skill_sources:
        skill_root=root if root.name=="skills" else root/"skills"
        if skill_root.is_dir():
            for path in skill_files(skill_root):
                fm=parse_frontmatter(path)
                skills.append({"id":path.parent.name if path.name=="SKILL.md" else path.stem,"scope":scope,"description":bool(fm.get("description"))})
    return agents,skills,commands,plugins,custom_tools


def scan_languages(project_dir: Path, max_files=10000):
    counts={}; walked=0
    try:
        for root,dirs,files in os.walk(project_dir):
            dirs[:]=[d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                walked+=1
                if walked>max_files: return counts,walked,True
                language=LANG_EXT.get(Path(name).suffix.lower())
                if language: counts[language]=counts.get(language,0)+1
    except OSError: pass
    return counts,walked,False


def safe_label(value, limit=80):
    text=str(value)
    if "://" in text or re.search(r"[^/]+:[^/]*@",text): return "[redacted]"
    return text[:limit] if re.fullmatch(r"[@A-Za-z0-9_.:/-]{1,"+str(limit)+r"}",text) else "[redacted]"


def safe_version(value):
    text=str(value or "")[:120]
    return text if re.fullmatch(r"[A-Za-z0-9 .()+_-]{1,120}",text) else "unrecognized version text"


def is_secret_key(key: str) -> bool:
    return any(part in key.casefold() for part in SECRET_WORDS)


def summarize_credentials(value, prefix=""):
    found=[]
    if isinstance(value,dict):
        for key,child in value.items():
            label=f"{prefix}.{key}" if prefix else str(key)
            if is_secret_key(str(key)) and child not in (None,"",False): found.append(label)
            else: found.extend(summarize_credentials(child,label))
    elif isinstance(value,list):
        for index,child in enumerate(value): found.extend(summarize_credentials(child,f"{prefix}[{index}]"))
    return found


def agent_map(merged):
    result={}
    for key in ("agent","agents"):
        value=merged.get(key)
        if isinstance(value,dict): result.update(value)
    return result


BUILTIN_AGENT_IDS={"build","plan","general","explore","title","summary","compaction"}


def configured_agents(merged, explicit_agent_ids=None, resolved=False):
    """Normalize configured/resolved agents and distinguish built-ins from overrides."""
    result=[]
    explicit={str(name) for name in (explicit_agent_ids or set())}
    for name,definition in agent_map(merged).items():
        if not isinstance(definition,dict): continue
        role_text=str(definition.get("description",""))+" "+str(definition.get("system",definition.get("prompt","")))
        agent_id=str(name)
        is_builtin=agent_id in BUILTIN_AGENT_IDS
        is_explicit=agent_id in explicit
        classification="overridden built-in" if is_builtin and is_explicit else "built-in" if is_builtin else "custom"
        result.append({"id":agent_id,"scope":"resolved CLI config" if resolved else "JSON config","mode":definition.get("mode","unspecified/default"),"description":bool(definition.get("description")),"role_text":role_text,"permission":definition.get("permission",{}) if isinstance(definition.get("permission"),dict) else {},"permissions":definition.get("permissions",[]),"tools":definition.get("tools"),"model":definition.get("model"),"classification":classification,"definition_source":"resolved config" if resolved else "file"})
    return result


def _resource_matches(pattern, resource):
    """Match OpenCode permission resource globs without treating unrelated scopes as global."""
    pattern=str(pattern or "*")
    resource=str(resource or "*")
    if pattern in ("*", "**"): return True
    if resource in ("*", "**"):
        return pattern in ("*", "**")
    return fnmatch.fnmatchcase(resource,pattern)


def permission_decision_detail(entry, action, resource="*"):
    """Evaluate v2 action/resource/effect rules in declaration order; the last match wins."""
    legacy=entry.get("permission") if isinstance(entry,dict) else None
    if isinstance(legacy,dict) and (action in legacy or "*" in legacy):
        value=legacy.get(action,legacy.get("*"))
        if isinstance(value,dict):
            matches=[(str(k),v) for k,v in value.items() if _resource_matches(k,resource)]
            if matches: return {"effect":matches[-1][1],"action":action,"resource":matches[-1][0],"order":None,"source":"legacy permission map"}
            return None
        return {"effect":value,"action":action,"resource":"*","order":None,"source":"legacy permission map"}
    toggles=entry.get("tools") if isinstance(entry,dict) else None
    if isinstance(toggles,dict) and (action in toggles or "*" in toggles):
        value=toggles.get(action,toggles.get("*"))
        if value is False: return {"effect":"deny","action":action,"resource":"*","order":None,"source":"legacy tools toggle"}
        if value is True: return {"effect":"allow","action":action,"resource":"*","order":None,"source":"legacy tools toggle"}
    rules=entry.get("permissions") if isinstance(entry,dict) else None
    matched=[]
    if isinstance(rules,list):
        for index,rule in enumerate(rules,1):
            if not isinstance(rule,dict): continue
            rule_action=str(rule.get("action","")); rule_resource=str(rule.get("resource","*"))
            if rule_action not in (action,"*") or not _resource_matches(rule_resource,resource): continue
            matched.append({"effect":rule.get("effect"),"action":rule_action,"resource":rule_resource,"order":index,"source":"v2 permissions"})
    return matched[-1] if matched else None


def permission_decision(entry, tool, resource="*"):
    """Compatibility wrapper returning the final effect, if a rule matches."""
    detail=permission_decision_detail(entry,tool,resource)
    return detail.get("effect") if detail else None


def global_permission_decision(perms, tool):
    if isinstance(perms,dict):
        value=perms.get(tool,perms.get("*"))
        if isinstance(value,dict):
            if value.get("*")=="allow": return "allow"
            return None
        return value
    return permission_decision({"permissions":perms},tool)


def safe_resource_label(value):
    """Keep simple policy resources useful while withholding paths and credential-like text."""
    text=str(value or "*")
    if is_secret_key(text) or re.search(r"(?:token|secret|password|key)=",text,re.I): return "[redacted resource]"
    if "://" in text or "\\" in text or "/" in text: return "[path/resource pattern]"
    return safe_label(text)


def permission_rules(merged):
    value=merged.get("permissions")
    if isinstance(value,list): return value
    legacy=merged.get("permission")
    return legacy if isinstance(legacy,dict) else {}


def effective_agent_permission(agent, global_rules, action, resource="*"):
    """Evaluate visible global rules followed by agent-scoped rules, preserving order."""
    combined=[]
    if isinstance(global_rules,list):
        combined.extend(global_rules)
    elif isinstance(global_rules,dict):
        for rule_action,value in global_rules.items():
            if isinstance(value,dict):
                combined.extend({"action":rule_action,"resource":pattern,"effect":effect} for pattern,effect in value.items())
            else:
                combined.append({"action":rule_action,"resource":"*","effect":value})
    agent_rules=agent.get("permissions")
    if isinstance(agent_rules,list): combined.extend(agent_rules)
    agent_legacy=agent.get("permission")
    if isinstance(agent_legacy,dict):
        for rule_action,value in agent_legacy.items():
            if isinstance(value,dict):
                combined.extend({"action":rule_action,"resource":pattern,"effect":effect} for pattern,effect in value.items())
            else:
                combined.append({"action":rule_action,"resource":"*","effect":value})
    return permission_decision({"permissions":combined},action,resource)


def add_finding(findings,severity,code,title,evidence,impact,action,source):
    findings.append({"severity":severity,"code":code,"title":title,"evidence":evidence,"impact":impact,"action":action,"source":source})


def analyze(records, merged, agents, skills, commands, plugins, project_dir, schema_ok, remote_skill_count=0, custom_tools=None):
    findings=[]
    if not records:
        add_finding(findings,"Medium","NO_CONFIG","No OpenCode config file was found","No supported config file was found in the documented global locations or supplied project/Git-root paths.","The tool cannot determine active models, MCP servers, permissions, or configuration overrides.","Check OPENCODE_CONFIG, XDG_CONFIG_HOME, the official ~/.config/opencode path, and the project root. Do not treat missing-feature statuses as confirmed.",base.DOCS["Configuration"])
    elif all(r["data"] is None for r in records):
        add_finding(findings,"Critical","NO_PARSED_CONFIG","Config files exist but none parsed","At least one candidate file exists, but none could be interpreted as JSON/JSONC.","Subsequent recommendations would be guesses.","Fix syntax/encoding and rerun; the auditor never repairs the file automatically.",base.DOCS["Configuration"])
    if not schema_ok:
        add_finding(findings,"Informational","SCHEMA_UNAVAILABLE","Official schema check unavailable","Could not fetch the live JSON schema during this run.","Unknown config fields and invalid types could not be compared with the current schema.","Retry online; syntax and static checks still ran.",SCHEMA_URL)
    if "tools" in merged:
        add_finding(findings,"Medium","LEGACY_TOOLS","Legacy tool toggles detected","Top-level `tools` appears in a loaded config.","The current agent schema prefers granular `permission`/`permissions` rules; boolean tool switches obscure action-by-action decisions.","Compare the field with the installed release docs and migrate only after testing.",base.DOCS["Agents"])
    if merged.get("snapshot") is False or merged.get("snapshots") is False:
        add_finding(findings,"Medium","SNAPSHOT_DISABLED","Snapshots explicitly disabled","A loaded config sets `snapshot(s): false`.","OpenCode's UI recovery/undo path for agent changes is disabled.","Keep it enabled unless storage/indexing constraints justify the tradeoff.",base.DOCS["Configuration"])
    if merged.get("update")=="auto":
        add_finding(findings,"Medium","AUTOUPDATE_AUTO","Automatic update installation is configured","The current `update` setting is `auto`.","OpenCode may install updates without a separate manual update action, which can change behavior between sessions.","Choose this only if automatic version changes are acceptable; otherwise use a notification-only setting supported by your installed release.",base.DOCS["Configuration"])
    if merged.get("autoupdate") is False or merged.get("update")=="disable":
        add_finding(findings,"Low","AUTOUPDATE_OFF","Update notifications are disabled","A loaded config disables update notices with `autoupdate: false` or `update: disable`.","You may miss notices for fixes and releases.","Consider `notify` if you want update awareness without automatic installation.",base.DOCS["Configuration"])
    if isinstance(merged.get("compaction"),dict) and merged["compaction"].get("prune") is False:
        add_finding(findings,"Informational","COMPACTION_PRUNE_OFF","Old tool output is not pruned","`compaction.prune` is explicitly false.","Long sessions may retain more historical tool output in context.","For extended sessions, test pruning; check whether removed details matter to your workflow.",base.DOCS["Configuration"])
    if "permission" in merged and "permissions" in merged:
        add_finding(findings,"Medium","MIXED_PERMISSION_DIALECT","Legacy and current permission fields coexist","Both top-level `permission` and `permissions` are present in the merged files.","The report cannot assume which rules win across legacy settings, current ordered rules, and all runtime layers.","Inspect the installed-build behavior and migrate in a copied config; do not remove either field automatically.",SCHEMA_URL)
    if "agent" in merged and "agents" in merged:
        add_finding(findings,"Medium","MIXED_AGENT_DIALECT","Legacy and current agent fields coexist","Both top-level `agent` and `agents` maps are present.","Duplicate agent names or differences in field shape may make the displayed inventory differ from Desktop behavior.","Compare same-name entries and test the intended agent in Desktop after migrating a copy.",SCHEMA_URL)
    perms=permission_rules(merged)
    if not perms:
        add_finding(findings,"Informational","PERMISSIONS_DEFAULTS","No explicit global tool permission rules detected","Neither current `permissions` nor legacy `permission` rules are visible in the loaded configs.","OpenCode documentation says most tools are available without approval by default, while some file/path operations have built-in safeguards; exact behavior depends on tool and version.","Check the permission defaults for your installed build and add only the approvals/denials needed for your workflow.",base.DOCS["Tools and permissions"])
    policies=merged.get("policies",[])
    if isinstance(policies,list):
        for rule in policies:
            if not isinstance(rule,dict) or rule.get("action")!="integration.use": continue
            resource=str(rule.get("resource",""))
            if resource=="*" or resource.startswith(("mcp:","skill:","plugin:")):
                add_finding(findings,"Medium","INTEGRATION_POLICY_SCOPE_CHANGED","Check the scope of an `integration.use` rule", "The rule uses a broad resource or a legacy MCP/Skill/Plugin resource prefix.","Recent changelog notes say `integration.use` now applies to integration accounts only; MCP servers, Skills, and Plugins use separate `mcp.use`, `skill.use`, and `plugin.use` actions. A broad integration rule may no longer constrain those resources.","If you intended to restrict MCP/Skills/Plugins, inspect the installed release’s policy migration and define the corresponding explicit actions; verify against the exact build before changing it.","https://opencode.ai/changelog")
    for tool in ("shell","edit","external_directory"):
        value=global_permission_decision(perms,tool)
        if value=="allow":
            add_finding(findings,"High" if tool in ("shell","edit") else "Medium","BROAD_PERMISSION",f"Global `{tool}` permission is allow",f"The visible config contains a broad global `{tool}: allow` rule.","The capability may be available without approval for actions not matched by a later/narrower denial.","Review whether this is intentional; prefer scoped approvals or per-agent restrictions for read-only roles.",base.DOCS["Tools and permissions"])
    agent_ids={}
    for agent in agents:
        if agent.get("classification")=="built-in": continue
        agent_ids.setdefault(agent["id"],[]).append(agent["scope"])
    for agent_id,scopes in agent_ids.items():
        if len(scopes)>1:
            add_finding(findings,"Medium","DUPLICATE_AGENT_ID",f"Agent ID {safe_label(agent_id)} exists in multiple sources",f"The ID is present in {', '.join(safe_label(scope) for scope in scopes)}.","The active definition may depend on OpenCode source precedence, so the displayed rules may not be the definition selected by Desktop.","Keep IDs unique unless overriding is deliberate; confirm which definition is loaded in the installed build.",base.DOCS["Agents"])
    for agent in agents:
        classification=agent.get("classification","custom")
        if classification=="built-in":
            continue
        role=(agent["id"]+" "+str(agent.get("role_text",""))).casefold()
        likely_readonly=any(word in role for word in ("review","audit","read-only","readonly","explore","research"))
        if agent.get("tools"):
            add_finding(findings,"Medium","AGENT_LEGACY_TOOLS",f"Agent {safe_label(agent['id'])} uses legacy tools field",f"{safe_label(agent['scope'])} agent metadata contains `tools`.","V2 uses granular `permissions` rules with explicit actions and resources; legacy booleans can obscure which actions remain available.","Inspect the agent and consider explicit v2 rules for `edit` and `shell`; do not change automatically.",base.DOCS["Agents"])
        edit_state=effective_agent_permission(agent,perms,"edit"); shell_state=effective_agent_permission(agent,perms,"shell")
        if likely_readonly and (edit_state!="deny" or shell_state!="deny"):
            proven_allow=edit_state=="allow" or shell_state=="allow"
            severity="High" if proven_allow else "Medium"
            code="READONLY_AGENT_CAPABILITY" if proven_allow else "READONLY_AGENT_POLICY_UNRESOLVED"
            evidence=f"Custom/overridden agent is described as read-only-like; edit={edit_state or 'unspecified'}, shell={shell_state or 'unspecified'}. Only explicit matching rules are summarized; unspecified does not establish runtime permission."
            impact=("A matching allow rule explicitly grants a capability that can modify files or execute shell commands." if proven_allow else "The visible agent-specific rules do not prove a read-only policy. Global/default policy and tool-level safeguards may affect actual behavior.")
            action="If strict read-only behavior is required, add agent-scoped deny rules for `edit` and `shell`, then test in a disposable repository. Do not infer shell access from a missing or legacy `bash` field."
            add_finding(findings,severity,code,f"Read-only-like agent {safe_label(agent['id'])} needs permission review",evidence,impact,action,base.DOCS["Agents"])
        if not agent.get("description") and classification in ("custom","overridden built-in"):
            add_finding(findings,"Low","AGENT_NO_DESCRIPTION",f"Agent {safe_label(agent['id'])} has no description",f"No description metadata was detected for {safe_label(agent['scope'])} agent.","OpenCode may have less information for deciding when to invoke a specialized agent.","Add a concise, task-specific description.",base.DOCS["Agents"])
    if remote_skill_count:
        add_finding(findings,"Medium","REMOTE_SKILL_CATALOG","Remote skill catalogs are configured",f"Detected {remote_skill_count} HTTP(S) catalog URL(s); URLs and contents were not fetched or printed.","Remote catalogs can change the skills and instructions advertised to the model, expanding trust and network boundaries.","Review catalog ownership, HTTPS/origin controls, index entries, and version changes; enable only trusted sources.",base.DOCS["Skills"])
    custom_tools=custom_tools or []
    if custom_tools:
        add_finding(findings,"Medium","CUSTOM_TOOL_CODE","Custom tool implementation files are discovered",f"Found {len(custom_tools)} TypeScript/JavaScript file(s) under documented local/global custom-tool directories; source code was not read.","Custom tools are executable code called by the model and can perform actions beyond built-in tools.","Review the source, dependencies, inputs/outputs, and permissions; do not trust an unfamiliar tool merely because its name looks safe.",base.DOCS["Custom tools"])
        builtins={"bash","edit","write","read","grep","glob","list","lsp","apply_patch","skill","todowrite","webfetch","websearch","question"}
        for item in custom_tools:
            if item["id"].casefold() in builtins:
                add_finding(findings,"High","CUSTOM_TOOL_OVERRIDES_BUILTIN",f"Custom tool may override built-in `{safe_label(item['id'])}`",f"A custom tool file named `{safe_label(item['filename'])}` was found in {safe_label(item['scope'])} scope.","OpenCode documents that a custom tool with the same name takes precedence over the built-in tool, changing its behavior and trust boundary.","Inspect the implementation and test it in a disposable project; rename it if overriding the built-in behavior is unintended.",base.DOCS["Custom tools"])
    formatter=merged.get("formatter")
    if formatter:
        custom_formatter_count=sum(1 for entry in formatter.values() if isinstance(entry,dict) and isinstance(entry.get("command"),list)) if isinstance(formatter,dict) else 0
        add_finding(findings,"Informational","FORMATTER_EXECUTION_ENABLED","Formatters are enabled/configured",f"Formatter config is active; {custom_formatter_count} explicit custom command(s) were detected. Built-in/custom formatters may run after edits.","Enabled formatters can rewrite files or execute local formatter commands after the agent edits them.","Verify formatter executables, package sources, extensions, and command arguments; inspect diffs after formatting.",base.DOCS["Formatters"])
    lsp=merged.get("lsp")
    if lsp is True:
        add_finding(findings,"Informational","LSP_AUTO_ENABLED","Built-in LSP integration is enabled","`lsp: true` enables built-in language-server behavior for matching project extensions.","Servers may start automatically when relevant files are opened; some use installed or auto-installed project tooling and add resource overhead.","Enable LSP only for project languages that benefit; review the official server list and local package/tool behavior.",base.DOCS["LSP servers"])
    elif isinstance(lsp,dict):
        custom_lsp=[entry for entry in lsp.values() if isinstance(entry,dict) and entry.get("command") and not entry.get("disabled")]
        if custom_lsp:
            add_finding(findings,"Medium","CUSTOM_LSP_COMMAND","Custom LSP launch commands are configured",f"Found {len(custom_lsp)} enabled LSP server definition(s) with commands; command text was withheld.","OpenCode may launch these local processes for matching files; they receive the configured environment and project context.","Review executable provenance, arguments, environment variables, and extensions; test resource usage in a copy of the project.",base.DOCS["LSP servers"])
    instructions=merged.get("instructions")
    if isinstance(instructions,list) and instructions:
        add_finding(findings,"Informational","CUSTOM_INSTRUCTIONS","Additional instruction files are configured",f"Found {len(instructions)} configured instruction path/pattern(s); paths and contents were not printed or read.","These files add model instructions and may change behavior, especially if they are untrusted or pulled from project-controlled sources.","Review each source and its precedence/content; treat repository instructions as untrusted input and keep secrets out of prompt files.",base.DOCS["Configuration"])
    skill_ids={}
    for skill in skills: skill_ids.setdefault(skill["id"],[]).append(skill["scope"])
    for skill_id,scopes in skill_ids.items():
        if len(scopes)>1:
            add_finding(findings,"Medium","DUPLICATE_SKILL_ID",f"Skill ID {skill_id} exists in multiple sources",f"The ID is present in {', '.join(scopes)}.","OpenCode selects the higher-precedence definition, which may differ from the one a user expects.","Keep IDs unique or document the intended override; check exact case-sensitive ID and source precedence.",base.DOCS["Skills"])
    for skill in skills:
        if not skill["description"]:
            add_finding(findings,"Low","SKILL_NO_DESCRIPTION",f"Skill {skill['id']} has no description",f"No description was detected in {skill['scope']} skill metadata.","The model may not discover the skill automatically.","Add a clear frontmatter description; keep skill ID unique and review scripts it references.",base.DOCS["Skills"])
    mcp=merged.get("mcp",{})
    servers=mcp.get("servers",{}) if isinstance(mcp,dict) else {}
    if isinstance(mcp,dict):
        legacy_direct=[key for key,value in mcp.items() if key not in ("servers","timeout") and isinstance(value,dict)]
        if legacy_direct:
            add_finding(findings,"Medium","LEGACY_MCP_SHAPE","MCP servers use the deprecated direct-under-mcp shape",f"Found {len(legacy_direct)} server-like entries directly under `mcp`; v2 documents `mcp.servers`.","Legacy MCP forms may be converted or behave differently during overrides; server shadowing becomes easier to miss.","Review the official v2 MCP migration guidance and test a copied config; use unique server names and repeat full server settings in higher-precedence overrides.",base.DOCS["MCP servers"])
    global_server_names=set()
    for record in records:
        if record["scope"] in ("global","custom override","alternate global candidate","managed") and isinstance(record["data"],dict):
            layer_mcp=record["data"].get("mcp",{})
            layer_servers=layer_mcp.get("servers",{}) if isinstance(layer_mcp,dict) else {}
            if isinstance(layer_servers,dict):
                global_server_names.update(name for name,item in layer_servers.items() if isinstance(item,dict) and not item.get("disabled",False))
    if global_server_names:
        add_finding(findings,"Medium","GLOBAL_MCP_CONTEXT","Active MCP servers are configured in a global/custom layer",f"Found {len(global_server_names)} non-disabled MCP server(s) in global/custom configuration.","These servers may be available across unrelated projects and expose more context/tools than needed.","For single-user workflows, keep only broadly useful servers global; place project-specific servers at project scope or restrict tool permissions. Verify behavior in your OpenCode release.",base.DOCS["MCP servers"])
        for name,item in servers.items():
            if not isinstance(item,dict): continue
            if item.get("type")=="local" and item.get("command"):
                command=item["command"]
                if isinstance(command,list) and command and str(command[0]).lower() in ("npx","npm","pnpm","bunx"):
                    add_finding(findings,"Medium","MCP_NPX_RUNTIME",f"MCP {safe_label(name)} launches a package runner",f"The local MCP command uses `{command[0]}`.","Package runner commands can download or execute package code when launched; supply-chain and version-drift risk.","Pin/review package versions, inspect publisher/source, and keep credentials out of command arguments.",base.DOCS["MCP servers"])
    plugin_values=merged.get("plugins",merged.get("plugin",[]))
    if plugin_values:
        add_finding(findings,"Medium","PLUGIN_EXECUTION","OpenCode plugins are configured",f"The configuration lists {len(plugin_values) if isinstance(plugin_values,list) else 1} plugin package(s); local plugin file count is {len(plugins)}.","Plugins execute code and may add hooks/tools at startup.","Review package origin, version pinning, lifecycle hooks, and permissions before trusting them.",base.DOCS["Plugins"])
    return findings


def safe_model_info(merged):
    values=[]
    for key in ("model","small_model"):
        value=merged.get(key)
        if isinstance(value,str): values.append(f"{key}: `{safe_label(value,120)}`")
    if isinstance(merged.get("model"),dict):
        model=merged["model"]
        values.append(f"model: `{safe_label(model.get('providerID','?'))}/{safe_label(model.get('model','?'))}`"+(f" variant `{safe_label(model['variant'])}`" if model.get("variant") else ""))
    providers=merged.get("providers",merged.get("provider"))
    if isinstance(providers,dict) and providers:
        values.append("provider IDs: "+", ".join(f"`{safe_label(k)}`" for k in list(providers)[:20]))
    if isinstance(providers,dict):
        model_ids=[]
        for provider_id,definition in providers.items():
            if not isinstance(definition,dict): continue
            models=definition.get("models",{})
            if isinstance(models,dict):
                model_ids.extend(f"{safe_label(provider_id)}/{safe_label(model_id)}" for model_id in models)
        if model_ids: values.append("models declared inside provider config: "+", ".join(f"`{item}`" for item in model_ids[:40])+ (" (additional entries omitted)" if len(model_ids)>40 else ""))
    return values


def summarize_mcp(merged):
    mcp=merged.get("mcp",{})
    servers=mcp.get("servers",{}) if isinstance(mcp,dict) else {}
    out=[]
    if not isinstance(servers,dict): return out
    for name,entry in servers.items():
        if not isinstance(entry,dict): continue
        fields=[f"`{safe_label(name)}`",f"transport/type: {safe_label(entry.get('type','unspecified'))}","disabled: yes" if entry.get("disabled") else "disabled: no/unspecified"]
        if isinstance(entry.get("codemode"),bool): fields.append(f"Code Mode: {'enabled' if entry['codemode'] else 'disabled'}")
        auth=any(k in entry for k in ("oauth","headers","environment"))
        fields.append("auth/env configuration present: yes (values hidden)" if auth else "auth/env configuration not visible in this file")
        out.append("- "+"; ".join(fields))
    return out


def check_mcp_runtime(cli_path=None,timeout=45):
    """Opt-in runtime check; only returns server IDs and coarse status, never raw output."""
    executable=cli_path or os.environ.get("OPENCODE_CLI") or shutil.which("opencode")
    if not executable:
        return {"requested":True,"status":"CLI unavailable","servers":[],"returncode":None}
    try:
        result=subprocess.run([str(executable),"mcp","list"],capture_output=True,text=True,timeout=timeout,check=False,stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"requested":True,"status":"timed out","servers":[],"returncode":None}
    except OSError:
        return {"requested":True,"status":"could not launch","servers":[],"returncode":None}
    raw=(result.stdout or "")+"\n"+(result.stderr or "")
    raw=re.sub(r"\x1b\[[0-9;]*[A-Za-z]","",raw)
    statuses=[]
    for line in raw.splitlines():
        lower=line.casefold()
        if "needs authentication" in lower or "authentication required" in lower or "needs auth" in lower: status="authentication required"
        elif "connected" in lower and "disconnected" not in lower: status="connected"
        elif "disconnected" in lower: status="disconnected"
        elif "failed" in lower or "error" in lower: status="error"
        else: continue
        match=re.search(r"([A-Za-z0-9][A-Za-z0-9_.-]{0,79})",line.strip())
        name=match.group(1) if match else "server"
        if name.casefold() in {"connected","disconnected","error","failed","needs"}: name="server"
        statuses.append({"name":name,"status":status})
    return {"requested":True,"status":"completed" if result.returncode==0 else "nonzero exit","servers":statuses,"returncode":result.returncode}


def create_report(project_dir: Path, offline=False, global_only=False, check_mcp=False, use_debug_config=True, cli_path=None):
    version,version_evidence=base.detect_version()
    records=config_sources(project_dir,global_only)
    parsed=[r for r in records if r["data"] is not None]
    active_parsed=[r for r in parsed if r["scope"]!="alternate global candidate"]
    analysis_records=active_parsed or [r for r in parsed if r["scope"]=="alternate global candidate"]
    static_merged={}
    for record in analysis_records: static_merged=merge_dicts(static_merged,record["data"])
    debug_result=debug_config(project_dir,global_only,cli_path) if use_debug_config else {"status":"skipped by user","config":None,"version":None}
    if debug_result.get("version"):
        version=debug_result["version"]
        version_evidence="detected from the selected CLI used for `debug config`"
    merged=redact_secret_values(debug_result["config"]) if isinstance(debug_result.get("config"),dict) else redact_secret_values(static_merged)
    config_source="installed CLI debug config" if debug_result.get("config") is not None else "static file merge (fallback)"
    roots=source_roots(project_dir,global_only)
    extra_skill_roots,remote_skill_catalogs=configured_skill_roots(merged,project_dir)
    agents,skills,commands,plugins,custom_tools=inventory_files(roots,skill_roots(project_dir,global_only)+extra_skill_roots)
    explicit_agent_ids=set(agent_map(static_merged))|{str(item["id"]) for item in agents}
    for item in agents:
        if item["id"] in BUILTIN_AGENT_IDS:
            item["classification"]="overridden built-in"
    resolved_agents=configured_agents(merged,explicit_agent_ids,resolved=debug_result.get("config") is not None)
    existing_by_id={str(item["id"]):index for index,item in enumerate(agents)}
    for resolved_agent in resolved_agents:
        prior_index=existing_by_id.get(str(resolved_agent["id"]))
        if prior_index is None:
            agents.append(resolved_agent)
            existing_by_id[str(resolved_agent["id"])]=len(agents)-1
        else:
            # Keep the CLI-resolved definition for analysis, but preserve its local origin classification.
            resolved_agent["classification"]=agents[prior_index].get("classification","custom")
            agents[prior_index]=resolved_agent
    languages,file_count,truncated=({},0,False) if global_only else scan_languages(project_dir)
    schema,schema_error=(None,"offline mode") if offline else fetch_schema()
    schema_findings,schema_ok=top_schema_check(parsed,schema) if schema else ([],False)
    if debug_result.get("config") is not None and schema:
        # A live schema may lag the installed build. Suppress schema-only unknown/deprecated/type
        # warnings for fields the installed CLI retained; syntax/parse problems remain visible.
        accepted_keys=set(merged)
        schema_findings=[item for item in schema_findings if not (item["code"] in ("UNKNOWN_CONFIG_KEY","DEPRECATED_CONFIG_KEY","CONFIG_TYPE") and item["title"].split(": ",1)[-1] in accepted_keys)]
    findings=schema_findings+analyze(analysis_records,merged,agents,skills,commands,plugins,project_dir,schema_ok,remote_skill_catalogs,custom_tools)
    mcp_runtime=check_mcp_runtime(cli_path) if check_mcp else {"requested":False,"status":"not run","servers":[],"returncode":None}
    if check_mcp:
        for entry in mcp_runtime["servers"]:
            if entry["status"]!="connected":
                add_finding(findings,"Medium","MCP_RUNTIME_STATUS",f"MCP server {safe_label(entry['name'])} runtime status: {entry['status']}","The opt-in `opencode mcp list` check returned a non-connected state.","This integration may be unavailable to agents until its process, network endpoint, or authentication is fixed.","Inspect the server in OpenCode Desktop `/mcps`; do not share raw CLI output or credentials.",base.DOCS["MCP servers"])
    active_global_found=any(r["scope"]=="global" and r["data"] is not None for r in records)
    alternate_global_found=any(r["scope"]=="alternate global candidate" and r["data"] is not None for r in records)
    if alternate_global_found and not active_global_found:
        add_finding(findings,"Medium","GLOBAL_PATH_UNCONFIRMED","Only an alternate global config path was found","An AppData configuration was located, but the official docs describe ~/.config/opencode as the standard global path.","The auditor cannot prove this alternate file is the one loaded by your Desktop build.","Confirm the active config location for your installed build or set OPENCODE_CONFIG explicitly; treat findings from this file as provisional.",base.DOCS["Configuration"])
    elif alternate_global_found and active_global_found:
        add_finding(findings,"Medium","MULTIPLE_GLOBAL_CONFIGS","Two global config locations contain settings","Both the documented global path and a common alternate Windows AppData path contain config files; only the documented path is used for the main static merge.","The Desktop build may read a different location, so recommendations could describe the wrong active profile.","Confirm the exact config path used by your installed Desktop/CLI build before applying recommendations; keep one intended source or set `OPENCODE_CONFIG` explicitly.",base.DOCS["Configuration"])
    same_dir={}
    for r in records:
        if r["data"] is not None: same_dir.setdefault((r["scope"],str(r["path"].parent).casefold()),[]).append(r["path"].suffix.casefold())
    if any(len(suffixes)>1 for suffixes in same_dir.values()):
        add_finding(findings,"Low","MULTIPLE_CONFIG_FORMATS","Both JSON and JSONC config files exist in one source directory","The same config source has multiple recognized file formats.","The audit cannot assume which file the installed build prioritizes or whether both are merged.","Confirm the load order for your exact build and remove ambiguity only after backing up and testing.",base.DOCS["Configuration"])
    if os.environ.get("OPENCODE_CONFIG_CONTENT"):
        add_finding(findings,"High","INLINE_CONFIG_UNINSPECTED","Inline config override is present","OPENCODE_CONFIG_CONTENT is set; its value was intentionally not read or printed.","This high-precedence runtime layer may change the effective settings reported below.","Inspect it locally without sharing secrets, or rerun in a shell where the variable is not set if it is irrelevant.",base.DOCS["Configuration"])
    docs={}
    if offline: doc_errors={name:"offline mode" for name in base.DOCS}
    else:
        doc_errors={}
        for name,url in base.DOCS.items():
            text,error=base.fetch_text(url)
            if text: docs[name]=text
            else: doc_errors[name]=error or "unavailable"
    current=base.latest_changelog_version(docs.get("Changelog"))
    changelog_v2=base.latest_changelog_v2_version(docs.get("Changelog"))
    source_data=base.collect_official_source_data() if not offline else None
    v2_docs=base.collect_v2_documentation(source_data.get("github_releases")) if source_data else {"status":"not checked offline","error":"offline mode","files":[],"version":None,"selected_count":0,"downloaded_count":0,"spec_count":0,"docs_count":0}
    semantic=base.analyze_v2_configuration(merged,v2_docs,config_source)
    for item in semantic.get("findings",[]):
        add_finding(findings,item["severity"],item["code"],item["title"],item["evidence"],item["impact"],item["action"],item["source"])
    github_v2=(source_data or {}).get("github_releases",{}).get("latest_v2")
    release_values_agree=bool(github_v2 and changelog_v2==github_v2.get("version"))
    release_sources_agree=bool(release_values_agree and source_data and source_data["github_releases"].get("scan_complete"))
    version_match=re.search(r"\bv?(\d+\.\d+\.\d+)\b",version)
    if github_v2 and changelog_v2 and not release_sources_agree:
        add_finding(findings,"Medium","V2_RELEASE_SOURCE_MISMATCH","Official sources disagree on the latest v2 release",f"GitHub Releases API candidate is {github_v2['version']}; first v2 release label found on the changelog is {changelog_v2}.","The auditor cannot confirm the latest v2 release from this run; the API tag may still be newer, or page/API data may be inconsistent.","Open both official release records and confirm the release date/tag manually; do not treat this disagreement as a confirmed update recommendation.",f"{base.OFFICIAL_SOURCES['GitHub releases']} | {base.OFFICIAL_SOURCES['Changelog']}")
    if release_values_agree and not release_sources_agree:
        add_finding(findings,"Low","V2_RELEASE_SCAN_BOUNDED","Release and changelog labels agree, but the release scan was bounded",f"Both sources show {github_v2['version']}, but the public release list did not end within {base.MAX_RELEASE_PAGES} pages.","A later historical record could theoretically contain a higher stable v2 tag; the audit avoids presenting a bounded scan as a complete-history proof.","Use the linked GitHub Releases page to confirm the tag ordering; no local update is recommended from this finding.",base.OFFICIAL_SOURCES["GitHub releases"])
    if version_match and release_sources_agree:
        installed=tuple(int(x) for x in version_match.group(1).split(".")); latest_tuple=base.version_tuple(github_v2["version"])
        if installed[0]==2 and latest_tuple and installed<latest_tuple:
            add_finding(findings,"Medium","VERSION_BEHIND","Installed v2 CLI is behind the corroborated latest stable v2 release",f"CLI reports v{version_match.group(1)}; GitHub Releases and the official changelog both indicate {github_v2['version']} (published {github_v2.get('published_at') or 'date unavailable'}).","You may be missing fixes/features; the Desktop app and CLI may also differ.","Verify the Desktop version and linked official release notes, then update only through your normal trusted channel if appropriate.",github_v2.get("url") or base.OFFICIAL_SOURCES["GitHub releases"])
        elif installed==latest_tuple:
            add_finding(findings,"Informational","VERSION_CURRENT","Installed CLI matches the corroborated latest stable v2 release",f"CLI reports {version_match.group(1)} and both official release sources indicate {github_v2['version']}.","No newer stable v2 release is indicated by this point-in-time check.","Recheck later; this compares CLI only, not necessarily Desktop packaging.",github_v2.get("url") or base.OFFICIAL_SOURCES["GitHub releases"])
        elif installed[0]!=2:
            add_finding(findings,"Informational","VERSION_LINE_NOT_COMPARED","Installed CLI is outside the v2 release line",f"Detected CLI version {version_match.group(1)}; latest stable v2 candidate {github_v2['version']}.","Cross-major version comparison is not a reliable ‘behind’ judgment; OpenCode v1/v2 may have different release tracks.","Confirm whether the installed binary is v1 or v2 and consult the official migration/release guidance before changing versions.",base.DOCS["Changelog"])
    if merged and not merged.get("model"):
        add_finding(findings,"Informational","MODEL_NOT_EXPLICIT","No default model is explicit in loaded config","No top-level `model` value was found; model selection may be stored by the Desktop account/session instead.","The tool cannot compare configured model suitability, cost, or context size from this file.","Check the Desktop model picker and provider availability; do not infer a missing model solely from this config.",base.DOCS["Configuration"])
    severity_order={"Critical":0,"High":1,"Medium":2,"Low":3,"Informational":4}
    findings.sort(key=lambda item:(severity_order.get(item["severity"],5),item["code"],item["title"]))
    lines=["# OpenCode Deep Environment Audit", "",f"Generated: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}","", "## Executive assessment", "", f"- Detected CLI version: **{safe_version(version)}** ({version_evidence})", f"- Leading release on official changelog: **{current or 'not recognized'}**",f"- Latest stable v2 candidate from GitHub Releases: **{github_v2['version'] if github_v2 else ('not checked offline' if offline else 'unavailable')}**"+(f" (published {github_v2.get('published_at') or 'date unavailable'})" if github_v2 else ""),f"- v2 release corroboration: **{'confirmed by changelog and complete Releases scan' if release_sources_agree else ('matches changelog, bounded Releases scan' if release_values_agree else ('not checked offline' if offline else 'not corroborated / unknown'))}**",f"- Candidate config files: **{len(records)}**; parsed successfully: **{len(parsed)}**",f"- OpenCode support directories inspected: **{len(roots)}**",f"- Project files sampled for language types: **{file_count}{'+' if truncated else ''}**"+(" (global-only mode)" if global_only else ""),f"- Official docs retrieved: **{len(docs)}/{len(base.DOCS)}**; schema: **{'available' if schema_ok else 'unavailable'}**", "", "> The installed CLI `debug config` is the primary effective-config source when available. Live schema and official v2 documentation are supporting references; this is not proof that Desktop bundles the same build. No settings are edited, secret values are not printed, and local config is not sent to an AI service.",""]
    if not records or not parsed:
        lines += ["> **Coverage warning:** No usable config file was parsed. Configuration-dependent conclusions are unknown; see findings. Verify the path and rerun before acting.",""]
    lines += ["## Configuration coverage and precedence",""]
    lines.append(f"- Effective-config source: **{config_source}** ({debug_result['status']}); CLI version: **{safe_version(debug_result.get('version') or 'not detected')}**.")
    lines.append("- `opencode debug config` output is parsed locally; credential-like values are redacted in memory before analysis, and raw stdout/stderr are never included in the report. The CLI may load supported remote config, depending on its build and login state.")
    if records:
        for r in records:
            status="parsed" if r["data"] is not None else "unreadable/invalid"
            lines.append(f"- {r['scope']} layer: `{r['path'].name}` — {status}; values and full paths are omitted.")
        key_sources={}
        for r in records:
            if isinstance(r["data"],dict):
                for key in r["data"]: key_sources.setdefault(key,[]).append(r["scope"])
        overlaps={k:v for k,v in key_sources.items() if len(v)>1}
        if overlaps:
            lines.append("- Root settings present in multiple layers: "+", ".join(f"`{k}` ({' → '.join(v)})" for k,v in sorted(overlaps.items()))+". More-specific settings may override earlier values.")
    else: lines.append("No config candidates found in the documented/custom/global/project locations inspected.")
    lines += ["- OpenCode supports merged config layers. When `debug config` succeeds, the installed CLI's resolved configuration drives capability and permission analysis. The live schema is advisory and may lag; unknown/deprecated/type schema-only warnings are suppressed when that exact key is retained by the installed CLI. Static source files are still checked for syntax and parse problems.",""]
    lines += ["## Configuration detail (sensitive values withheld)",""]
    models=safe_model_info(merged)
    lines.extend("- "+line for line in models) if models else lines.append("- No model/provider IDs visible in parsed config; Desktop account/session model selection may be stored elsewhere.")
    permissions=permission_rules(merged)
    if isinstance(permissions,dict):
        names=[k for k in permissions if not is_secret_key(str(k))]
        rule_summary=", ".join(f"`{safe_label(key)}`={permissions[key] if permissions[key] in ('allow','ask','deny') else '[rule map/other]'}" for key in names[:30])
    elif isinstance(permissions,list):
        rule_summary=", ".join(f"`{safe_label(r.get('action','?'))}`[{safe_resource_label(r.get('resource','*'))}]={safe_label(r.get('effect','?'))}#{index}" for index,r in enumerate(permissions,1) if isinstance(r,dict))
    else: rule_summary=""
    lines.append("- Global permission rules: "+(rule_summary or "none explicit"))
    if isinstance(permissions,list) and permissions:
        lines.append("  - V2 matching uses `action` + `resource`; the last matching rule in this list wins. Resource paths/patterns are withheld or generalized.")
    policies=merged.get("policies",[])
    if isinstance(policies,list) and policies:
        policy_summary=", ".join(f"{safe_label(r.get('action','?'))}={r.get('effect','?')}" for r in policies[:20] if isinstance(r,dict))
        lines.append("- Provider/tool/integration policies: "+(policy_summary or f"{len(policies)} rules; details unavailable"))
    else: lines.append("- Provider/tool/integration policies: no explicit `policies` entries detected.")
    secret_fields=summarize_credentials(merged)
    lines.append(f"- Credential-like config fields: {len(secret_fields)} detected; field values and names are withheld.")
    lines.append("- MCP server inventory:")
    lines.extend(summarize_mcp(merged) or ["  - No MCP servers visible in parsed `mcp.servers` settings."])
    package_entries=merged.get("plugins",merged.get("plugin",[]))
    package_names=[]
    if isinstance(package_entries,list):
        for item in package_entries:
            if isinstance(item,str): package_names.append(item)
            elif isinstance(item,dict) and isinstance(item.get("package"),str): package_names.append(item["package"])
    elif isinstance(package_entries,str): package_names.append(package_entries)
    lines.append(f"- Configured plugin package entries: {len(package_names)}; local plugin files discovered: {len(plugins)}.")
    if package_names:
        lines.append("  - Plugin IDs: "+", ".join(f"`{name[:100]}`" for name in package_names[:25])+(" (additional entries omitted)" if len(package_names)>25 else ""))
    if plugins: lines.append("  - Local plugin filenames: "+", ".join(f"`{name}`" for name in plugins[:25]))
    agent_counts={kind:sum(1 for item in agents if item.get("classification")==kind) for kind in ("custom","built-in","overridden built-in")}
    lines.append(f"- Agents discovered: {len(agents)} ({agent_counts['custom']} custom, {agent_counts['built-in']} built-in, {agent_counts['overridden built-in']} overridden built-in); skills: {len(skills)}; custom commands: {len(commands)}; custom tool files: {len(custom_tools)}.")
    if custom_tools: lines.append("  - Custom tool file IDs: "+", ".join(f"`{safe_label(item['id'])}` ({safe_label(item['scope'])})" for item in custom_tools[:40])+ (" (additional entries omitted)" if len(custom_tools)>40 else ""))
    configured_commands=merged.get("commands",merged.get("command",{}))
    command_names=list(configured_commands) if isinstance(configured_commands,dict) else []
    command_names += commands
    if command_names: lines.append("  - Command IDs: "+", ".join(f"`{safe_label(name)}`" for name in sorted(set(command_names))[:40]))
    for agent in agents:
        edit_state=effective_agent_permission(agent,permissions,"edit"); shell_state=effective_agent_permission(agent,permissions,"shell")
        model=f"; model={safe_label(agent['model'])}" if agent.get("model") else ""
        lines.append(f"  - Agent `{safe_label(agent['id'])}` ({safe_label(agent.get('classification','custom'))}, {safe_label(agent['scope'])}, mode={safe_label(agent['mode'])}): description={'yes' if agent['description'] else 'no'}; edit={edit_state or 'unspecified'}, shell={shell_state or 'unspecified'} (combined visible rules; wildcard resource only){model}.")
    for skill in skills: lines.append(f"  - Skill `{safe_label(skill['id'])}` ({safe_label(skill['scope'])}): description={'yes' if skill['description'] else 'no'}.")
    lines.append("- Formatter: "+("present" if merged.get("formatter") else "not explicitly configured/false")+"; LSP: "+("present" if merged.get("lsp") else "not explicitly configured/false")+".")
    lines.append("- Project languages inferred from file extensions: "+(", ".join(f"{lang} ({n} files)" for lang,n in sorted(languages.items(),key=lambda x:-x[1])[:12]) if languages else "none detected in this folder")+". No source file contents were read.")
    if languages:
        lines.append("- Relevant local tooling found on PATH (presence only; commands were not run):")
        for language in sorted(languages):
            candidates=LANG_TOOLS.get(language,[])
            present=[name for name in candidates if shutil.which(name)]
            lines.append(f"  - {language}: "+(", ".join(f"`{name}`" for name in present) if present else "none of the checked common tools"))
    lines += ["", "## Runtime MCP check", ""]
    if not mcp_runtime["requested"]:
        lines.append("Not run. Pass `--check-mcp` to explicitly execute `opencode mcp list`; the resolved-config check above does not test server connectivity.")
    else:
        lines.append(f"Opt-in check: {mcp_runtime['status']}; return code: {mcp_runtime['returncode'] if mcp_runtime['returncode'] is not None else 'not available'}.")
        if mcp_runtime["servers"]:
            lines.extend(f"- `{safe_label(item['name'])}`: {item['status']}" for item in mcp_runtime["servers"])
        else:
            lines.append("No server statuses could be safely parsed; raw CLI output was intentionally discarded.")
    lines += ["", "## Release-pinned semantic comparison", ""]
    if semantic.get("status")=="completed":
        basis="the installed CLI's resolved `debug config`" if config_source=="installed CLI debug config" else "static config-file merge only; treat results as provisional"
        lines.append(f"Compared locally against the fetched OpenCode v{semantic.get('version','unknown')} migration text, using {basis}. {len(semantic.get('findings',[]))} concrete documented compatibility issue(s) found.")
        lines.append("Only source-backed legacy mappings or explicitly ignored fields are raised. Findings omit setting values and custom resource identifiers; full document bodies remain in memory and are not written to the report.")
    elif semantic.get("status")=="partial documentation":
        lines.append(f"The v{semantic.get('version','unknown')} documentation fetch was partial. Any findings below are limited to the fetched source text; absence of a finding is not evidence of compatibility.")
    else:
        lines.append(f"Semantic comparison was not run: {semantic.get('status','unknown')}. No source-backed v2 semantic conclusions can be made from this run.")
    lines += ["", "## Findings and recommended actions", ""]
    if not findings: lines.append("No high-confidence static findings were raised. This does not certify the setup or validate runtime connections.")
    else:
        for i,item in enumerate(findings,1):
            lines += [f"### {i}. [{item['severity']}] {item['title']}","",f"- Evidence: {item['evidence']}",f"- Why it matters: {item['impact']}",f"- Recommended next step: {item['action']}",f"- Official source: {item['source']}",""]
    lines += ["## Official source retrieval",""]
    lines.extend(f"- [{name}]({url}) — fetched" if name in docs else f"- [{name}]({url}) — unavailable: {doc_errors.get(name,'not checked')[:140]}" for name,url in base.DOCS.items())
    lines += [f"- [Config JSON Schema]({SCHEMA_URL}) — {'fetched for top-level key/type checks' if schema_ok else 'not available; no full schema validation performed'}"]
    lines += base.render_v2_docs_section(v2_docs)
    lines += ["", "### Additional official sources", "",f"Checked at: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} (retrieval time)."]
    if source_data:
        lines.append(f"- [OpenCode website]({base.OFFICIAL_SOURCES['OpenCode website']}) — {base.source_error(source_data['website'])}; navigation source, not version authority.")
        lines.append(f"- [Documentation inventory]({base.OFFICIAL_SOURCES['Docs inventory']}) — {base.source_error(source_data['docs_inventory'])}; {source_data['docs_inventory'].get('item_count',0)} indexed docs links found.")
        repo=source_data['github_repo']; lines.append(f"- [Official GitHub repository]({base.OFFICIAL_SOURCES['GitHub repository']}) — {base.source_error(repo)}"+(f"; `{repo['full_name']}`, default branch `{repo['default_branch']}`." if repo.get('ok') else ""))
        readme=source_data['github_readme']; lines.append(f"- [Repository README]({base.GITHUB_REPO_API}/readme) — {base.source_error(readme)}"+(f"; blob `{readme.get('sha')}`, {readme.get('size')} bytes; headings: "+"; ".join(f"`{x}`" for x in readme.get('headings',[])[:8])+"." if readme.get('ok') else ""))
        gh=source_data['github_releases']; lines.append(f"- [GitHub Releases API]({base.OFFICIAL_SOURCES['GitHub releases API']}) — {base.source_error(gh)}; {gh.get('records_read',0)} records from {gh.get('pages_read',0)} bounded page(s) ({'complete history reached' if gh.get('scan_complete') else 'bounded scan'}); latest v2 candidate `{(gh.get('latest_v2') or {}).get('version','unknown')}`.")
    else:
        lines.append("- Website, docs inventory, repository metadata, README, and release API: not checked (`--offline`).")
    lines += [f"- [Official Discord invite]({base.OFFICIAL_SOURCES['Discord invite']}) — manual/community link only; the audit does not join or read private channels.",f"- [X account @opencode]({base.OFFICIAL_SOURCES['X account']}) — manual announcement signal only; ownership is not independently verified and X is not an authoritative version feed.", "", "## Scope and safeguards", "", "- `opencode debug config` runs by default when a CLI is available to show resolved settings; it may load remote config supported by the installed build. Raw stdout/stderr are discarded and credential-like values are redacted before analysis. Use `--skip-debug-config` for static-only review.","- The optional `--check-mcp` launches `opencode mcp list`, which may start local MCP processes or contact remote endpoints; it is not run by default.", "- Secret values are never printed. The report may show non-secret identifiers such as model names, MCP server IDs, agent names, and permission decisions; review the report before sharing it.", "- The audit does not inspect OpenCode's private Desktop database, authentication store, active UI model, remote policy, inline config contents, or all environment variables.", "- A missing config field may be inherited from OpenCode defaults. Explicit setting detection is not the same as effective runtime behavior.", "- Every suggested change requires human approval and should be tested on a disposable project.", ""]
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir",default=".",help="Project directory or user config folder (read-only)")
    parser.add_argument("--output",help="Markdown output file; defaults to stdout")
    parser.add_argument("--offline",action="store_true",help="Skip all official web/schema checks")
    parser.add_argument("--global-only",action="store_true",help="Inspect user-level OpenCode config only; do not scan project files")
    parser.add_argument("--check-mcp",action="store_true",help="Opt in to `opencode mcp list`; may start local servers or contact remote endpoints")
    parser.add_argument("--cli",help="Path to the OpenCode CLI executable; defaults to OPENCODE_CLI or PATH lookup")
    parser.add_argument("--skip-debug-config",action="store_true",help="Do not invoke `opencode debug config`; use static file analysis only")
    args=parser.parse_args()
    report=create_report(Path(args.project_dir).expanduser().resolve(),args.offline,args.global_only,args.check_mcp,not args.skip_debug_config,args.cli)
    if args.output:
        target=Path(args.output).expanduser().resolve(); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(report,encoding="utf-8"); print(f"Report saved to {target}")
    else: print(report)
    return 0

if __name__=="__main__": raise SystemExit(main())
