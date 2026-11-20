# Official OpenCode facts used by the deep auditor

This note records the official-source behavior the audit rules are based on. OpenCode documentation and the live schema can evolve independently, so the tool reports discrepancies and deprecated fields rather than assuming every example is current for every installed build.

## Configuration and precedence

The official configuration page documents JSON/JSONC and the general merge order: remote defaults, global `~/.config/opencode/opencode.json`, `OPENCODE_CONFIG`, project `opencode.json`, `.opencode` resources, inline `OPENCODE_CONFIG_CONTENT`, then managed policy. `OPENCODE_CONFIG_DIR` adds agent/command/plugin resources. Managed config paths include `%ProgramData%\opencode` on Windows, `/etc/opencode` on Linux, and `/Library/Application Support/opencode` on macOS. The auditor runs the installed CLI’s documented `opencode debug config` command when available, parses it locally, redacts credential-like fields in memory, and discards raw output.

Source: <https://opencode.ai/docs/config/>

## Current schema and legacy field names

The live schema at <https://opencode.ai/config.json> includes current plural forms such as `agents`, `permissions`, `snapshots`, `plugins`, and `providers`, while also retaining legacy singular forms such as `agent`, `permission`, `snapshot`, `plugin`, and `provider` with a `deprecated` marker. `small_model`, `autoupdate`, `tools`, `command`, and other older fields may likewise be marked deprecated. Agent documentation still contains examples of the older singular `agent` and `permission` forms. Accordingly, the auditor accepts both structures for inventory and flags fields only when the live schema marks them deprecated; it does not label them invalid merely because the docs examples differ.

The schema's current tool permission rules use ordered records with `action`, `resource`, and `effect`; agent definitions can carry `permissions`. The schema and docs have changed over time, so permission resolution in this utility is a static approximation, not a reproduction of OpenCode runtime policy evaluation.

The official CLI reference lists `opencode debug config` as “Show the resolved configuration.” This is why the audit prefers that output from the selected installed CLI over interpreting the live JSON Schema as the sole authority. The schema remains useful for editor validation but can be newer or older than the installed build.

Sources:
- <https://opencode.ai/docs/cli/commands/>
- <https://opencode.ai/config.json>
- <https://opencode.ai/docs/agents/>
- <https://opencode.ai/docs/tools/>
- <https://opencode.ai/docs/custom-tools/>
- <https://opencode.ai/docs/formatters/>
- <https://opencode.ai/docs/lsp/>
- <https://opencode.ai/changelog>

The changelog also describes a policy-scope change: `integration.use` now governs integration accounts only, while `mcp.use`, `skill.use`, and `plugin.use` govern MCP servers, Skills, and Plugins. Older resource prefixes may be converted automatically, but a broad `integration.use` rule no longer implies control of those other resource classes. The auditor flags broad/legacy-prefix cases as a review prompt; it does not rewrite rules or validate newer action names against a potentially lagging schema snapshot.

## Agents, tools, MCP, Skills, and formatters

- OpenCode's tools documentation says tools are enabled by default; permissions can allow, deny, or ask. The last matching rule matters, so a broad later allow can override a narrower denial. MCP tool permission patterns can target normalized `<server>_<tool>` names.
- V2 MCP server configuration uses `mcp.servers`. Direct server names under `mcp` are documented as deprecated compatibility form. Remote MCP OAuth is on by default; local commands and remote endpoints are user-configured; `disabled: true` prevents connection; Code Mode defaults to true. `opencode mcp list` can show runtime connection state, but active checks are opt-in because they can start processes or contact remote services.
- Skills may be Markdown files at a source root or nested `SKILL.md` directories. Official discovery locations include `.opencode/skills`, `.claude/skills`, and `.agents/skills` at project/global scopes. Skill IDs are path-derived and case-sensitive; duplicate IDs resolve by source precedence; descriptions are required for model discovery. Configured external URL catalogs are not fetched by this local auditor.
- Formatters are disabled by default unless configured; they execute commands after edits. LSP availability and runtime health depend on installed local servers. This auditor checks static declarations and executable presence only, never launches formatters or LSPs.

Sources:
- <https://opencode.ai/docs/mcp-servers/>
- <https://opencode.ai/docs/skills/>
- <https://opencode.ai/docs/formatters/>
- <https://opencode.ai/docs/lsp/>
- <https://opencode.ai/changelog>

## Official source coverage and v2 release checks

The collector checks fixed public HTTPS endpoints: `opencode.ai`, the docs inventory at `/docs/llms.txt`, the official changelog, GitHub repository metadata and README API, and the repository Releases API. It records source status and retrieval time, uses bounded response bodies, and requires no GitHub token. README ingestion is limited to summary headings and its blob SHA; the full README is not copied into the report.

Latest stable v2 is selected from GitHub Releases records whose tags match `v2.<minor>.<patch>`, with `draft` and `prerelease` false, sorted numerically. The candidate is corroborated against the first v2 release label detected on the changelog. If the API and changelog disagree or either is unavailable, the report labels the latest-v2 claim unconfirmed and does not use `/releases/latest`, component tags, Discord, or X as definitive sources. Release publication time and local retrieval time are reported separately.

The official Discord shortcut is shown as a manual community link only; the auditor does not join, log in, or read private channels. `x.com/opencode` is shown as a user-provided announcement link; ownership is not independently verified and X is not scraped or treated as the canonical release source.

Sources: <https://opencode.ai/> · <https://opencode.ai/docs/llms.txt> · <https://opencode.ai/changelog> · <https://github.com/anomalyco/opencode> · <https://github.com/anomalyco/opencode/releases> · <https://api.github.com/repos/anomalyco/opencode/releases?per_page=20&page=1> · <https://opencode.ai/discord> · <https://x.com/opencode>

## Release-pinned v2 documentation retrieval

After the stable-v2 candidate is selected from the public Releases API, the audit requests only two directory listings at that exact tag: `services/site/src/docs/content` and `specs/v2`. It accepts an explicit allowlist of core documentation topics (agents, commands, compaction, config, formatters, instructions, MCP, migration, models, permissions, plugins, policies, providers, skills, snapshots, and tools), plus Markdown/MDX files directly under `specs/v2`. It then retrieves those files from `raw.githubusercontent.com/anomalyco/opencode/<tag>/...`. It does not use the moving `dev` branch for this release-specific set.

Safety bounds: public unauthenticated HTTPS only, fixed repository/paths, semver-validated v2 tag, at most 40 files, 250 KB per file, eight concurrent raw-file requests. Bodies remain in memory; only titles, links, counts, and retrieval status are included in the Markdown report. Offline mode skips these requests. Incomplete manifests or individual file failures produce an unavailable/partial status. This is a focused core-doc set, not a claim that every web page in the repository was downloaded.

Sources: <https://github.com/anomalyco/opencode> · <https://github.com/anomalyco/opencode/tree/v2.3.2/services/site/src/docs/content> · <https://github.com/anomalyco/opencode/tree/v2.3.2/specs/v2> · <https://opencode.ai/docs/migrate-v1/>
