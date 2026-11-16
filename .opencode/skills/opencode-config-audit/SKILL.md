---
name: OpenCode Configuration Audit
description: Inspect OpenCode configuration, permissions, agents, MCP servers, plugins, and environment setup; use for a read-only health check before proposing changes.
---

# OpenCode Configuration Audit

## Safety rules

- Start read-only. Do not install, update, edit, delete, or publish anything unless the user explicitly asks.
- Never read or print secrets from `.env`, provider credential stores, browser profiles, or shell history.
- Do not assume a feature or config key exists based on a blog post. Use official documentation and the installed version's schema/help output.
- OpenCode Desktop and OpenCode CLI may share configuration but have different interaction surfaces. Do not claim to inspect the user's computer unless the project is running there and the relevant files are accessible.

## Audit workflow

1. Establish platform, OpenCode version, config path, project root, and whether this is a local, project, or global audit. Use `opencode --version` and `opencode debug config` only when available and authorized.
2. Inventory configuration files and precedence. Inspect `opencode.json`/`opencode.jsonc`, `.opencode/agents/`, `.opencode/skills/`, plugin files, and documented MCP configuration. Exclude secrets and cache contents.
3. Validate syntax and schema. Distinguish syntax validity from semantic suitability. Verify fields against the official docs for the installed release, not an assumed “latest v2” schema.
4. Review least privilege. Check agent `mode`, `permission` rules, risky shell operations, write permissions, external-directory access, and MCP exposure. Prefer deny-by-default for read-only agents and approvals for destructive or external actions.
5. Review model/provider configuration without exposing credentials. Confirm model IDs against the provider catalog and note cost, context, latency, and data-handling tradeoffs where known.
6. Review integrations one at a time: MCP, plugins, formatters, LSP, commands, skills, and hooks. Verify installation source, maintenance, permissions, and actual need. Do not add guessed integrations such as an unverified “localweb” MCP.
7. Report each finding with severity, evidence (file and key), impact, recommendation, and whether it is safe to apply. Clearly separate verified issues from optional optimizations.
8. Ask before applying potentially breaking changes. Preserve a backup and explain rollback steps.

## Output format

- Environment/version and audit scope
- Findings: Critical / High / Medium / Low / Informational
- Safe quick wins
- Optional changes requiring approval
- Validation and rollback commands
- Documentation URLs and date checked
