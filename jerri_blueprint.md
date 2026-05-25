# Jerri Agent Blueprint (for Orchestrator Integration)

## Goal
Create a new specialist agent named **Jeri** (process name: `jeri-agent`) that the existing **orchestrator-agent** can discover, route to, and communicate with over the same A2A WebSocket messaging system.

Primary work domain:
- `/home/adamsl/rol_finances/readable_documents/bank_statements/january`

Design target:
- Match the **Coder Agent pattern** (agent card + async observer loop + start/stop scripts + topic-based routing).

---

## 1) Agent Identity + Routing Contract

Use this exact runtime identity so routing is predictable:
- `AGENT_NAME = "jeri-agent"`

Suggested primary topic:
- `"january-statements"`

Optional additional topics:
- `"bank-statements"`
- `"readable-documents"`
- `"document-analysis"`

Why this matters:
- Orchestrator discovers agents from `agent.json` files.
- Delegation topic is chosen from the first topic in the agent card (per current `A2ACollectiveHub.prepare_delegation()`).

---

## 2) Files to Add

Create a new directory:
- `a2a_communicating_agents/jeri_agent/`

Add these files:

1. `jeri_agent/agent.json`
2. `jeri_agent/main.py`
3. `start_jeri_agent.sh`
4. `stop_jeri_agent.sh`

Optional:
5. `logs/jeri_agent.log` (created at runtime)
6. `logs/jeri_agent.pid` (created at runtime)

---

## 3) `jeri_agent/agent.json` (Coder-style card)

Use the same schema pattern as `coder_agent/agent.json`.

Required high-level fields:
- `name`: `jeri-agent`
- `version`: `1.0.0`
- `description`: statement/doc specialist for January bank statements
- `capabilities`: array of callable tasks with input/output schema
- `topics`: include `january-statements` first
- `memory_access`: `read-write`

Suggested capabilities:
- `analyze_statement`
- `extract_transactions`
- `summarize_statement`
- `prepare_report_snippet`
- `find_anomalies`

Each should accept at least:
- `description` (string)
- `context` (object, optional)

---

## 4) `jeri_agent/main.py` (Coder-agent structure)

Base it on `coder_agent/main.py` with these edits:

### Constants
- `AGENT_NAME = "jeri-agent"`
- `AGENT_TOPIC = "january-statements"`
- Add `TARGET_DIR = Path("/home/adamsl/rol_finances/readable_documents/bank_statements/january")`

### Messaging pattern
- Keep **AgentMessenger** usage identical to coder-agent/orchestrator pattern.
- Subscribe to Jeri topic with observer callback.
- Handle both:
  - JSON-RPC payloads (`agent.execute_task`)
  - direct human text messages

### Response behavior
- Return concise task status payloads to orchestrator.
- Include:
  - `status`
  - `message`
  - `details`
  - optional `artifacts` (e.g., paths to generated summaries)

### Safety / scope guardrail
- Reject file access outside `TARGET_DIR` unless explicitly allowed in message context.

### Model behavior
- Same approach as coder-agent:
  - Codex CLI if available
  - deterministic fallback for simple tasks

---

## 5) Start/Stop Scripts

### `start_jeri_agent.sh`
Mirror `start_coder_agent.sh` conventions:
- Check stale/running PID at `logs/jeri_agent.pid`
- Ensure `logs/` exists
- Activate venv (same currently used by existing agents)
- Launch: `python -u jeri_agent/main.py >> logs/jeri_agent.log 2>&1`
- Save PID

### `stop_jeri_agent.sh`
- Read PID file
- Graceful kill, then force kill if needed
- Remove stale PID file

---

## 6) Orchestrator Compatibility Checklist

No orchestrator code changes are strictly required for basic delegation if:
- `jeri_agent/agent.json` exists
- orchestrator refreshes registry
- topic is valid and Jeri is running/subscribed

Recommended optional tweaks later:
- Add explicit topic mapping for `jeri-agent` in orchestrator helper map for deterministic routing diagnostics.
- Add router prompt/examples so LLMRouter chooses `jeri-agent` for January statement tasks.

---

## 7) Verification Steps

1. Start core services:
- WebSocket server
- Orchestrator
- Letta bridge (if needed)

2. Start Jeri:
- `./start_jeri_agent.sh`

3. Confirm process:
- PID exists and process alive
- tail `logs/jeri_agent.log`

4. Send delegation test message to orchestrator asking for January statement analysis.

5. Confirm in logs:
- orchestrator discovers `jeri-agent`
- route decision targets `jeri-agent`
- message delivered on `january-statements`
- Jeri responds and orchestrator receives response

---

## 8) First Practical Task for Jeri

Initial task prompt to validate value:
- “Review January bank statement files in `/home/adamsl/rol_finances/readable_documents/bank_statements/january`, list available files, and produce a short summary of what can be parsed automatically vs what needs manual review.”

Expected output:
- file inventory
- parse-readiness summary
- suggested next actions for report pipeline

---

## 9) Naming Note

This blueprint file uses the requested name:
- `jerri_blueprint.md`

Agent runtime naming remains:
- **Jeri** (human name)
- `jeri-agent` (system ID)

