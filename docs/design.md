# Design notes

This repository is a starter kit for one local OpenCode user. It provides a read-only configuration audit workflow, focused agents, and an offline syntax checker. It does not scan the host automatically, collect telemetry, benchmark model quality, install plugins, or apply repairs.

## Configuration principles

- Keep the main config generic and avoid guessing a provider/model ID.
- Prefer per-agent permissions for specialized, read-only work.
- Require approval for shell commands and edits in the starter config.
- Add MCP servers only after reviewing the official server documentation and data handling.
- Validate against the installed release; configuration precedence can change the effective result.

## Future work, not implemented

Runtime metrics, MCP health checks, formatter/LSP recommendations, worktree helpers, and change application need separate designs and tests. No feature is advertised as implemented until there is working code, documented setup, and a test or manual verification procedure.
