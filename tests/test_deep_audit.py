import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import deep_audit as audit


class DeepAuditTests(unittest.TestCase):
    def test_current_and_deprecated_schema_fields_are_distinguished(self):
        schema={"properties":{
            "agents":{"type":"object"},"permissions":{"type":"array"},
            "plugins":{"type":"array"},"agent":{"deprecated":True},
            "permission":{"deprecated":True},"plugin":{"deprecated":True},
        }}
        records=[{"scope":"global","path":Path("opencode.json"),"data":{
            "agents":{},"permissions":[],"plugins":[],"agent":{},"permission":{},"plugin":[]
        }}]
        findings,ok=audit.top_schema_check(records,schema)
        self.assertTrue(ok)
        deprecated=[f["title"] for f in findings if f["code"]=="DEPRECATED_CONFIG_KEY"]
        self.assertEqual(len(deprecated),3)
        self.assertFalse(any(f["code"]=="UNKNOWN_CONFIG_KEY" for f in findings))

    def test_starter_config_uses_current_pluralized_fields(self):
        from audit_config import strip_jsonc
        cfg=json.loads(strip_jsonc((ROOT/"config/opencode.jsonc").read_text(encoding="utf-8")))
        self.assertIn("agents",cfg); self.assertIn("permissions",cfg)
        self.assertNotIn("agent",cfg); self.assertNotIn("permission",cfg)
        self.assertIn("snapshots",cfg); self.assertIn("update",cfg)

    def test_agent_map_reads_both_schema_generations(self):
        agents=audit.agent_map({"agent":{"old":{"mode":"subagent"}},"agents":{"new":{"mode":"primary"}}})
        self.assertEqual(set(agents),{"old","new"})
        normalized=audit.configured_agents({"agents":{"review":{"description":"Read-only review", "mode":"subagent", "model":"provider/model-x", "permissions":[{"action":"edit","resource":"*","effect":"deny"}]}}})
        self.assertEqual(normalized[0]["model"],"provider/model-x")
        self.assertEqual(audit.permission_decision(normalized[0],"edit"),"deny")

    def test_permissions_rules_and_legacy_map(self):
        self.assertEqual(audit.permission_decision({"permission":{"edit":"deny"}},"edit"),"deny")
        entry={"permissions":[
            {"action":"edit","resource":"*","effect":"deny"},
            {"action":"bash","resource":"*","effect":"ask"},
        ]}
        self.assertEqual(audit.permission_decision(entry,"edit"),"deny")
        self.assertEqual(audit.permission_decision(entry,"bash"),"ask")
        self.assertIsNone(audit.permission_decision(entry,"webfetch"))
        self.assertEqual(audit.permission_decision({"permission":{"*":"deny"}},"edit"),"deny")
        self.assertEqual(audit.permission_decision({"tools":{"edit":False,"bash":False}},"edit"),"deny")

    def test_integration_use_scope_change_is_flagged_without_leaking_resources(self):
        findings=audit.analyze([],{},[],[],[],[],Path("."),False)
        self.assertTrue(any(item["code"]=="PERMISSIONS_DEFAULTS" for item in findings))
        findings=audit.analyze([],{"policies":[{"action":"integration.use","resource":"*","effect":"deny"}]},[],[],[],[],Path("."),True)
        finding=next(item for item in findings if item["code"]=="INTEGRATION_POLICY_SCOPE_CHANGED")
        self.assertIn("mcp.use",finding["impact"])
        self.assertNotIn("resource `*`",finding["evidence"])

    def test_skills_arrays_combine_and_mcp_servers_replace_same_name(self):
        merged=audit.merge_dicts(
            {"skills":["./one"],"mcp":{"servers":{"docs":{"type":"remote","url":"https://old","headers":{"X":"old"}}}}},
            {"skills":["./two"],"mcp":{"servers":{"docs":{"type":"remote","url":"https://new"}}}},
        )
        self.assertEqual(merged["skills"],["./one","./two"])
        self.assertEqual(merged["mcp"]["servers"]["docs"],{"type":"remote","url":"https://new"})

    def test_compatibility_skill_paths_are_inventoried_without_scanning_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            project=Path(td)
            skills=project/".agents"/"skills"
            skills.mkdir(parents=True)
            (skills/"review.md").write_text("---\nname: Review\ndescription: Review code\n---\n",encoding="utf-8")
            (skills/".hidden").mkdir()
            (skills/".hidden"/"secret" ).mkdir()
            (skills/".hidden"/"secret"/"SKILL.md").write_text("---\ndescription: hidden\n---\n",encoding="utf-8")
            _,found,_,_,_=audit.inventory_files([],[("compat",skills)])
            self.assertEqual([item["id"] for item in found],["review"])

    def test_custom_tools_are_inventoried_and_builtin_override_is_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            tool_dir=root/"tools"; tool_dir.mkdir()
            (tool_dir/"bash.ts").write_text("// untrusted code must not be read",encoding="utf-8")
            (tool_dir/"lookup.js").write_text("// tool source",encoding="utf-8")
            _,_,_,_,tools=audit.inventory_files([("project",root)])
            self.assertEqual({item["id"] for item in tools},{"bash","lookup"})
            findings=audit.analyze([],{},[],[],[],[],root,True,custom_tools=tools)
            self.assertTrue(any(item["code"]=="CUSTOM_TOOL_CODE" for item in findings))
            collision=next(item for item in findings if item["code"]=="CUSTOM_TOOL_OVERRIDES_BUILTIN")
            self.assertIn("bash",collision["title"])

    def test_formatter_lsp_and_instructions_execution_risks_are_reported(self):
        merged={"formatter":{"custom":{"command":["fmt","$FILE"]}},
                "lsp":{"custom":{"command":["lsp-server"],"extensions":[".x"]}},
                "instructions":["AGENTS.md"]}
        findings=audit.analyze([],merged,[],[],[],[],Path("."),True)
        codes={item["code"] for item in findings}
        self.assertIn("FORMATTER_EXECUTION_ENABLED",codes)
        self.assertIn("CUSTOM_LSP_COMMAND",codes)
        self.assertIn("CUSTOM_INSTRUCTIONS",codes)

    def test_markdown_agent_frontmatter_parses_current_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            agent=Path(td)/"review.md"
            agent.write_text("---\ndescription: Read-only review\npermissions:\n  - action: edit\n    resource: \"*\"\n    effect: deny\n  - action: bash\n    resource: \"*\"\n    effect: deny\n---\n",encoding="utf-8")
            data=audit.parse_frontmatter(agent)
            normalized={"permissions":data.get("_permissions",[])}
            self.assertEqual(audit.permission_decision(normalized,"edit"),"deny")
            self.assertEqual(audit.permission_decision(normalized,"bash"),"deny")

    def test_safe_label_redacts_arbitrary_values(self):
        self.assertEqual(audit.safe_label("bash"),"bash")
        self.assertEqual(audit.safe_label("https://user:pass@example.org"),"[redacted]")

    def test_debug_config_parses_resolved_json_and_redacts_secrets(self):
        self.assertEqual(audit.parse_debug_config_output("{}"),{})
        text='Loaded config:\n'+json.dumps({"$schema":"https://opencode.ai/config.json","model":"provider/model","provider":{"provider":{"options":{"apiKey":"never-print-me"}}},"mcp":{"servers":{"docs":{"type":"remote","url":"https://example.test"}}}})
        parsed=audit.parse_debug_config_output(text)
        self.assertEqual(parsed["model"],"provider/model")
        self.assertEqual(parsed["provider"]["provider"]["options"]["apiKey"],"[redacted]")
        self.assertNotIn("never-print-me",json.dumps(parsed))

    def test_resolved_cli_config_suppresses_stale_schema_unknown_key_warning(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td); config_dir=home/".config"/"opencode"; config_dir.mkdir(parents=True)
            (config_dir/"opencode.json").write_text('{"future_feature": true}',encoding="utf-8")
            with patch.dict(os.environ,{"XDG_CONFIG_HOME":str(home/".config"),"HOME":str(home)},clear=False), \
                 patch.object(audit.base,"detect_version",return_value=("v2.3.4","test")), \
                 patch.object(audit,"debug_config",return_value={"status":"resolved config loaded from installed CLI","config":{"future_feature":True,"model":"provider/model"},"version":"v2.3.4"}), \
                 patch.object(audit,"source_roots",return_value=[]), \
                 patch.object(audit,"inventory_files",return_value=([],[],[],[],[])), \
                 patch.object(audit,"skill_roots",return_value=[]), \
                 patch.object(audit,"configured_skill_roots",return_value=([],0)), \
                 patch.object(audit,"fetch_schema",return_value=({"properties":{"model":{"type":"string"}}},None)), \
                 patch.object(audit.base,"fetch_text",return_value=("v2.3.2",None)), \
                 patch.object(audit.base,"collect_official_source_data",return_value={"website":{"ok":True},"docs_inventory":{"ok":True,"item_count":69},"github_repo":{"ok":True,"full_name":"anomalyco/opencode","default_branch":"dev"},"github_readme":{"ok":True,"sha":"abc","size":100,"headings":["OpenCode"]},"github_releases":{"ok":True,"latest_v2":{"version":"v2.3.2","url":"https://github.com/anomalyco/opencode/releases/tag/v2.3.2","published_at":"2026-11-18T01:35:12Z"},"records_read":20,"pages_read":1,"scan_complete":True}}):
                report=audit.create_report(home,global_only=True)
            self.assertIn("installed CLI debug config",report)
            self.assertIn("Additional official sources",report)
            self.assertIn("Latest stable v2 candidate from GitHub Releases: **v2.3.2**",report)
            self.assertIn("v2 release corroboration: **confirmed by changelog and complete Releases scan**",report)
            self.assertNotIn("Config key not found in current official schema: future_feature",report)
            self.assertIn("provider/model",report)

    def test_debug_config_uses_selected_cli_and_falls_back_without_raw_error(self):
        version=audit.subprocess.CompletedProcess(args=[],returncode=0,stdout="v2.3.4\n",stderr="")
        error=audit.subprocess.CompletedProcess(args=[],returncode=1,stdout="",stderr="token=do-not-show")
        with patch.object(audit.subprocess,"run",side_effect=[version,error]) as run:
            result=audit.debug_config(Path("."),cli_path="C:/Program Files/OpenCode/opencode.exe")
        self.assertEqual(result["version"],"v2.3.4")
        self.assertIsNone(result["config"])
        self.assertNotIn("do-not-show",json.dumps(result))
        self.assertEqual(run.call_args_list[1].args[0], ["C:/Program Files/OpenCode/opencode.exe","debug","config"])

    def test_runtime_check_is_opt_in_and_discards_raw_output(self):
        completed=audit.subprocess.CompletedProcess(
            args=["opencode","mcp","list"],returncode=0,
            stdout="context7 connected\nprivate-token needs authentication\n",stderr="secret=never-print",
        )
        with patch.object(audit.shutil,"which",return_value="/usr/bin/opencode"), patch.object(audit.subprocess,"run",return_value=completed):
            result=audit.check_mcp_runtime()
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["servers"][0],{"name":"context7","status":"connected"})
        self.assertNotIn("secret",json.dumps(result))

    def test_readonly_report_does_not_print_skill_urls_or_permission_resources(self):
        with tempfile.TemporaryDirectory() as td:
            home=Path(td)
            config_dir=home/".config"/"opencode"
            config_dir.mkdir(parents=True)
            cfg={
                "skills":["https://user:password@example.org/catalog/"],
                "permissions":[{"action":"bash","resource":"password=VERY_SECRET","effect":"deny"}],
            }
            (config_dir/"opencode.json").write_text(json.dumps(cfg),encoding="utf-8")
            env={"XDG_CONFIG_HOME":str(home/".config"),"HOME":str(home)}
            with patch.dict(os.environ,env,clear=False):
                os.environ.pop("OPENCODE_CONFIG",None)
                report=audit.create_report(home,offline=True,global_only=True)
            self.assertIn("Remote skill catalogs are configured",report)
            self.assertNotIn("VERY_SECRET",report)
            self.assertNotIn("password@example.org",report)
            self.assertIn("not run",report.casefold())
            self.assertIn("installed CLI `debug config` is the primary",report)


if __name__=="__main__":
    unittest.main()
