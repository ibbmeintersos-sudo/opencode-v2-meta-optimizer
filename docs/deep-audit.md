# Deep audit guide

`deep_audit.py` is a local, read-only configuration review tool for one OpenCode Desktop user. It is designed to help you decide what to check next; it does not optimize settings by silently editing them.

## Run it

On Windows, double-click `run-report-windows.bat`. Leave the project path blank to review global settings, or enter a project folder to include its nearest project config, `.opencode` resources, compatible Skills directories, and language/tool inventory. The launcher asks separately whether you want an MCP runtime check; choose `N` or press Enter for static analysis only.

From a terminal:

```text
python scripts/deep_audit.py --global-only --output opencode-deep-audit.md
python scripts/deep_audit.py --project-dir "C:\\path\\to\\project" --output opencode-deep-audit.md
python scripts/deep_audit.py --global-only --check-mcp --output opencode-deep-audit.md
```

`--check-mcp` explicitly runs `opencode mcp list`. Depending on the OpenCode build, this may launch local MCP processes or contact remote endpoints. The auditor discards raw command output and records only safely parsed server IDs and coarse status. It does not run this check by default. It does not test model-provider authentication or spend tokens.

## What the report covers

The report inventories candidate global, custom, project, and managed JSON/JSONC config files without printing full paths or arbitrary values. It compares top-level keys with the live official schema, detects fields marked deprecated, identifies some obvious mixed legacy/current dialects, and reviews visible model/provider IDs, permission rules, policies, MCP servers, plugin packages, agents, commands, and configured formatters/LSP/instruction paths. It discovers TypeScript/JavaScript custom-tool definition files in documented global/project `tools` directories, and local Skills in documented compatibility roots and configured local `skills` paths. Custom tool/plugin/formatter/LSP source is not read or executed; HTTP Skill catalog contents are never fetched.

For a source project, the language inventory uses file extensions and checks whether common local formatter/compiler/LSP executables are present on `PATH`; it does not run them or read source-file contents. Agent permissions are approximated from visible settings. This is not a full implementation of OpenCode’s runtime precedence, wildcard matching, policy engine, Desktop state, or hidden configuration.

## Reading findings

A `DEPRECATED_CONFIG_KEY` means the current live schema marks a field deprecated; it does **not** prove that the installed OpenCode version rejects it. The official documentation and live schema can contain examples from different generations, so the auditor supports both plural current forms (`agents`, `permissions`, `plugins`, `providers`, `snapshots`) and their legacy counterparts for inventory, and advises testing migrations on a copy. It never rewrites a configuration.

A missing field is not necessarily a defect: defaults may supply it. Similarly, a server declaration does not prove it connects, a model ID does not prove credentials work, and a Desktop account/session selection may not be stored in the inspected JSON files. A version read from `opencode --version` is the CLI version and may not match the Desktop package.

## Privacy and safety

The report does not print secret values, arbitrary permission resource patterns, configured skill URLs, config contents, or raw MCP command output. It may show model IDs, agent and skill IDs, plugin names, MCP server IDs, permission action names, and package/tool presence. Review the report before sharing it. The auditor does not access OpenCode’s private Desktop database, OAuth/token store, remote organization policy, macOS managed preferences, inline `OPENCODE_CONFIG_CONTENT`, or all environment variables.

No audit recommendation is automatically applied. Back up configuration first and validate any approved change in a disposable project with the exact Desktop/OpenCode version you use.
