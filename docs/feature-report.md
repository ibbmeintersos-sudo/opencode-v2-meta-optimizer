# Feature recommendation report

Run this from a project folder on the computer where OpenCode is installed:

```bash
python3 /path/to/opencode-v2-meta-optimizer/scripts/recommend_features.py \
  --project-dir /path/to/your/project \
  --output opencode-feature-report.md
```

The script calls `opencode --version` (read-only), reads the documented `~/.config/opencode/` location, `XDG_CONFIG_HOME`, the Windows `%APPDATA%\opencode` fallback, `OPENCODE_CONFIG`, and the specified project's `opencode.json`/`opencode.jsonc` (including parents up to the Git root). It checks a fixed official source catalog: the OpenCode homepage, docs inventory (`/docs/llms.txt`), documentation pages and changelog, public GitHub repository metadata and README, and the GitHub Releases API. It filters stable `v2.x.y` release tags, excludes drafts/prereleases/component tags, sorts candidates numerically, and compares the candidate with the changelog’s first v2 label. It then reads the tagged GitHub Contents API directories for the 16 core user-facing docs topics and `specs/v2`, and fetches those exact tag-pinned Markdown/MDX files. The report shows counts, titles, and links to the exact release tag; no files are cached locally. This is a focused docs set, not every page in the website repository. Disagreements are reported as unconfirmed. Discord is a manual invite link and is not joined or crawled; X is a user-provided announcement link, not a canonical version source. It does not upload local config contents, print secret values, execute OpenCode agent tasks, install/update packages, or change configuration. The automatic release-pinned documentation fetch uses the public GitHub Contents API and `raw.githubusercontent.com`; these requests also disclose ordinary connection metadata such as IP address and user agent to GitHub.

Use `--offline` to skip website and GitHub documentation retrieval. In offline mode the report cannot make live, source-grounded recommendations.

If no config file is found or parsed, feature statuses are reported as unknown; they do not mean a capability is missing or disabled. Desktop Worktrees are treated as a documented workflow rather than a config flag. The report compares configuration signals with a curated feature catalog based on official docs. It is not a machine-verifiable inventory of every Desktop capability. If the CLI is absent from PATH, the installed Desktop version cannot be confirmed. The upstream latest-v2 result does not prove compatibility with the installed CLI/Desktop version or any particular feature. The report never applies recommendations.
