import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_config", ROOT / "scripts" / "audit_config.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class JsoncTests(unittest.TestCase):
    def test_comments_and_trailing_comma(self):
        text = '{\n // comment\n "name": "ok", /* inline */ "items": [1, 2,],\n}'
        self.assertEqual(json.loads(module.strip_jsonc(text)), {"name": "ok", "items": [1, 2]})

    def test_comment_markers_inside_strings_are_preserved(self):
        text = '{"url":"https://example.com/a//b", "value":"/*literal*/"}'
        self.assertEqual(json.loads(module.strip_jsonc(text)), {"url": "https://example.com/a//b", "value": "/*literal*/"})

    def test_unterminated_block_comment_fails(self):
        with self.assertRaises(ValueError):
            module.strip_jsonc('{"x": 1 /* not closed')

    def test_audit_project_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "opencode.jsonc"
            path.write_text('{"$schema":"https://opencode.ai/config.json", "agent": {"reader": {"mode":"subagent", "permission":{"edit":"deny"}}}}', encoding="utf-8")
            errors, _ = module.audit(path)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
