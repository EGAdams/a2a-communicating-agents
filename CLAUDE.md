# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An agent-to-agent (A2A) communication system. A hub-and-spoke collective where an
**orchestrator** receives natural-language requests over a WebSocket message board,
decides whether to answer directly or delegate, and routes work to specialist agents
(**coder**, **tester**, **jeri**) over topic-based pub/sub. Agents advertise capabilities
via `agent.json` cards (Google A2A protocol style).

## Environment & commands

Activate the virtualenv before any Python command. The user relies on a shell alias `so`
to do this — use it rather than guessing a venv path (there are several venvs on this machine
and scripts disagree about which one). `start_a2a_system.sh` hard-requires `$VIRTUAL_ENV` to
be set first and exits otherwise.

```bash
so                        # activate venv (user alias)

bash start_all.sh         # PREFERRED: starts websocket server (waits for :3030),
                          # then letta bridge, orchestrator, coder, tester, jeri — in order
bash stop_all.sh

# Chat with the orchestrator (interactive)
cd agent_messaging && python3 orchestrator_chat.py

# Quick discovery + delegation smoke test
python agent_messaging/run_collective.py
```

`start_a2a_system.sh` is an older startup path that still references `/home/adamsl/planner/`
paths and starts a Node dashboard on :3000 + Letta on :8283. Prefer `start_all.sh` for the
core agent system. Note this directory layout discrepancy: runtime code resolves its own root
via `PROJECT_ROOT = Path(__file__).resolve().parents[1]`, but several scripts and the `AGENTS.md`
troubleshooting notes hardcode `/home/adamsl/planner/...` log paths — actual logs land in `./logs/`.

### Tests

```bash
python -m pytest agent_messaging/tests/test_memory_system.py -q   # memory backends
python -m pytest orchestrator_agent/tests/test_routing.py -q      # router chain (pure unit, mocked LLM)
pytest -q <path>::<test_name>                                     # single test
cd jeri_agent && npm run build                                    # TypeScript agent (tsc)
```

`orchestrator_agent/tests/` are fast, fully-mocked unit tests (no live agents needed).
`agent_messaging/tests/*.sh` and `tests/test_agent_a.py`/`test_agent_b.py` are manual
end-to-end smoke walkthroughs that require the live system running.

## Architecture

### Transport layer (`agent_messaging/`)
Messaging is built on layered GoF patterns — understand these before touching messaging:

- **`message_transport.py`** — `MessageTransport` abstract interface (Strategy).
- **Concrete transports** — `WebSocketTransport` (primary, `ws://localhost:3030`),
  `LettaTransport`, `RAGBoardTransport` (last-resort fallback that goes through ChromaDB).
- **`transport_factory.py`** — `TransportFactory` tries transports in priority order
  (websocket → letta → rag) and returns the first that connects.
- **`transport_manager.py`** — `TransportManager` **Singleton**. ALL agents share ONE
  WebSocket connection through it. This exists specifically to fix a prior bug where every
  `inbox()`/`post_message()` call spun up a new transport and silently fell back to RAG.
  Do not bypass it by constructing transports directly.
- **`messenger.py`** — `AgentMessenger` **Facade** over the transport. Async-first
  (`send_to_agent_async`, `post_message_async`, `inbox_async`, `subscribe_async`). Sync
  wrappers exist only for backward compat and are deprecated. New agents should `subscribe()`
  to a topic and handle messages via async callback (**Observer**), not poll `inbox()`.
- **`websocket_server.py`** — standalone pub/sub server on :3030. Topic → set of subscribers,
  keeps last 100 messages per topic for late subscribers.

The async refactor (singleton transport + observer-based agents) is the intended end state;
`REFACTOR_STATUS.md`, `ASYNC_REFACTOR_PLAN.md`, and `CONTINUE_REFACTOR.md` document its history
and any remaining sync-fallback rough edges.

### Routing (`orchestrator_agent/routing/`)
The orchestrator's decision logic is a **Chain of Responsibility**:

- `interfaces.py` — `IRouter` contract, plus frozen value objects `RouteDecision` and
  `RoutingContext`. `target == SELF` (`"__self__"`) means the orchestrator answers directly;
  any other target is an agent name to delegate to.
- `llm_router.py` — `LLMRouter` makes ONE LLM call per message that either returns
  `DELEGATE: <agent-name>` or a direct answer. The blocking LLM/subprocess call is wrapped in
  `run_in_executor` so WebSocket keepalives survive long calls.
- `fallback_router.py` — `FallbackRouter`, always-commits safe default (end of chain).
- `router_chain.py` — `RouterChain` returns the first non-None decision.

`orchestrator_agent/main.py` also has keyword/heuristic fast-paths *before* the LLM
(`_fallback_route`, `_is_testing_intent`, `_is_run_instructions_followup`). These are
deliberately narrow — see the routing gotchas below.

### Agents
Each specialist (`coder_agent/`, `tester_agent/`, `dashboard_agent/`, `jeri_agent/`) has an
`agent.json` card declaring `capabilities` and the `topics` it subscribes to (e.g. coder →
`code`, tester → `testing`). The orchestrator discovers cards via `A2ADispatcher` /
`A2ACollectiveHub` (`a2a_dispatcher.py`, `a2a_collective.py`) and delegates by posting a
JSON-RPC payload to the target's topic. `jeri_agent/` is TypeScript/Node (build with `tsc`),
the rest are Python.

### Memory (`agent_messaging/` + `rag_system/`)
Unified memory behind a `MemoryBackend` interface with two backends — `LettaMemory` and
`ChromaDBMemory` — chosen by `MemoryFactory`. Top-level async helpers `remember`/`recall`/
`forget`/`get_recent_memories` (exported from `agent_messaging/__init__.py`) are the intended
entry points. `rag_system/` provides the ChromaDB-backed document store and RAG engine that
both the memory layer and the RAG transport fallback use. ChromaDB data lives under `storage/`
(do not commit it).

### Remote logging
Agents push status lines to an external viewer (`localhost:8080` PHP API) via
`orchestrator_agent/remote_logger.py`, keyed by IDs like `OrchestratorAgent_2026` /
`CoderAgent_2026`, which drive colored status LEDs on a dashboard.

## Routing gotchas (hard-won; check before changing routing)
- Follow-up questions like "how do I run it?" must NOT route to `tester-agent` just because
  tester owns `run_tests`. Self-routing is checked before registered-agent lookup; keep
  explicit run-instruction handling in `main.py`.
- Generic verbs like `check` are ambiguous and must not, alone, trigger tester routing.
  Tester routing is gated by explicit testing intent via `_is_testing_intent()`
  (`test`, `test suite`, `coverage`, `run tests`, `regression`, etc.).
- Trivial requests (hello-world, assembly hello-world) should use deterministic fast paths in
  `coder_agent/main.py`, not `codex exec`, to avoid chat timeouts.
- A coder response arriving after the chat timeout means the worker was slow, not unreachable.
- For LED-stuck-yellow issues, see `remote_logger.py`: `Finished` matching must be
  case-insensitive and idle heartbeats must not overwrite a terminal green/red state.
- Routing-regression first stops: `orchestrator_agent/main.py` → `orchestrator_agent/tests/`
  → `logs/orchestrator.log` (confirm the actual runtime decision/reasoning).

## Conventions
- Python 3.10+ (`pyproject.toml`), PEP 8, 4-space indent, `snake_case`, `UpperCamelCase`,
  `ALL_CAPS` module constants (e.g. `AGENT_NAME`, `AGENT_TOPIC`). Prefer `pathlib.Path` and the
  `log_update()` logging helpers over bare `print`.
- `jeri_agent/` (and `hybrid_letta_agents/` JS): ES modules, 2-space indent.
- Secrets live in workspace `.env` (`.env.example` is the template). Never commit keys,
  `storage/` dumps, or `logs/`.
- `*.backup` files (e.g. `main.py.backup`, `messenger.py.backup`) are pre-refactor snapshots,
  not active code.
