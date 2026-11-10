"""Run the investigating agent: headless Claude Code with two read-only MCP servers.

Every stream-json event is written to runs/<id>.jsonl as {"ts": wall-clock, "raw": event},
so a replay is the same file played back with its original timing.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from noc import tools
from noc.clients import GRAFANA, ROOT, grafana_token

RUNS = ROOT / "runs"
SANDBOX = RUNS / "sandbox"  # empty working dir: no project files, no CLAUDE.md
SYSTEM_PROMPT_FILE = ROOT / "agent" / "system-prompt.md"
GRAFANA_TOOLS = "prometheus,loki,tempo,alerting,datasource"
DISALLOWED = ["Bash", "Read", "Write", "Edit", "MultiEdit", "WebFetch", "WebSearch", "Glob", "Grep", "Task",
              "Agent", "NotebookEdit", "TodoWrite", "Skill", "SlashCommand", "LSP"]

logging.getLogger("httpx").setLevel(logging.WARNING)


def mcp_config() -> Path:
    tok = grafana_token()
    if not tok:
        raise RuntimeError("Grafana viewer token missing: run infra/3-config first")
    cfg = {"mcpServers": {
        "reduction": {"command": "uv", "args": ["run", "--project", str(ROOT), "python", "-m", "noc.mcp_server"]},
        "grafana": {"command": str(ROOT / "bin" / "mcp-grafana.exe"),
                    "args": ["--disable-write", "--enabled-tools", GRAFANA_TOOLS],
                    "env": {"GRAFANA_URL": GRAFANA, "GRAFANA_SERVICE_ACCOUNT_TOKEN": tok}},
    }}
    p = ROOT / ".secrets" / "mcp.json"  # holds the token; gitignored
    p.write_text(json.dumps(cfg, indent=2))
    return p


def first_prompt() -> tuple[str, dict]:
    """The investigation starts from the incident summary and the map, fetched by the same tools."""
    inc = tools.incident_summary()
    smap = tools.service_map()
    prompt = ("An investigation has been requested for the current state of the estate.\n\n"
              f"Incident summary (from incident_summary):\n```json\n{json.dumps(inc, indent=1)}\n```\n\n"
              f"Service map now vs the healthy baseline (from service_map):\n```json\n{json.dumps(smap, indent=1)}\n```\n\n"
              "Investigate and give your answer in the required format.")
    # The console's own view at the same moment, so a replay draws exactly what the live screen showed.
    from noc import mapview
    return prompt, {"incident_summary": inc, "service_map": smap, "map_view": mapview.payload(), "alerts": mapview.alerts()}


@dataclass
class RunHandle:
    id: str
    path: Path
    proc: subprocess.Popen
    started: float
    done: threading.Event = field(default_factory=threading.Event)

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def stop(self) -> None:
        kill_tree(self.proc)


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the agent and its MCP server children (on Windows, kill() alone orphans them)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
    if proc.poll() is None:
        proc.kill()


def run_agent(model: str = "claude-opus-5-5", effort: str = "high", max_turns: int = 40,
              run_id: str | None = None, label: str = "live", prompt_override: str | None = None,
              timeout_s: float = 720) -> RunHandle:
    run_id = run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    RUNS.mkdir(exist_ok=True)
    SANDBOX.mkdir(exist_ok=True)
    path = RUNS / f"{run_id}.jsonl"
    started = time.time()
    prompt, prefetch = first_prompt()
    if prompt_override:
        prompt = prompt_override
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--model", model, "--effort", effort, "--max-turns", str(max_turns),
           "--system-prompt-file", str(SYSTEM_PROMPT_FILE), "--setting-sources", "local",
           "--mcp-config", str(mcp_config()), "--strict-mcp-config", "--tools", "",
           "--allowedTools", "mcp__reduction", "mcp__grafana",
           "--disallowedTools", *DISALLOWED, "--no-session-persistence"]
    f = path.open("w", encoding="utf-8")
    meta = {"ts": started, "meta": {"id": run_id, "model": model, "effort": effort, "max_turns": max_turns,
                                    "label": label, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))}}
    f.write(json.dumps(meta) + "\n")
    f.write(json.dumps({"ts": time.time(), "prefetch": prefetch}) + "\n")
    f.flush()
    try:
        proc = subprocess.Popen(cmd, cwd=SANDBOX, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        proc.stdin.write(prompt)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except Exception:
        f.close()
        raise
    handle = RunHandle(run_id, path, proc, started)
    err_tail: list[str] = []
    killed = threading.Event()

    def drain_stderr() -> None:  # read continuously so a chatty process can never block on a full pipe
        for line in proc.stderr:  # type: ignore[union-attr]
            err_tail.append(line)
            del err_tail[:-40]

    def watchdog() -> None:
        if proc.poll() is None:
            killed.set()
            kill_tree(proc)

    timer = threading.Timer(timeout_s, watchdog)
    timer.daemon = True
    timer.start()

    def pump() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    raw = {"type": "stdout", "text": line[:500]}
                f.write(json.dumps({"ts": time.time(), "raw": raw}) + "\n")
                f.flush()
            proc.wait()
            timer.cancel()
            err_thread.join(5)
            err = "".join(err_tail)[-800:]
            if killed.is_set():
                err = f"watchdog: stopped after {timeout_s:.0f} s. " + err
            f.write(json.dumps({"ts": time.time(), "exit": {"code": proc.returncode, "stderr": err}}) + "\n")
        finally:
            f.close()
            handle.done.set()

    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    err_thread.start()
    threading.Thread(target=pump, daemon=True).start()
    return handle


def read_run(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-haiku-5-5"
    h = run_agent(model=model, effort=sys.argv[2] if len(sys.argv) > 2 else "medium", label="dev")
    print("run", h.id, "->", h.path)
    h.wait()
    from noc.events import Normaliser
    n = Normaliser()
    for rec in read_run(h.path):
        if "raw" in rec:
            for ev in n.feed(rec["raw"], rec["ts"]):
                d = ev.data
                if ev.type == "tool_call":
                    print(f"  [{ev.ts - h.started:5.1f}s] -> {d['server']}.{d['tool']} {json.dumps(d['input'])[:120]}")
                elif ev.type == "tool_result":
                    print(f"  [{ev.ts - h.started:5.1f}s] <- {d['tool']} {len(d['text'])} chars{' ERROR' if d['is_error'] else ''}")
                elif ev.type in ("verdict", "error", "cost", "run_finished", "rate_limit"):
                    print(f"  [{ev.ts - h.started:5.1f}s] {ev.type}: {json.dumps({k: v for k, v in d.items() if k != 'text'})[:600]}")
    for ev in n.close(time.time()):
        print("  ", ev.type, ev.data)
