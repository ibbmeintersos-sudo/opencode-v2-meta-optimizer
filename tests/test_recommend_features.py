import base64
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("recommend_features", ROOT / "scripts" / "recommend_features.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FeatureReportTests(unittest.TestCase):
    def test_latest_version_from_changelog(self):
        self.assertEqual(module.latest_changelog_version("OpenCode updates v2.3.0 and v2.2.7"), "v2.3.0")
        self.assertIsNone(module.latest_changelog_version("no releases"))

    def test_latest_v2_release_filters_semver_stable_tags(self):
        releases=[
            {"tag_name":"v1.99.0","draft":False,"prerelease":False},
            {"tag_name":"process-daemon-v9.0.0","draft":False,"prerelease":False},
            {"tag_name":"v2.10.0","draft":False,"prerelease":False,"html_url":"https://github.com/anomalyco/opencode/releases/tag/v2.10.0"},
            {"tag_name":"v2.11.0-beta.1","draft":False,"prerelease":True},
            {"tag_name":"v2.9.9","draft":True,"prerelease":False},
        ]
        latest=module.select_latest_v2_release(releases)
        self.assertEqual(latest["version"],"v2.10.0")
        self.assertEqual(module.latest_changelog_v2_version("v3.0.0 notes, then v2.3.2 and v2.2.9"),"v2.3.2")

    def test_official_source_collector_reads_pinned_readme_metadata_and_release_api(self):
        encoded=base64.b64encode(b"# OpenCode\n## Install\n## Desktop\n").decode()
        repo={"full_name":"anomalyco/opencode","default_branch":"dev","html_url":"https://github.com/anomalyco/opencode","updated_at":"2026-11-19T00:00:00Z"}
        readme={"encoding":"base64","content":encoded,"sha":"abc123","size":35}
        releases=[{"tag_name":"v2.3.2","draft":False,"prerelease":False,"html_url":"https://github.com/anomalyco/opencode/releases/tag/v2.3.2","published_at":"2026-11-18T01:35:12Z"},{"tag_name":"v1.99.0","draft":False,"prerelease":False}]
        with patch.object(module,"fetch_text",side_effect=[("home",None),("https://opencode.ai/docs/config/\n",None)]), patch.object(module,"fetch_json",side_effect=[(repo,None,{}),(readme,None,{}),(releases,None,{})]):
            sources=module.collect_official_source_data()
        self.assertTrue(sources["github_repo"]["ok"])
        self.assertEqual(sources["github_readme"]["sha"],"abc123")
        self.assertEqual(sources["github_releases"]["latest_v2"]["version"],"v2.3.2")
        self.assertIn("not crawled",sources["discord"]["status"])
        self.assertIn("not independently verified",sources["x"]["status"])

    def test_v2_docs_fetches_core_docs_and_specs_from_latest_release_tag(self):
        docs=[
            {"path":"services/site/src/docs/content/config.mdx","name":"config.mdx","type":"file"},
            {"path":"services/site/src/docs/content/migrate-v1.mdx","name":"migrate-v1.mdx","type":"file"},
            {"path":"services/site/src/docs/content/irrelevant.mdx","name":"irrelevant.mdx","type":"file"},
        ]
        specs=[{"path":"specs/v2/tools.md","name":"tools.md","type":"file"}]
        calls=[]
        def api(url,timeout=15.0):
            calls.append(url)
            return (docs if "services/site/src/docs/content" in url else specs,None,{})
        raw_calls=[]
        def raw(url):
            raw_calls.append(url)
            return ("# Config Guide\n## Permissions\nBody",None)
        with patch.object(module,"fetch_json",side_effect=api), patch.object(module,"fetch_raw_text",side_effect=raw):
            result=module.collect_v2_documentation({"latest_v2":{"version":"v2.3.2"}})
        self.assertEqual(result["status"],"complete")
        self.assertEqual(result["version"],"v2.3.2")
        self.assertEqual(result["downloaded_count"],3)
        self.assertEqual(result["manifest_count"],3)
        self.assertEqual(result["spec_count"],1)
        self.assertEqual(result["files"][0]["content"],"# Config Guide\n## Permissions\nBody")
        self.assertNotIn("Body", "\n".join(module.render_v2_docs_section(result)))
        self.assertTrue(all("/v2.3.2/" in url for url in raw_calls))
        self.assertEqual(len(calls),2)
        self.assertTrue(all("ref=v2.3.2" in url for url in calls))
        self.assertNotIn("irrelevant.mdx", " ".join(raw_calls))

    def test_semantic_comparison_uses_tagged_evidence_for_ignored_and_inverted_fields(self):
        migration=("The V1 config format remains supported. When both forms set the same canonical value, "
                   "a valid native V2 value takes precedence. V2 has no native `tail_turns` or `prune` field; "
                   "both legacy fields are ignored with a warning. V2 groups servers under `mcp.servers`, "
                   "replaces `enabled` with the inverse `disabled`, and separates timeout purposes.")
        docs={"status":"complete","complete":True,"version":"v2.3.2","files":[
            {"path":"services/site/src/docs/content/migrate-v1.mdx","url":"https://github.com/anomalyco/opencode/blob/v2.3.2/services/site/src/docs/content/migrate-v1.mdx","content":migration}
        ]}
        config={"agent":{"reviewer":{"prompt":"private prompt"}},"agents":{"reviewer":{"system":"private prompt"}},
                "compaction":{"tail_turns":8},"mcp":{"servers":{"docs":{"enabled":False,"timeout":30000}}}}
        result=module.analyze_v2_configuration(config,docs,"installed CLI debug config")
        codes={item["code"] for item in result["findings"]}
        self.assertEqual(result["status"],"completed")
        self.assertIn("V2_COMPACTION_LEGACY_IGNORED",codes)
        self.assertIn("V2_MCP_ENABLED_INVERSION",codes)
        self.assertIn("V2_MCP_TIMEOUT_SHAPE",codes)
        self.assertIn("V2_LEGACY_NATIVE_CONFLICT_AGENT",codes)
        rendered=json.dumps(result)
        self.assertNotIn("private prompt",rendered)
        self.assertNotIn("reviewer",rendered)
        self.assertIn("v2.3.2",rendered)

    def test_semantic_comparison_is_quiet_without_matching_field_or_documentation(self):
        evidence="V2 has no native `tail_turns` or `prune` field; both legacy fields are ignored with a warning."
        docs={"status":"complete","complete":True,"version":"v2.3.2","files":[{"path":"migrate-v1.mdx","content":evidence,"url":"https://github.com/anomalyco/opencode/blob/v2.3.2/migrate-v1.mdx"}]}
        clean=module.analyze_v2_configuration({"compaction":{"auto":True}},docs)
        self.assertEqual(clean["findings"],[])
        unavailable=module.analyze_v2_configuration({"compaction":{"tail_turns":8}},{"status":"unavailable","files":[]})
        self.assertEqual(unavailable["status"],"release documentation unavailable")
        self.assertEqual(unavailable["findings"],[])

    def test_semantic_comparison_marks_partial_fetch_and_only_flags_actual_overlap(self):
        docs={"status":"partial","complete":False,"version":"v2.3.2","files":[{"path":"services/site/src/docs/content/migrate-v1.mdx","content":"When both forms set the same canonical value, a valid native V2 value takes precedence.","url":"https://github.com/anomalyco/opencode/blob/v2.3.2/services/site/src/docs/content/migrate-v1.mdx"}]}
        no_overlap=module.analyze_v2_configuration({"agent":{"one":{}},"agents":{"two":{}}},docs)
        self.assertEqual(no_overlap["status"],"partial documentation")
        self.assertEqual(no_overlap["findings"],[])
        no_permission_overlap=module.analyze_v2_configuration({"permission":{"bash":"ask"},"permissions":[{"action":"edit","resource":"*","effect":"deny"}]},docs)
        self.assertEqual(no_permission_overlap["findings"],[])
        permission_overlap=module.analyze_v2_configuration({"permission":{"bash":"ask"},"permissions":[{"action":"bash","resource":"*","effect":"deny"}]},docs)
        self.assertEqual([item["code"] for item in permission_overlap["findings"]],["V2_LEGACY_NATIVE_CONFLICT_PERMISSION"])
        alias_docs={**docs,"files":[{**docs["files"][0],"content":docs["files"][0]["content"]+" `bash` is now `shell`."}]}
        alias_overlap=module.analyze_v2_configuration({"permission":{"bash":{"git push *":"ask"}},"permissions":[{"action":"shell","resource":"git push *","effect":"deny"}]},alias_docs)
        self.assertEqual([item["code"] for item in alias_overlap["findings"]],["V2_LEGACY_NATIVE_CONFLICT_PERMISSION"])

    def test_v2_docs_fail_closed_for_invalid_or_truncated_manifest(self):
        no_release=module.collect_v2_documentation({"latest_v2":{"version":"v3.1.0"}})
        self.assertEqual(no_release["status"],"unavailable")
        with patch.object(module,"fetch_json",return_value=(None,"HTTP 403",{})):
            failed=module.collect_v2_documentation({"latest_v2":{"version":"v2.3.2"}})
        self.assertEqual(failed["status"],"unavailable")
        self.assertEqual(failed["downloaded_count"],0)

    def test_report_discloses_missing_cli_and_keeps_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            config = project / "opencode.jsonc"
            config.write_text('{"$schema":"https://opencode.ai/config.json"}', encoding="utf-8")
            before = config.read_bytes()
            with patch.object(module, "detect_version", return_value=("Not detected", "test: no CLI")), patch.object(module,"collect_official_source_data",return_value={}):
                report = module.build_report(project, offline=True)
            self.assertIn("Not detected", report)
            self.assertIn("No configuration was changed", report)
            self.assertIn("offline mode", report)
            self.assertEqual(config.read_bytes(), before)

    def test_no_config_means_unknown_not_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "detect_version", return_value=("v2.3.2", "test")), \
                 patch.object(module, "DOCS", {"Agents": "https://opencode.ai/docs/agents/"}), \
                 patch.object(module, "fetch_text", return_value=("official agents docs", None)), patch.object(module,"collect_official_source_data",return_value={"website":{"ok":False},"docs_inventory":{"ok":False},"github_repo":{"ok":False},"github_readme":{"ok":False},"github_releases":{"ok":False,"latest_v2":None,"records_read":0,"pages_read":0},"discord":{"status":"not checked","url":"https://opencode.ai/discord"},"x":{"status":"not checked","url":"https://x.com/opencode"}}), patch.object(module,"collect_v2_documentation",return_value={"status":"unavailable","error":"no candidate","files":[],"version":None,"selected_count":0,"downloaded_count":0,"spec_count":0,"docs_count":0}):
                report = module.build_report(Path(tmp))
            self.assertIn("No configuration file was parsed", report)
            self.assertIn("Unknown - no local config was parsed", report)
            self.assertIn("Cannot make a configuration-specific recommendation", report)

    def test_project_and_documented_home_config_paths_are_searched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            project = root / "repo"
            (home / ".config" / "opencode").mkdir(parents=True)
            (home / ".config" / "opencode" / "opencode.json").write_text("{}", encoding="utf-8")
            project.mkdir()
            (project / "opencode.jsonc").write_text("{}", encoding="utf-8")
            with patch("pathlib.Path.home", return_value=home), patch.dict(os.environ, {"OPENCODE_CONFIG": "", "XDG_CONFIG_HOME": ""}, clear=False):
                found = module.config_candidates(project)
            self.assertIn(home / ".config" / "opencode" / "opencode.json", found)
            self.assertIn(project / "opencode.jsonc", found)

    def test_worktree_is_reported_as_a_capability_not_config_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "detect_version", return_value=("v2.3.2", "test")), \
                 patch.object(module, "DOCS", {"Changelog": "https://opencode.ai/changelog"}), \
                 patch.object(module, "fetch_text", return_value=("v2.3.2 worktree", None)), patch.object(module,"collect_official_source_data",return_value={"website":{"ok":False},"docs_inventory":{"ok":False},"github_repo":{"ok":False},"github_readme":{"ok":False},"github_releases":{"ok":False,"latest_v2":None,"records_read":0,"pages_read":0},"discord":{"status":"manual","url":"https://opencode.ai/discord"},"x":{"status":"manual","url":"https://x.com/opencode"}}):
                report = module.build_report(Path(tmp))
            self.assertIn("not a config flag to detect", report)
            self.assertIn("detected version v2.3.2", report)

    def test_fetch_uses_only_fixed_official_sources(self):
        self.assertTrue(all(url.startswith("https://opencode.ai/") for url in module.DOCS.values()))
        self.assertTrue(all(url.startswith("https://") for url in module.OFFICIAL_SOURCES.values()))
        self.assertEqual(module.GITHUB_REPO_API,"https://api.github.com/repos/anomalyco/opencode")

    def test_report_marks_release_disagreement_not_as_confirmed_latest(self):
        source_data={"website":{"ok":True},"docs_inventory":{"ok":True,"item_count":160},"github_repo":{"ok":True,"full_name":"anomalyco/opencode","default_branch":"dev"},"github_readme":{"ok":True,"sha":"abc","size":20,"headings":["OpenCode","Install"]},"github_releases":{"ok":True,"latest_v2":{"version":"v2.3.2","url":"https://github.com/anomalyco/opencode/releases/tag/v2.3.2","published_at":"2026-11-18T01:35:12Z"},"records_read":100,"pages_read":1,"scan_complete":True},"discord":{"status":"manual link","url":"https://opencode.ai/discord"},"x":{"status":"not crawled","url":"https://x.com/opencode"}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(module,"detect_version",return_value=("v2.3.1","test")), patch.object(module,"DOCS",{"Changelog":"https://opencode.ai/changelog"}), patch.object(module,"fetch_text",return_value=("v2.3.0 v2.2.7",None)), patch.object(module,"collect_official_source_data",return_value=source_data), patch.object(module,"collect_v2_documentation",return_value={"status":"partial","version":"v2.3.2","files":[],"selected_count":4,"downloaded_count":3,"spec_count":1,"docs_count":2,"failed_count":1}):
            report=module.build_report(Path(tmp))
        self.assertIn("Latest stable v2 candidate",report)
        self.assertIn("v2.3.2",report)
        self.assertIn("not corroborated / sources disagree",report)
        self.assertIn("does not match the changelog's first v2 label `v2.3.0`",report)


if __name__ == "__main__":
    unittest.main()
