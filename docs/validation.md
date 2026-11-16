# Validation scope

`python3 scripts/audit_config.py <path>` performs dependency-free JSON/JSONC parsing and limited static checks. It is not a full JSONC standard parser, does not fetch or validate the official JSON Schema, and does not launch OpenCode or contact MCP servers. Its output must not be interpreted as proof that a config is supported by a particular OpenCode build.

For version-specific verification, consult the official [configuration](https://opencode.ai/docs/config/), [agents](https://opencode.ai/docs/agents/), [skills](https://opencode.ai/docs/skills/), [MCP](https://opencode.ai/docs/mcp-servers/), [tools](https://opencode.ai/docs/tools/), and [plugins](https://opencode.ai/docs/plugins/) documentation. Check the installed version and resolved config using that version's documented commands.
