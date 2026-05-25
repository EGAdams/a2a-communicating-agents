#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Iterable


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src_dir: Path, dst_dir: Path) -> None:
    if not src_dir.exists():
        return
    for path in src_dir.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(src_dir)
        copy_file(path, dst_dir / rel)


def canonical_skill_file(skill_dir: Path) -> Path | None:
    candidates = ["SKILL.md", "Skill.md", "skill.md", "README.md"]
    for name in candidates:
        candidate = skill_dir / name
        if candidate.exists():
            return candidate
    return None


def normalize_skill(skill_dir: Path, out_dir: Path) -> dict:
    out_skill_dir = out_dir / skill_dir.name
    out_skill_dir.mkdir(parents=True, exist_ok=True)

    primary = canonical_skill_file(skill_dir)
    primary_out = None
    if primary:
        primary_out = out_skill_dir / "SKILL.md"
        copy_file(primary, primary_out)

    # Copy all supporting files except duplicate skill variants.
    skip_names = {"SKILL.md", "Skill.md", "skill.md"}
    for src in skill_dir.rglob("*"):
        if src.is_dir():
            continue
        if src == primary:
            continue
        if src.name in skip_names:
            continue
        rel = src.relative_to(skill_dir)
        copy_file(src, out_skill_dir / rel)

    return {
        "name": skill_dir.name,
        "path": str(out_skill_dir),
        "has_skill_md": bool(primary_out),
    }


def build_hooks_manifest(settings_path: Path, out_path: Path) -> None:
    data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = data.get("hooks", {})
    out = {
        "source_settings": str(settings_path),
        "denied_tools": data.get("deniedTools", []),
        "hook_events": hooks,
    }
    out_path.write_text(json.dumps(out, indent=2) + "\n")


def build_tools_manifest(settings_local_path: Path, out_path: Path) -> None:
    data = json.loads(settings_local_path.read_text()) if settings_local_path.exists() else {}
    permissions = data.get("permissions", {})
    mcp_servers = data.get("mcpServers", {})
    out = {
        "source_settings_local": str(settings_local_path),
        "permissions": permissions,
        "enable_all_project_mcp_servers": data.get("enableAllProjectMcpServers", False),
        "enabled_mcpjson_servers": data.get("enabledMcpjsonServers", []),
        "mcp_servers": mcp_servers,
    }
    out_path.write_text(json.dumps(out, indent=2) + "\n")


def list_markdown_files(path: Path) -> Iterable[Path]:
    if not path.exists():
        return []
    return sorted([p for p in path.rglob("*.md") if p.is_file()])


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Codex-style initialization from .master_claude")
    parser.add_argument("--source", default="/home/adamsl/.master_claude", help="Source .master_claude directory")
    parser.add_argument(
        "--output",
        default="/home/adamsl/planner/a2a_communicating_agents/orchestrator_agent/.codex_initialization/master_claude",
        help="Destination directory",
    )
    args = parser.parse_args()

    src = Path(args.source).resolve()
    out = Path(args.output).resolve()

    if not src.exists():
        raise SystemExit(f"Source directory not found: {src}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    agents_out = out / "agents"
    skills_out = out / "skills"
    commands_out = out / "commands"
    tools_out = out / "tools"
    hooks_out = out / "hooks"
    styles_out = out / "output_styles"
    docs_out = out / "docs"

    agents_out.mkdir(parents=True, exist_ok=True)
    skills_out.mkdir(parents=True, exist_ok=True)
    commands_out.mkdir(parents=True, exist_ok=True)
    tools_out.mkdir(parents=True, exist_ok=True)
    hooks_out.mkdir(parents=True, exist_ok=True)
    styles_out.mkdir(parents=True, exist_ok=True)
    docs_out.mkdir(parents=True, exist_ok=True)

    # Agents
    agent_records = []
    for md in list_markdown_files(src / "agents"):
        rel = md.relative_to(src / "agents")
        dst = agents_out / rel
        copy_file(md, dst)
        agent_records.append({"name": md.stem, "path": str(dst)})

    # Skills
    skill_records = []
    skills_src = src / "skills"
    if skills_src.exists():
        for skill_dir in sorted([p for p in skills_src.iterdir() if p.is_dir()]):
            skill_records.append(normalize_skill(skill_dir, skills_out))

    # Commands
    for md in list_markdown_files(src / "commands"):
        rel = md.relative_to(src / "commands")
        copy_file(md, commands_out / rel)

    # Hooks + tool/manifests
    copy_tree(src / "hooks", hooks_out)
    build_hooks_manifest(src / "settings.json", tools_out / "hooks_manifest.json")
    build_tools_manifest(src / "settings.local.json", tools_out / "tooling_manifest.json")

    # Other useful context
    copy_tree(src / "output-styles", styles_out)
    copy_tree(src / "docs", docs_out)

    # Top-level catalog for quick import
    catalog = {
        "source": str(src),
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "agents_count": len(agent_records),
        "skills_count": len(skill_records),
        "commands_count": len(list(list_markdown_files(src / "commands"))),
        "agents": agent_records,
        "skills": skill_records,
    }
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")

    readme = out / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Codex Initialization from .master_claude",
                "",
                "This folder is generated from `/home/adamsl/.master_claude` and provides a Codex-style bootstrap:",
                "",
                "- `agents/`: imported agent prompt definitions (`*.md`).",
                "- `skills/`: normalized skill folders with canonical `SKILL.md` files.",
                "- `commands/`: imported command prompts.",
                "- `hooks/`: hook scripts migrated as tooling/runtime helpers.",
                "- `tools/hooks_manifest.json`: extracted hook event wiring from `settings.json`.",
                "- `tools/tooling_manifest.json`: permissions and MCP tooling from `settings.local.json`.",
                "- `output_styles/` and `docs/`: supporting style + docs assets.",
                "- `catalog.json`: machine-readable inventory.",
                "",
                "Regenerate:",
                "",
                "```bash",
                "python codex_init/generate_from_master_claude.py",
                "```",
            ]
        )
        + "\n"
    )

    print(f"Generated: {out}")
    print(f"Agents: {len(agent_records)} | Skills: {len(skill_records)}")


if __name__ == "__main__":
    main()
