# Contributing

1. Open an issue describing the OpenCode version, operating system, and reproducible behavior. Remove secrets and private data.
2. Keep changes scoped and compatible with the documented OpenCode version. Link official documentation for configuration changes.
3. Do not add API tokens, personal configuration dumps, or private project data.
4. Run `python3 -m unittest discover -s tests -v` and `python3 scripts/audit_config.py config/opencode.jsonc`.
5. Describe any security or permission impact in the pull request.

This project is community-maintained and is not affiliated with OpenCode.
