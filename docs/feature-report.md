# Feature recommendation report

Run this from a project folder on the computer where OpenCode is installed:

```bash
python3 /path/to/opencode-v2-meta-optimizer/scripts/recommend_features.py \
  --project-dir /path/to/your/project \
  --output opencode-feature-report.md
```

The script calls `opencode --version` (read-only), reads the documented `~/.config/opencode/` location, `XDG_CONFIG_HOME`, the Windows `%APPDATA%\opencode` fallback, `OPENCODE_CONFIG`, and the specified project's `opencode.json`/`opencode.jsonc` (including parents up to the Git root). It fetches a fixed set of pages on `opencode.ai`. It does not upload local config contents, print secret values, execute OpenCode agent tasks, install/update packages, or change configuration. HTTP requests disclose ordinary connection metadata such as IP address and user agent to the official website.

Use `--offline` to skip documentation retrieval. In offline mode the report cannot make live, source-grounded recommendations.

If no config file is found or parsed, feature statuses are reported as unknown; they do not mean a capability is missing or disabled. Desktop Worktrees are treated as a documented workflow rather than a config flag. The report compares configuration signals with a curated feature catalog based on official docs. It is not a machine-verifiable inventory of every Desktop capability. If the CLI is absent from PATH, the installed Desktop version cannot be confirmed. Feature introduction versions are not automatically derived from release prose, so verify the changelog for the detected version before applying any recommendation. The report never applies recommendations.
