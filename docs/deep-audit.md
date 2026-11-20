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

By default, the report runs the installed CLI’s `opencode debug config` and uses its resolved configuration for capability and permission analysis. Online, it also checks the official homepage, docs inventory, versioned docs/changelog, public GitHub repository metadata and README, and a bounded Releases API query for stable v2 candidates. It then fetches selected core site documentation and every top-level Markdown/MDX specification under `specs/v2` from the exact stable release tag using GitHub Contents API manifests and raw file URLs. The report links those fetched files at the pinned tag; file text is held in memory only for local semantic checks and is not cached or printed. Discord is a manual invite link; X is a non-canonical announcement link and its ownership is not independently asserted. It also inventories candidate global, custom, project, and managed JSON/JSONC config files without printing full paths or arbitrary values. If the CLI is not in `PATH`, set `OPENCODE_CLI`, pass `--cli <path>`, or use `--skip-debug-config` for file-only analysis. It compares top-level keys with the live official schema, detects fields marked deprecated, identifies some obvious mixed legacy/current dialects, and reviews visible model/provider IDs, permission rules, policies, MCP servers, plugin packages, agents, commands, and configured formatters/LSP/instruction paths. It discovers TypeScript/JavaScript custom-tool definition files in documented global/project `tools` directories, and local Skills in documented compatibility roots and configured local `skills` paths. Custom tool/plugin/formatter/LSP source is not read or executed; HTTP Skill catalog contents are never fetched.

The **Release-pinned semantic comparison** uses rules grounded in `migrate-v1.mdx` fetched from the selected stable v2 tag. It currently reports only concrete cases, including explicitly ignored legacy compaction fields, MCP `enabled`/`disabled` inversion, numeric MCP timeout shape, and overlapping old/new root config maps where the migration guide states precedence. The report links each finding to its tagged source section and does not print setting values or custom agent/server names. A missing finding is not a certification: the analyzer intentionally does not infer unsupported mappings or plugin/runtime compatibility from file presence alone. When `debug config` succeeds the resolved CLI output is compared; otherwise only parsed file merges are available and the report labels them provisional. Partial or unavailable documentation is disclosed and cannot support claims beyond the text that was actually retrieved.

For a source project, the language inventory uses file extensions and checks whether common local formatter/compiler/LSP executables are present on `PATH`; it does not run them or read source-file contents. Agent permissions are approximated from visible settings. This is not a full implementation of OpenCode’s runtime precedence, wildcard matching, policy engine, Desktop state, or hidden configuration.

## Reading findings

A `DEPRECATED_CONFIG_KEY` means the current live schema marks a field deprecated; it does **not** prove that the installed OpenCode version rejects it. If `debug config` succeeds and the installed CLI retains that key in its resolved config, corresponding schema-only unknown/deprecated/type warnings are suppressed. Other schema type checks remain advisory. The official documentation and live schema can contain examples from different generations, so the auditor supports both plural current forms (`agents`, `permissions`, `plugins`, `providers`, `snapshots`) and their legacy counterparts for inventory, and advises testing migrations on a copy. It never rewrites a configuration.

A missing field is not necessarily a defect: defaults may supply it. Similarly, a server declaration does not prove it connects, a model ID does not prove credentials work, and a Desktop account/session selection may not be stored in the inspected JSON files. A version read from `opencode --version` is the CLI version and may not match the Desktop package. Latest v2 is selected from non-draft, non-prerelease GitHub release tags matching `v2.x.y`, sorted numerically, and compared with the changelog’s first v2 label. A mismatch is explicitly unconfirmed.

## Privacy and safety

The CLI’s raw `debug config` stdout/stderr are parsed locally and discarded. Credential-like values are redacted in memory before analysis. The report does not print secret values, arbitrary permission resource patterns, configured skill URLs, config contents, or raw MCP command output. The command may load remote configuration supported by the installed build; use `--skip-debug-config` if you want file-only inspection. It may show model IDs, agent and skill IDs, plugin names, MCP server IDs, permission action names, and package/tool presence. Review the report before sharing it. The auditor does not access OpenCode’s private Desktop database, OAuth/token store, remote organization policy, macOS managed preferences, inline `OPENCODE_CONFIG_CONTENT`, or all environment variables.

No audit recommendation is automatically applied. Back up configuration first and validate any approved change in a disposable project with the exact Desktop/OpenCode version you use.
