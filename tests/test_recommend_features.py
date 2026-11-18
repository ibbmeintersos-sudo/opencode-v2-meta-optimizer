import importlib.util
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

    def test_report_discloses_missing_cli_and_keeps_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            config = project / "opencode.jsonc"
            config.write_text('{"$schema":"https://opencode.ai/config.json"}', encoding="utf-8")
            before = config.read_bytes()
            with patch.object(module, "detect_version", return_value=("Not detected", "test: no CLI")):
                report = module.build_report(project, offline=True)
            self.assertIn("Not detected", report)
            self.assertIn("No configuration was changed", report)
            self.assertIn("offline mode", report)
            self.assertEqual(config.read_bytes(), before)

    def test_no_config_means_unknown_not_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "detect_version", return_value=("v2.3.2", "test")), \
                 patch.object(module, "DOCS", {"Agents": "https://opencode.ai/docs/agents/"}), \
                 patch.object(module, "fetch_text", return_value=("official agents docs", None)):
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
                 patch.object(module, "fetch_text", return_value=("v2.3.2 worktree", None)):
                report = module.build_report(Path(tmp))
            self.assertIn("not a config flag to detect", report)
            self.assertIn("detected version v2.3.2", report)

    def test_fetch_uses_only_fixed_official_sources(self):
        self.assertTrue(all(url.startswith("https://opencode.ai/") for url in module.DOCS.values()))


if __name__ == "__main__":
    unittest.main()
