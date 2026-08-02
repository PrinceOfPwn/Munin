#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    text = read(path)
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}: {old[:80]!r}")
    write(path, text.replace(old, new, count))


def append_once(path: str, marker: str, content: str) -> None:
    text = read(path)
    if marker in text:
        return
    write(path, text.rstrip() + "\n\n" + content.strip() + "\n")


# ---------------------------------------------------------------------------
# Process output: partial reads, bounded backpressure, chunk preservation,
# and redaction before any live or durable consumer sees the data.
# ---------------------------------------------------------------------------
replace(
    "munin/mcp/opsec.py",
    "import json\nimport logging\n",
    "import codecs\nimport json\nimport logging\n",
)
replace(
    "munin/mcp/opsec.py",
    "from .config import Settings\n",
    "from .audit import redact_secrets\nfrom .config import Settings\n",
)
replace(
    "munin/mcp/opsec.py",
    '_PROCESS_OUTPUT_CHUNK_CHARS = int(os.environ.get("MUNIN_PROCESS_OUTPUT_CHUNK_CHARS", "4096"))\n',
    '_PROCESS_OUTPUT_CHUNK_CHARS = max(256, int(os.environ.get("MUNIN_PROCESS_OUTPUT_CHUNK_CHARS", "4096")))\n'
    '_PROCESS_OUTPUT_QUEUE_SIZE = max(8, int(os.environ.get("MUNIN_PROCESS_OUTPUT_QUEUE_SIZE", "256")))\n',
)
replace(
    "munin/mcp/opsec.py",
    '''        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        output_buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}
        output_sizes = {"stdout": 0, "stderr": 0}
        reader_threads: list[threading.Thread] = []

        def read_stream(stream_name: str, pipe: Any) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    if line:
                        output_queue.put((stream_name, line))
            finally:
                output_queue.put((stream_name, None))
''',
    '''        # Bound the producer/consumer gap. A verbose scanner now applies
        # backpressure to its pipe reader instead of allocating an unbounded
        # number of Python strings and downstream events.
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue(
            maxsize=_PROCESS_OUTPUT_QUEUE_SIZE
        )
        output_buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}
        output_sizes = {"stdout": 0, "stderr": 0}
        reader_threads: list[threading.Thread] = []

        def read_stream(stream_name: str, pipe: Any) -> None:
            """Read available bytes in bounded chunks without waiting for newlines."""
            encoding = getattr(pipe, "encoding", None) or "utf-8"
            errors = getattr(pipe, "errors", None) or "replace"
            decoder = codecs.getincrementaldecoder(encoding)(errors=errors)
            try:
                while True:
                    raw = os.read(pipe.fileno(), _PROCESS_OUTPUT_CHUNK_CHARS)
                    if not raw:
                        break
                    text = decoder.decode(raw)
                    if text:
                        output_queue.put((stream_name, text))
                tail = decoder.decode(b"", final=True)
                if tail:
                    output_queue.put((stream_name, tail))
            finally:
                output_queue.put((stream_name, None))
''',
)
replace(
    "munin/mcp/opsec.py",
    '''            if text:
                last_activity = now
                output_sequence += 1
                text = text[:_PROCESS_OUTPUT_CHUNK_CHARS]
                output_buffers[stream_name].append(text)
                output_sizes[stream_name] += len(text)
                # Retain a bounded tail for the final result while streaming
                # every bounded chunk to the operator-facing event log.
                max_chars = max(1, int(self.settings.max_output_chars))
                while output_sizes[stream_name] > max_chars and output_buffers[stream_name]:
                    removed = output_buffers[stream_name].pop(0)
                    output_sizes[stream_name] -= len(removed)
                emit_process_event(
                    {
                        "kind": "tool_output",
                        "stream": stream_name,
                        "text": text,
                        "sequence": output_sequence,
                        "elapsed_ms": int((now - started_at) * 1000),
                        "final": False,
                    }
                )
''',
    '''            if text:
                last_activity = now
                safe_text = str(redact_secrets(text))
                max_chars = max(1, int(self.settings.max_output_chars))
                # os.read already coalesces newline-heavy output. Chunk again
                # defensively because decoded text length is not a byte count.
                for start in range(0, len(safe_text), _PROCESS_OUTPUT_CHUNK_CHARS):
                    chunk = safe_text[start : start + _PROCESS_OUTPUT_CHUNK_CHARS]
                    if not chunk:
                        continue
                    output_sequence += 1
                    output_buffers[stream_name].append(chunk)
                    output_sizes[stream_name] += len(chunk)
                    # Keep an exact bounded tail for the final tool result.
                    excess = output_sizes[stream_name] - max_chars
                    while excess > 0 and output_buffers[stream_name]:
                        first = output_buffers[stream_name][0]
                        if len(first) <= excess:
                            output_buffers[stream_name].pop(0)
                            output_sizes[stream_name] -= len(first)
                            excess -= len(first)
                        else:
                            output_buffers[stream_name][0] = first[excess:]
                            output_sizes[stream_name] -= excess
                            excess = 0
                    emit_process_event(
                        {
                            "kind": "tool_output",
                            "stream": stream_name,
                            "text": chunk,
                            "sequence": output_sequence,
                            "elapsed_ms": int((now - started_at) * 1000),
                            "final": False,
                        }
                    )
''',
)

# ---------------------------------------------------------------------------
# Job progress: manager-owned identity/order, lossless bounded hand-off, and a
# conservative active signal under lock contention.
# ---------------------------------------------------------------------------
replace(
    "munin/mcp/jobs.py",
    "from collections.abc import Callable\n",
    "import logging\nimport os\n\nfrom collections.abc import Callable\n",
)
replace(
    "munin/mcp/jobs.py",
    "from threading import Lock\n",
    "from threading import Condition, Lock\n",
)
replace(
    "munin/mcp/jobs.py",
    "LOCK_TIMEOUT = 2.0\n",
    '''LOCK_TIMEOUT = 2.0
MAX_PENDING_PROGRESS_EVENTS = max(
    128, int(os.environ.get("MUNIN_MAX_PENDING_PROGRESS_EVENTS", "1024"))
)
logger = logging.getLogger(__name__)
''',
)
replace(
    "munin/mcp/jobs.py",
    '''        self.lock = Lock()
        self.is_shutdown = False
''',
    '''        self.lock = Lock()
        self.progress_changed = Condition(self.lock)
        self.is_shutdown = False
''',
)
replace(
    "munin/mcp/jobs.py",
    '''        with self.lock:
            if self.is_shutdown:
                return
            self.is_shutdown = True
''',
    '''        with self.progress_changed:
            if self.is_shutdown:
                return
            self.is_shutdown = True
            self.progress_changed.notify_all()
''',
)
replace(
    "munin/mcp/jobs.py",
    '''        with self.lock:
            job = self.records.get(job_id)
            if not job:
                return
            if job.progress is None:
                job.progress = []
            job.progress_sequence += 1
            job.progress.append(
                {
                    "at": utc_now_iso(),
                    "sequence": job.progress_sequence,
                    "run_id": job.run_id,
                    "job_id": job.job_id,
                    "tool_name": job.tool,
                    "tool_call_id": job.tool_call_id,
                    **event,
                }
            )
            # Keep polling payloads bounded even for a pathological ReAct loop.
            if len(job.progress) > 100:
                del job.progress[:-100]
''',
    '''        with self.progress_changed:
            job = self.records.get(job_id)
            if not job:
                return
            if job.progress is None:
                job.progress = []
            # A run-scoped consumer acknowledges events in progress_for_run.
            # Backpressure here preserves every unread output chunk instead of
            # silently truncating the first burst beyond an arbitrary 100 rows.
            while (
                job.run_id
                and len(job.progress) >= MAX_PENDING_PROGRESS_EVENTS
                and not self.is_shutdown
            ):
                self.progress_changed.wait(timeout=0.25)
            if self.is_shutdown:
                return
            job.progress_sequence += 1
            payload = dict(event)
            if "sequence" in payload:
                payload.setdefault("source_sequence", payload["sequence"])
            job.progress.append(
                {
                    "at": utc_now_iso(),
                    **payload,
                    # Manager-owned identity and ordering; never caller supplied.
                    "sequence": job.progress_sequence,
                    "run_id": job.run_id,
                    "job_id": job.job_id,
                    "tool_name": job.tool,
                    "tool_call_id": job.tool_call_id,
                }
            )
            # Direct MCP jobs have no run stream to acknowledge progress. Keep
            # their polling payload compact without affecting run-scoped data.
            if not job.run_id and len(job.progress) > 100:
                del job.progress[:-100]
''',
)
replace(
    "munin/mcp/jobs.py",
    '''            events: list[dict[str, Any]] = []
            for job in self.records.values():
                if job.run_id != run_id:
                    continue
                after = int(cursors.get(job.job_id, 0))
                for event in (job.progress or []):
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= after:
                        continue
                    if event.get("kind") not in {"tool_output", "tool_heartbeat"}:
                        cursors[job.job_id] = max(cursors.get(job.job_id, 0), sequence)
                        continue
                    events.append(dict(event))
                    cursors[job.job_id] = max(cursors.get(job.job_id, 0), sequence)
            events.sort(key=lambda item: (str(item.get("at") or ""), int(item.get("sequence") or 0)))
            return events
''',
    '''            events: list[dict[str, Any]] = []
            consumed_any = False
            for job in self.records.values():
                if job.run_id != run_id:
                    continue
                after = int(cursors.get(job.job_id, 0))
                for event in (job.progress or []):
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= after:
                        continue
                    if event.get("kind") in {"tool_output", "tool_heartbeat"}:
                        events.append(dict(event))
                    cursors[job.job_id] = max(cursors.get(job.job_id, 0), sequence)
                acknowledged = int(cursors.get(job.job_id, 0))
                if acknowledged and job.progress:
                    before = len(job.progress)
                    job.progress[:] = [
                        event
                        for event in job.progress
                        if int(event.get("sequence") or 0) > acknowledged
                    ]
                    consumed_any = consumed_any or len(job.progress) != before
            if consumed_any:
                self.progress_changed.notify_all()
            events.sort(key=lambda item: (str(item.get("at") or ""), int(item.get("sequence") or 0)))
            return events
''',
)
replace(
    "munin/mcp/jobs.py",
    '''        if not run_id or not self._acquire_lock():
            return False
''',
    '''        if not run_id:
            return False
        if not self._acquire_lock():
            # Contention is not proof of completion. Keep the stream open and
            # retry on the next poll rather than truncating final output.
            return True
''',
)

# ---------------------------------------------------------------------------
# Runtime delivery: diagnostic overflow and one final race-closing drain.
# ---------------------------------------------------------------------------
replace(
    "munin/core/runtime_adapter.py",
    "import asyncio\nimport os\n",
    "import asyncio\nimport logging\nimport os\n",
)
replace(
    "munin/core/runtime_adapter.py",
    "DEFAULT_RECURSION_LIMIT = _recursion_limit_from_environment()\n",
    "DEFAULT_RECURSION_LIMIT = _recursion_limit_from_environment()\n\nlogger = logging.getLogger(__name__)\n",
)
replace(
    "munin/core/runtime_adapter.py",
    '''                except asyncio.QueueFull:  # pragma: no cover - backpressure guard
                    pass
''',
    '''                except asyncio.QueueFull:  # pragma: no cover - backpressure guard
                    logger.warning(
                        "dropping progress envelope because the run queue is full "
                        "(run_id=%s kind=%s)",
                        run_id,
                        envelope.get("kind"),
                    )
''',
)
replace(
    "munin/core/runtime_adapter.py",
    '''                if graph_finished.is_set() and not JOBS.has_active_run(run_id):
                    # All queued output was emitted before this sentinel was
                    # inserted.  The consumer can now close deterministically
                    # without racing the final process-reader lines.
                    await event_queue.put(("progress_done", None))
                    return
''',
    '''                if graph_finished.is_set() and not JOBS.has_active_run(run_id):
                    # A job can append its last chunks and become inactive
                    # between the drain above and this check. Read once more
                    # before inserting the terminal sentinel.
                    for event in JOBS.progress_for_run(run_id, cursors):
                        await event_queue.put(("envelope", event))
                    await event_queue.put(("progress_done", None))
                    return
''',
)

# ---------------------------------------------------------------------------
# Trusted run association for async MCP jobs.
# ---------------------------------------------------------------------------
replace(
    "munin/mcp/main.py",
    '''    try:
        from ..core.execution_progress import active_tool_identity  # noqa: PLC0415

        _active_name, tool_call_id = active_tool_identity()
    except Exception:  # pragma: no cover - direct MCP calls have no graph context
        tool_call_id = ""
''',
    '''    try:
        from ..core.execution_progress import active_tool_identity  # noqa: PLC0415
        from ..core.middleware.progress_emit import ACTIVE_RUN_ID  # noqa: PLC0415

        _active_name, tool_call_id = active_tool_identity()
        trusted_run_id = str(ACTIVE_RUN_ID.get() or "")
    except Exception:  # pragma: no cover - direct MCP calls have no graph context
        tool_call_id = ""
        trusted_run_id = ""
''',
)
# Exactly two run_id associations inside _submit_command_job: audit + JOBS.
main_text = read("munin/mcp/main.py")
function_start = main_text.index("def _submit_command_job(")
function_end = main_text.index("\n\ndef _run_command(", function_start)
function = main_text[function_start:function_end]
if function.count("run_id=run_id,") != 2:
    raise RuntimeError("munin/mcp/main.py: unexpected _submit_command_job run_id uses")
function = function.replace("run_id=run_id,", "run_id=trusted_run_id,")
write("munin/mcp/main.py", main_text[:function_start] + function + main_text[function_end:])

# ---------------------------------------------------------------------------
# Durable replay tail: compact command output by both row count and bytes.
# ---------------------------------------------------------------------------
replace(
    "munin/production/store.py",
    '_SESSION_TOUCH_INTERVAL_MS: int = int(os.environ.get("MUNIN_SESSION_TOUCH_INTERVAL_SECONDS", "120")) * 1000\n',
    '''_SESSION_TOUCH_INTERVAL_MS: int = int(os.environ.get("MUNIN_SESSION_TOUCH_INTERVAL_SECONDS", "120")) * 1000
_MAX_DURABLE_TOOL_OUTPUT_EVENTS = max(
    16, int(os.environ.get("MUNIN_MAX_DURABLE_TOOL_OUTPUT_EVENTS", "256"))
)
_MAX_DURABLE_TOOL_OUTPUT_BYTES = max(
    65_536, int(os.environ.get("MUNIN_MAX_DURABLE_TOOL_OUTPUT_BYTES", str(512 * 1024)))
)
''',
)
replace(
    "munin/production/store.py",
    '''        with self._transaction() as conn:
            if not conn.execute("SELECT 1 FROM agent_runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError(run_id)
            return self._append_event(conn, run_id=run_id, kind="tool.output", payload=payload)
''',
    '''        with self._transaction() as conn:
            if not conn.execute("SELECT 1 FROM agent_runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError(run_id)
            event = self._append_event(conn, run_id=run_id, kind="tool.output", payload=payload)
            # Keep only the newest bounded tail across all command-output
            # events in this run. Sequence gaps are valid: replay uses a
            # monotonic `sequence > cursor` query and does not require density.
            rows = conn.execute(
                "SELECT id,payload_json FROM run_events "
                "WHERE run_id=? AND kind='tool.output' ORDER BY sequence DESC",
                (run_id,),
            ).fetchall()
            retained_events = 0
            retained_bytes = 0
            stale_ids: list[str] = []
            for row in rows:
                payload_size = len(str(row["payload_json"]).encode("utf-8"))
                if (
                    retained_events < _MAX_DURABLE_TOOL_OUTPUT_EVENTS
                    and retained_bytes + payload_size <= _MAX_DURABLE_TOOL_OUTPUT_BYTES
                ):
                    retained_events += 1
                    retained_bytes += payload_size
                else:
                    stale_ids.append(str(row["id"]))
            if stale_ids:
                conn.executemany(
                    "DELETE FROM run_events WHERE id=?",
                    [(event_id,) for event_id in stale_ids],
                )
            return event
''',
)

# ---------------------------------------------------------------------------
# Middleware and skill validation.
# ---------------------------------------------------------------------------
replace(
    "munin/core/middleware/progress_emit.py",
    '''    if isinstance(value, str):
        return value[:8_000]
''',
    '''    value = _deep_redact(value)
    if isinstance(value, str):
        return value[:8_000]
''',
)
replace(
    "munin/core/autonomy/skill_library.py",
    '''        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.lstrip().startswith("name:"):
                value = line.split(":", 1)[1].strip()
                return value.strip("\\\"'") or None
        return None
''',
    '''        declared: str | None = None
        closed = False
        for line in lines[1:]:
            if line.strip() == "---":
                closed = True
                break
            if line.startswith("name:"):
                value = line[len("name:") :].strip()
                declared = value.strip("\\\"'") or None
        return declared if closed else None
''',
)

# ---------------------------------------------------------------------------
# Artifact inline security.
# ---------------------------------------------------------------------------
replace(
    "munin/production/asgi.py",
    "    async def artifact(request: Request) -> Response:\n",
    '''    inline_artifact_media_types = {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/csv",
        "text/markdown",
        "application/json",
    }

    async def artifact(request: Request) -> Response:
''',
)
replace(
    "munin/production/asgi.py",
    '''            if request.query_params.get("download") == "true" or request.query_params.get("inline") == "true":
                disposition = "attachment" if request.query_params.get("download") == "true" else "inline"
                return Response(result["content"], media_type=result["media_type"], headers={"Content-Disposition": f"{disposition}; filename={result['filename']}"})
''',
    '''            download_requested = request.query_params.get("download") == "true"
            inline_requested = request.query_params.get("inline") == "true"
            if download_requested or inline_requested:
                media_type = str(result["media_type"] or "application/octet-stream")
                base_media_type = media_type.split(";", 1)[0].strip().lower()
                disposition = (
                    "inline"
                    if inline_requested
                    and not download_requested
                    and base_media_type in inline_artifact_media_types
                    else "attachment"
                )
                filename = str(result["filename"]).replace('"', "")
                return Response(
                    result["content"],
                    media_type=media_type,
                    headers={
                        "Content-Disposition": f'{disposition}; filename="{filename}"',
                        "X-Content-Type-Options": "nosniff",
                    },
                )
''',
)

# ---------------------------------------------------------------------------
# Frontend correctness, privacy, accessibility, and performance.
# ---------------------------------------------------------------------------
replace(
    "app/src/components/AgentConsole.tsx",
    '''  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
''',
    '''  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const savingTitle = useRef(false);
''',
)
replace(
    "app/src/components/AgentConsole.tsx",
    '''  async function saveTitle() {
    const nextTitle = draftTitle.trim();
    if (!nextTitle || nextTitle === title) {
      setEditingTitle(false);
      return;
    }
    await onRename(nextTitle);
    setEditingTitle(false);
  }
''',
    '''  async function saveTitle() {
    if (savingTitle.current) return;
    const nextTitle = draftTitle.trim();
    if (!nextTitle || nextTitle === title) {
      setEditingTitle(false);
      return;
    }
    savingTitle.current = true;
    try {
      await onRename(nextTitle);
    } catch {
      // The parent already reports the API failure to the operator.
    } finally {
      savingTitle.current = false;
      setEditingTitle(false);
    }
  }
''',
)
replace(
    "app/src/components/AgentConsole.tsx",
    '''      anchor.click();
      URL.revokeObjectURL(href);
''',
    '''      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 0);
''',
)
replace(
    "app/src/components/chat/blocks/parts/ArtifactPart.tsx",
    '<img src={safeUri || previewUri} alt={filename} className="max-h-96 max-w-full object-contain" />',
    '<img src={previewUri} alt={filename} className="max-h-96 max-w-full object-contain" />',
)
replace(
    "app/src/components/chat/blocks/parts/ToolHeartbeatPart.tsx",
    '''      role="status"
      aria-live="polite"
''',
    '''      role="status"
      aria-live="off"
      aria-label={`${toolName} ${text}`}
''',
)
replace(
    "app/src/components/chat/blocks/parts/ToolHeartbeatPart.tsx",
    '<span className="ml-auto font-mono text-[0.65rem] text-warning/70">',
    '<span aria-hidden className="ml-auto font-mono text-[0.65rem] text-warning/70">',
)
replace(
    "app/src/lib/aiChat.ts",
    '''  useEffect(() => {
    console.debug("[Munin stream]", {
      conversationId,
      status: chat.status,
      messageCount: chat.messages.length,
      partTypes: chat.messages.flatMap((message) => message.parts.map((part) => part.type)),
      error: chat.error?.message ?? null,
    });
  }, [chat.error, chat.messages, chat.status, conversationId]);
''',
    '''  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    console.debug("[Munin stream]", {
      conversationId,
      status: chat.status,
      messageCount: chat.messages.length,
      partCount: chat.messages.reduce((total, message) => total + message.parts.length, 0),
      error: chat.error?.message ?? null,
    });
  }, [chat.error, chat.messages.length, chat.status, conversationId]);
''',
)
replace(
    "app/src/lib/chat/translator.ts",
    "      const max = Math.min(emitted.length, content.length);\n",
    '''      // Bound the duplicate-tail search so a long final answer cannot
      // turn this request-path reconciliation into quadratic work.
      const max = Math.min(emitted.length, content.length, 4096);
''',
)
replace(
    "app/src/lib/chat/translator.ts",
    '''          },
          transient: true,
        }];
''',
    '''          },
        }];
''',
)
replace(
    "app/src/lib/__tests__/translator.test.ts",
    'it("keeps quiet-command heartbeats visible and transient", () => {',
    'it("keeps quiet-command heartbeats visible in message parts", () => {',
)
replace(
    "app/src/lib/__tests__/translator.test.ts",
    '''        },
        transient: true,
      },
''',
    '''        },
      },
''',
)

# ---------------------------------------------------------------------------
# Focused regression tests for the reviewed failure modes.
# ---------------------------------------------------------------------------
append_once(
    "tests/test_live_process_output.py",
    "def test_partial_newline_free_output_is_streamed",
    r'''
def test_partial_newline_free_output_is_streamed(tmp_path):
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))
    code = "import sys,time; sys.stdout.write('working'); sys.stdout.flush(); time.sleep(0.2)"
    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=subprocess.list2cmdline([sys.executable, "-c", code]),
        timeout=10,
    )
    chunks = [event["text"] for event in events if event.get("kind") == "tool_output"]
    assert result["ok"] is True
    assert "working" in "".join(chunks)


def test_long_newline_free_output_is_not_truncated(tmp_path, monkeypatch):
    import munin.mcp.opsec as opsec

    monkeypatch.setattr(opsec, "_PROCESS_OUTPUT_CHUNK_CHARS", 256)
    payload = "x" * 4_000
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))
    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=subprocess.list2cmdline([sys.executable, "-c", f"print({payload!r}, end='')"]),
        timeout=10,
    )
    streamed = "".join(
        event["text"] for event in events if event.get("kind") == "tool_output"
    )
    assert result["ok"] is True
    assert streamed == payload
    assert result["data"]["stdout"] == payload


def test_live_output_is_redacted_before_progress_emission(tmp_path):
    events: list[dict] = []
    job = SimpleNamespace(
        cancel_requested=False,
        process_handle=None,
        process_pid=0,
        progress_sink=events.append,
    )
    engine = ExecutionEngine(_settings(tmp_path))
    secret = "sk-" + "a" * 40
    result = engine.execute_job(
        job=job,
        tool="execute_command",
        level="active",
        command=subprocess.list2cmdline([sys.executable, "-c", f"print({secret!r})"]),
        timeout=10,
    )
    rendered = "".join(
        event.get("text", "") for event in events if event.get("kind") == "tool_output"
    )
    assert secret not in rendered
    assert secret not in result["data"]["stdout"]
    assert "REDACTED" in rendered


def test_job_manager_reserves_cursor_metadata_and_keeps_unread_events(monkeypatch):
    import munin.mcp.jobs as jobs

    monkeypatch.setattr(jobs, "MAX_PENDING_PROGRESS_EVENTS", 512)
    manager = jobs.JobManager(workers=1)
    try:
        job = manager.submit(
            tool="execute_command",
            level="active",
            target="localhost",
            command_preview="echo",
            run_id="run-cursor",
            tool_call_id="call-cursor",
            fn=lambda _job: {"ok": True},
        )
        for value in range(250):
            manager.add_progress(
                job.job_id,
                {
                    "kind": "tool_output",
                    "text": str(value),
                    "sequence": 1,
                    "run_id": "attacker-run",
                    "job_id": "attacker-job",
                },
            )
        cursors: dict[str, int] = {}
        events = manager.progress_for_run("run-cursor", cursors)
        assert len(events) == 250
        assert [event["sequence"] for event in events] == list(range(1, 251))
        assert all(event["run_id"] == "run-cursor" for event in events)
        assert all(event["job_id"] == job.job_id for event in events)
        assert all(event["source_sequence"] == 1 for event in events)
        assert manager.progress_for_run("run-cursor", cursors) == []
    finally:
        manager.shutdown()


def test_durable_tool_output_is_compacted_to_a_bounded_tail(tmp_path, monkeypatch):
    pytest.importorskip("argon2")
    import munin.production.store as store_module
    from munin.production.store import ProductionStore

    monkeypatch.setattr(store_module, "_MAX_DURABLE_TOOL_OUTPUT_EVENTS", 16)
    monkeypatch.setattr(store_module, "_MAX_DURABLE_TOOL_OUTPUT_BYTES", 65_536)
    store = ProductionStore.for_sqlite(tmp_path / "bounded-output.sqlite", master_key=b"b" * 32)
    operator = store.create_user(username="bounded-output", password="a strong bounded password", role="operator")
    conversation = store.create_conversation(owner_id=operator["id"], title="Bounded output")
    turn = store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="stream output",
        idempotency_key="bounded-output",
    )
    run_id = turn["run"]["id"]
    for sequence in range(40):
        store.append_tool_output_event(
            run_id=run_id,
            tool_name="execute_command",
            tool_call_id="call-bounded",
            job_id="job-bounded",
            stream="stdout",
            text=f"line-{sequence}",
            sequence=sequence,
        )
    output_events = [event for event in store.list_run_events(run_id) if event["kind"] == "tool.output"]
    assert len(output_events) == 16
    assert [event["payload"]["text"] for event in output_events] == [
        f"line-{sequence}" for sequence in range(24, 40)
    ]
''',
)
append_once(
    "tests/test_deepagents_skills.py",
    "def test_skill_frontmatter_requires_top_level_name_and_closing_delimiter",
    r'''
def test_skill_frontmatter_requires_top_level_name_and_closing_delimiter(tmp_path):
    from munin.core.autonomy.skill_library import SkillLibrary

    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "SKILL.md").write_text("---\n  name: nested\n---\n", encoding="utf-8")
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "SKILL.md").write_text("---\nname: incomplete\n", encoding="utf-8")
    valid = tmp_path / "valid"
    valid.mkdir()
    (valid / "SKILL.md").write_text("---\nname: valid\n---\n", encoding="utf-8")

    library = SkillLibrary(tmp_path)
    assert library.available() == ("valid",)
    errors = library.validation_errors()
    assert any(error.startswith("nested:") for error in errors)
    assert any(error.startswith("incomplete:") for error in errors)
''',
)
append_once(
    "tests/characterization/test_progress_emit_middleware.py",
    "def test_render_tool_result_redacts_tool_message_content_after_extraction",
    r'''
def test_render_tool_result_redacts_tool_message_content_after_extraction():
    from types import SimpleNamespace

    from munin.core.middleware.progress_emit import _render_tool_result

    secret = "sk-" + "z" * 40
    rendered = _render_tool_result(SimpleNamespace(content=f"api_key={secret}", artifact=None))
    assert secret not in rendered
    assert "REDACTED" in rendered
''',
)

print("Applied PR #13 review fixes")
