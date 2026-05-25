import { WebSocket } from 'ws';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const AGENT_NAME = 'jeri-agent';
const AGENT_TOPIC = 'january-statements';
const ORCH_TOPIC = 'orchestrator';
const WS_URL = process.env.A2A_WS_URL ?? 'ws://127.0.0.1:3030';
const TARGET_DIR =
  process.env.JERI_TARGET_DIR ??
  '/home/adamsl/rol_finances/readable_documents/bank_statements/january';
const ARTIFACT_ROOT =
  process.env.JERI_ARTIFACT_ROOT ?? '/home/adamsl/rol_finances/artifacts';

function log(msg: string): void {
  console.log(`[${AGENT_NAME}] ${msg}`);
}

type JsonRpcReq = {
  jsonrpc?: string;
  method?: string;
  params?: {
    task_id?: string;
    target_agent?: string;
    description?: string;
    context?: Record<string, unknown>;
    artifacts?: string[];
    capability?: string;
  };
  id?: number | string;
};

type WsEnvelope = {
  type?: string;
  topic?: string;
  from_agent?: string;
  content?: string;
  [k: string]: unknown;
};

function scopeAllowed(requestedPath: string): boolean {
  const base = path.resolve(TARGET_DIR);
  const candidate = path.resolve(requestedPath);
  return candidate === base || candidate.startsWith(`${base}${path.sep}`);
}

async function listFiles(): Promise<string[]> {
  try {
    const entries = await fs.readdir(TARGET_DIR, { withFileTypes: true });
    const files = entries.filter((e) => e.isFile()).map((e) => e.name).sort();
    return files;
  } catch {
    return [];
  }
}

function classifyFiles(files: string[]): { parseable: string[]; manual: string[] } {
  const parseable = files.filter((f) => /\.(txt|csv|json|md)$/i.test(f));
  const manual = files.filter((f) => !parseable.includes(f));
  return { parseable, manual };
}

function inferCapability(description: string, explicit?: string): string {
  if (explicit) return explicit;
  const d = description.toLowerCase();
  if (d.includes('anomal')) return 'find_anomalies';
  if (d.includes('extract')) return 'extract_transactions';
  if (d.includes('snippet') || d.includes('report')) return 'prepare_report_snippet';
  if (d.includes('summary') || d.includes('summar')) return 'summarize_statement';
  return 'analyze_statement';
}

async function buildCapabilityResult(
  capability: string,
  description: string,
  context: Record<string, unknown> = {},
): Promise<{
  status: 'success' | 'error';
  message: string;
  details?: Record<string, unknown>;
  artifacts?: string[];
}> {
  const files = await listFiles();
  const { parseable, manual } = classifyFiles(files);

  const requestedPath = typeof context.path === 'string' ? context.path : TARGET_DIR;
  if (!scopeAllowed(requestedPath)) {
    return {
      status: 'error',
      message: `Scope violation: path is outside TARGET_DIR`,
      details: {
        requestedPath,
        targetDir: TARGET_DIR,
      },
    };
  }

  switch (capability) {
    case 'extract_transactions':
      return {
        status: 'success',
        message: 'Transaction extraction preflight complete.',
        details: {
          description,
          files_considered: parseable,
          estimated_extractable_files: parseable.length,
          requires_manual_review: manual,
        },
      };

    case 'summarize_statement':
      return {
        status: 'success',
        message: 'Statement summary prepared.',
        details: {
          summary: `Found ${files.length} file(s): ${parseable.length} likely machine-parseable, ${manual.length} likely manual-review.`,
          files,
          target_dir: TARGET_DIR,
        },
      };

    case 'prepare_report_snippet': {
      const ts = new Date().toISOString().replace(/[:.]/g, '-');
      const outDir = path.join(ARTIFACT_ROOT, 'jeri-agent');
      const outPath = path.join(outDir, `readiness-summary-${ts}.md`);
      const snippet = [
        '# January Statement Readiness',
        '',
        `- Total files: ${files.length}`,
        `- Machine-parseable candidates: ${parseable.length}`,
        `- Manual-review candidates: ${manual.length}`,
        '',
        '## Parseable',
        ...parseable.map((f) => `- ${f}`),
        '',
        '## Manual Review',
        ...manual.map((f) => `- ${f}`),
      ].join('\n');

      await fs.mkdir(outDir, { recursive: true });
      await fs.writeFile(outPath, snippet, 'utf-8');

      return {
        status: 'success',
        message: 'Report snippet artifact created.',
        details: {
          target_dir: TARGET_DIR,
          artifact_root: ARTIFACT_ROOT,
        },
        artifacts: [outPath],
      };
    }

    case 'find_anomalies':
      return {
        status: 'success',
        message: 'Anomaly scan complete (heuristic).',
        details: {
          anomalies: manual.map((f) => ({ file: f, kind: 'unparseable-format', severity: 'warn' })),
          note: 'Heuristic scan only; deep semantic checks not enabled in this version.',
        },
      };

    case 'analyze_statement':
    default:
      return {
        status: 'success',
        message: 'Statement readiness analysis complete.',
        details: {
          target_dir: TARGET_DIR,
          total_files: files.length,
          parseable_files: parseable,
          manual_review_files: manual,
          recommendation:
            manual.length > 0
              ? 'Run OCR/normalization for manual-review files, then re-run extraction.'
              : 'Proceed to transaction extraction pipeline.',
        },
      };
  }
}

function sendJson(ws: WebSocket, payload: Record<string, unknown>): void {
  ws.send(JSON.stringify(payload));
}

async function handleInbound(ws: WebSocket, msg: WsEnvelope): Promise<void> {
  if (msg.type !== 'message') return;
  if (!msg.content || !msg.topic) return;
  if (msg.topic !== AGENT_TOPIC) return;
  if (msg.from_agent?.toLowerCase() === AGENT_NAME) return;

  let request: JsonRpcReq | null = null;
  try {
    request = JSON.parse(msg.content) as JsonRpcReq;
  } catch {
    // Accept plain text and wrap
    request = {
      jsonrpc: '2.0',
      method: 'agent.execute_task',
      params: {
        task_id: `plain-${Date.now()}`,
        description: String(msg.content ?? ''),
        context: {},
      },
      id: 1,
    };
  }

  if (request?.method !== 'agent.execute_task') {
    log(`Ignoring non-task method: ${request?.method ?? 'unknown'}`);
    return;
  }

  const params = request.params ?? {};
  const description = params.description ?? 'No description provided';
  const context = params.context ?? {};
  const capability = inferCapability(description, params.capability);

  const result = await buildCapabilityResult(capability, description, context);

  const rpcResponse = {
    jsonrpc: '2.0',
    result: {
      status: result.status,
      message: result.message,
      capability,
      task_id: params.task_id,
      details: result.details ?? {},
      artifacts: result.artifacts ?? [],
      agent: AGENT_NAME,
    },
    id: request.id ?? 1,
  };

  sendJson(ws, {
    type: 'send',
    topic: ORCH_TOPIC,
    to_agent: 'board',
    from_agent: AGENT_NAME,
    content: JSON.stringify(rpcResponse),
    priority: 'normal',
    metadata: { source: AGENT_NAME, response_type: 'jsonrpc' },
  });

  const human = [
    `✅ **Jeri Agent Response**`,
    `Capability: ${capability}`,
    `Status: ${result.status}`,
    `${result.message}`,
  ].join('\n');

  sendJson(ws, {
    type: 'send',
    topic: ORCH_TOPIC,
    to_agent: 'board',
    from_agent: AGENT_NAME,
    content: human,
    priority: 'normal',
    metadata: { source: AGENT_NAME, response_type: 'human' },
  });

  log(`Processed task '${capability}' (${params.task_id ?? 'n/a'})`);
}

function start(): void {
  const ws = new WebSocket(WS_URL);

  ws.on('open', () => {
    log(`Connected to ${WS_URL}`);
    sendJson(ws, { type: 'register', agent_id: AGENT_NAME });
  });

  ws.on('message', async (raw) => {
    try {
      const data = JSON.parse(raw.toString()) as WsEnvelope;

      if (data.type === 'registered') {
        log('Registered successfully; subscribing to january-statements');
        sendJson(ws, { type: 'subscribe', topic: AGENT_TOPIC });
        return;
      }

      if (data.type === 'subscribed') {
        log(`Subscribed to topic '${data.topic}'`);
        return;
      }

      await handleInbound(ws, data);
    } catch (err) {
      log(`Message handling error: ${String(err)}`);
    }
  });

  ws.on('close', () => {
    log('WebSocket closed; retrying in 2s...');
    setTimeout(start, 2000);
  });

  ws.on('error', (err) => {
    log(`WebSocket error: ${String(err)}`);
  });

  process.on('SIGTERM', () => {
    log('SIGTERM received; shutting down.');
    ws.close();
    process.exit(0);
  });

  process.on('SIGINT', () => {
    log('SIGINT received; shutting down.');
    ws.close();
    process.exit(0);
  });
}

start();
