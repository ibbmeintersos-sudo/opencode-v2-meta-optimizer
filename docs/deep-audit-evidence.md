# Official OpenCode facts used by the deep auditor

This note records the official-source behavior the audit rules are based on. OpenCode documentation and the live schema can evolve independently, so the tool reports discrepancies and deprecated fields rather than assuming every example is current for every installed build.

## Configuration and precedence

The official configuration page documents JSON/JSONC and the general merge order: remote defaults, global `~/.config/opencode/opencode.json`, `OPENCODE_CONFIG`, project `opencode.json`, `.opencode` resources, inline `OPENCODE_CONFIG_CONTENT`, then managed policy. `OPENCODE_CONFIG_DIR` adds agent/command/plugin resources. Managed config paths include `%ProgramData%\opencode` on Windows, `/etc/opencode` on Linux, and `/Library/Application Support/opencode` on macOS. The auditor does not call `opencode debug config`, because resolved output could expose sensitive values.

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
