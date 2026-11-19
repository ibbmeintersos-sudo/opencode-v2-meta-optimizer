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
                 patch.object(module, "fetch_text", return_value=("official agents docs", None)), patch.object(module,"collect_official_source_data",return_value={"website":{"ok":False},"docs_inventory":{"ok":False},"github_repo":{"ok":False},"github_readme":{"ok":False},"github_releases":{"ok":False,"latest_v2":None,"records_read":0,"pages_read":0},"discord":{"status":"not checked","url":"https://opencode.ai/discord"},"x":{"status":"not checked","url":"https://x.com/opencode"}}):
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
        with tempfile.TemporaryDirectory() as tmp, patch.object(module,"detect_version",return_value=("v2.3.1","test")), patch.object(module,"DOCS",{"Changelog":"https://opencode.ai/changelog"}), patch.object(module,"fetch_text",return_value=("v2.3.0 v2.2.7",None)), patch.object(module,"collect_official_source_data",return_value=source_data):
            report=module.build_report(Path(tmp))
        self.assertIn("Latest stable v2 candidate",report)
        self.assertIn("v2.3.2",report)
        self.assertIn("not corroborated / sources disagree",report)
        self.assertIn("does not match the changelog's first v2 label `v2.3.0`",report)


if __name__ == "__main__":
    unittest.main()
