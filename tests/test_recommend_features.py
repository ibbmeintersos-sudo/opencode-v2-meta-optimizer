import importlib.util
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

    def test_recommendation_not_made_when_source_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "detect_version", return_value=("v2.0.0", "test")):
                report = module.build_report(Path(tmp), offline=True)
            self.assertNotIn("### Agent-specific least-privilege permissions", report)

    def test_fetch_uses_only_fixed_official_sources(self):
        self.assertTrue(all(url.startswith("https://opencode.ai/") for url in module.DOCS.values()))


if __name__ == "__main__":
    unittest.main()
