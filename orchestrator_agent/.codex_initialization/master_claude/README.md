# Codex Initialization from .master_claude

This folder is generated from `/home/adamsl/.master_claude` and provides a Codex-style bootstrap:

- `agents/`: imported agent prompt definitions (`*.md`).
- `skills/`: normalized skill folders with canonical `SKILL.md` files.
- `commands/`: imported command prompts.
- `hooks/`: hook scripts migrated as tooling/runtime helpers.
- `tools/hooks_manifest.json`: extracted hook event wiring from `settings.json`.
- `tools/tooling_manifest.json`: permissions and MCP tooling from `settings.local.json`.
- `output_styles/` and `docs/`: supporting style + docs assets.
- `catalog.json`: machine-readable inventory.

Regenerate:

```bash
python codex_init/generate_from_master_claude.py
```
