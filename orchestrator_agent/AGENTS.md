# Repository Guidelines

## Project Structure & Module Organization
This repository coordinates multiple agents and shared messaging utilities:
- `agent_messaging/`: core transport logic, `messenger.py`, and fixtures/tests in `agent_messaging/tests/`.
- `orchestrator_agent/`: routing and delegation entrypoint (see `main.py`).
- `dashboard_agent/`: health/reliability agent for the Node dashboard with HTTP probes and runtime artifacts.
- `hybrid_letta_agents/`: Letta bridge experiments, docs, and SDK tests under `hybrid_letta_agents/tests/`.
- `tests/`: manual smoke walkthroughs (`test_agent_a.py`, `test_agent_b.py`).
- Root scripts such as `start_a2a_system.sh` and `stop_a2a_system.sh` manage end-to-end startup/shutdown.

## Build, Test, and Development Commands
- `source ../.venv/bin/activate`: activate the shared Python virtualenv.
- `pip install -r hybrid_letta_agents/requirements.txt`: install Letta-specific dependencies.
- `./start_a2a_system.sh`: start memory, orchestrator, dashboard agent, and admin UI (logs in `../logs/`).
- `python agent_messaging/run_collective.py`: run a quick discovery/delegation smoke test.
- `python -m pytest agent_messaging/tests/test_memory_system.py -q`: verify memory system behavior.
- `pytest hybrid_letta_agents/tests -q`: run Letta-side Python tests.
- `npx vitest run`: run JS/Vitest diagnostics in `hybrid_letta_agents` when applicable.

## Coding Style & Naming Conventions
Target Python 3.11+ and follow PEP 8:
- 4-space indentation, `snake_case` for functions, `UpperCamelCase` for classes, `ALL_CAPS` constants (for example `AGENT_NAME`).
- Prefer type hints, `pathlib.Path`, and project logging helpers over ad-hoc `print`.
- In `hybrid_letta_agents`, keep ES module syntax and 2-space indentation consistent with existing files.

## Testing Guidelines
Use `pytest` for Python and Vitest for JavaScript diagnostics.
- Add/maintain tests for transport, routing, and Letta bridge changes.
- Name tests descriptively (`test_memory_system.py`, `test_letta_status.js`).
- For orchestration-flow changes, include reproducible runbooks/transcripts in relevant test/docs locations.

## Commit & Pull Request Guidelines
- Keep commits short and imperative (for example: `agent_messaging: fix delegation timeout`).
- Scope related changes together; avoid mixing refactors with behavior fixes.
- PRs should include: problem statement, commands run, linked Task Master/issue IDs, and UI screenshots for dashboard changes.
- Include rollback/operational notes when touching startup/runtime behavior (for example `./stop_a2a_system.sh`).

## Security & Configuration Tips
- Keep secrets in workspace-level `.env`; never commit keys, logs, or data dumps.
- Prefer config helpers (such as `update_config.py`) over editing generated config files directly.
- Document new ports, dependencies, or config flags in the nearest README.

## A2A Runtime Troubleshooting Memory
- If orchestrator chat only shows `I have routed your request to **coder-agent**`, inspect `/home/adamsl/planner/logs/websocket.log`, `/home/adamsl/planner/logs/orchestrator.log`, and `/home/adamsl/planner/a2a_communicating_agents/logs/coder_agent.log` before changing routing.
- A coder response broadcast after the chat timeout means the worker was slow, not absent; simple hello-world/assembly requests should use deterministic fast paths in `coder_agent/main.py` instead of `codex exec`.
- Follow-up run questions such as `how do I run it?` can be misrouted to `tester-agent` through `run_tests` scoring; keep explicit run-instruction follow-up handling in `orchestrator_agent/main.py` and check self-routing before registered-agent lookup.
- Generic verbs like `check` are ambiguous and must not be tester-agent priority triggers by themselves; in `orchestrator_agent/main.py`, keep tester routing gated by explicit testing intent (`test`, `test suite`, `coverage`, `run tests`, `verify output/behavior`, `regression`) via `_is_testing_intent()`.
- If `CoderAgent_2026` is yellow after `Finished processing...`, inspect `orchestrator_agent/remote_logger.py`; idle heartbeats must not overwrite a terminal green/red LED state, and `Finished` matching must be case-insensitive.
- Verify logger viewer data with `curl -s "http://localhost:8080/php-api/object/select?object_view_id=CoderAgent_2026"` and confirm `public/index.html` in `/home/adamsl/the-factory` contains a matching `accordion-section`.
- First-stop files for routing regressions like this:
  1. `orchestrator_agent/main.py` (`_fallback_route`, `_is_testing_intent`, `_is_run_instructions_followup`)
  2. `orchestrator_agent/tests/test_followup_routing.py` (add/confirm phrase-level regressions)
  3. `/home/adamsl/planner/logs/orchestrator.log` (confirm actual decision/reasoning at runtime)
