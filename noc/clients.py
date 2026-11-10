"""Thin, typed clients for the lab's query APIs. All read-only.

Every function returns plain data. Errors raise QueryError with a message that
says what to do next, so tool results can pass it straight to the agent.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PROM = os.environ.get("NOC_PROM", "http://127.0.0.1:9090")
LOKI = os.environ.get("NOC_LOKI", "http://127.0.0.1:3100")
TEMPO = os.environ.get("NOC_TEMPO", "http://127.0.0.1:3200")
GRAFANA = os.environ.get("NOC_GRAFANA", "http://127.0.0.1:3000")
KUBECONFIG = os.environ.get("KUBECONFIG", str(Path.home() / ".kube" / "blast-radius.kubeconfig"))

# Namespaces the agent's tools may see. Faults live elsewhere and stay hidden.
VISIBLE_NAMESPACES = ("otel-demo", "default", "observability")
HIDDEN_WORKLOADS = ("flagd",)

_http = httpx.Client(timeout=20.0)


class QueryError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    try:
        r = _http.get(url, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise QueryError(f"{url} unreachable ({e.__class__.__name__}); the backend may be restarting, retry in 30 s")
    if r.status_code >= 400:
        raise QueryError(f"{url} returned {r.status_code}: {r.text[:300]}")
    return r.json()


# ---- Prometheus -------------------------------------------------------------

def prom_query(expr: str, at: float | None = None) -> list[dict]:
    params = {"query": expr}
    if at is not None:
        params["time"] = f"{at:.3f}"
    d = _get(f"{PROM}/api/v1/query", params)
    return d["data"]["result"]


def prom_range(expr: str, start: float, end: float, step: int = 30) -> list[dict]:
    d = _get(f"{PROM}/api/v1/query_range", {"query": expr, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": step})
    return d["data"]["result"]


def prom_scalar_by(expr: str, label: str, at: float | None = None) -> dict[str, float]:
    out = {}
    for s in prom_query(expr, at):
        v = float(s["value"][1])
        if v == v:  # drop NaN
            out[s["metric"].get(label, "")] = v
    return out


# ---- Loki -------------------------------------------------------------------

def loki_patterns(selector: str, start: float, end: float, step: str = "60s") -> list[dict]:
    d = _get(f"{LOKI}/loki/api/v1/patterns", {"query": selector, "start": f"{int(start * 1e9)}",
                                              "end": f"{int(end * 1e9)}", "step": step})
    return d.get("data") or []


def loki_count(selector: str, start: float, end: float) -> float:
    expr = f"sum(count_over_time({selector}[{max(int(end - start), 60)}s]))"
    d = _get(f"{LOKI}/loki/api/v1/query", {"query": expr, "time": f"{int(end * 1e9)}"})
    res = d["data"]["result"]
    return float(res[0]["value"][1]) if res else 0.0


def loki_lines(selector: str, start: float, end: float, limit: int = 3) -> list[str]:
    d = _get(f"{LOKI}/loki/api/v1/query_range", {"query": selector, "start": f"{int(start * 1e9)}",
                                                 "end": f"{int(end * 1e9)}", "limit": limit, "direction": "backward"})
    lines = []
    for stream in d["data"]["result"]:
        for _, line in stream["values"]:
            lines.append(line)
    return lines[:limit]


# ---- Tempo ------------------------------------------------------------------

def tempo_search(traceql: str, start: float, end: float, limit: int = 3) -> list[dict]:
    d = _get(f"{TEMPO}/api/search", {"q": traceql, "start": int(start), "end": int(end), "limit": limit})
    return d.get("traces") or []


def tempo_trace(trace_id: str) -> dict:
    return _get(f"{TEMPO}/api/v2/traces/{trace_id}")


# ---- Grafana ----------------------------------------------------------------

def grafana_token() -> str | None:
    p = ROOT / ".secrets" / "grafana-viewer-token"
    return p.read_text().strip() if p.exists() else None


def grafana_get(path: str, params: dict | None = None) -> dict | list:
    tok = grafana_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else None
    return _get(f"{GRAFANA}{path}", params, headers)


def grafana_alerts() -> list[dict]:
    """Active alert instances from Grafana's built-in Alertmanager."""
    return grafana_get("/api/alertmanager/grafana/api/v2/alerts", {"active": "true", "silenced": "false",
                                                                   "inhibited": "false"})  # type: ignore[return-value]


# ---- Kubernetes ---------------------------------------------------------------

def kubectl_json(*args: str) -> dict:
    cmd = ["kubectl", "--kubeconfig", KUBECONFIG, *args, "-o", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise QueryError(f"kubectl {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout)


def kube_events(since_s: float) -> list[dict]:
    out = []
    cutoff = time.time() - since_s
    for ns in VISIBLE_NAMESPACES:
        for ev in kubectl_json("get", "events", "-n", ns)["items"]:
            ts = ev.get("lastTimestamp") or ev.get("eventTime") or ev["metadata"].get("creationTimestamp")
            t = _parse_ts(ts)
            name = ev["involvedObject"].get("name", "")
            if t >= cutoff and not any(h in name for h in HIDDEN_WORKLOADS):
                out.append({"time": t, "namespace": ns, "kind": ev["involvedObject"].get("kind"),
                            "object": name, "reason": ev.get("reason"), "message": (ev.get("message") or "")[:200],
                            "type": ev.get("type")})
    out.sort(key=lambda e: e["time"])
    return out


def _parse_ts(ts: str | None) -> float:
    if not ts:
        return 0.0
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


# ---- Git (the change record) -------------------------------------------------

def git_log(since_s: float, paths: tuple[str, ...] = ("lab",)) -> list[dict]:
    fmt = "%H%x1f%ct%x1f%an%x1f%s"
    p = subprocess.run(["git", "-C", str(ROOT), "log", f"--since={int(since_s)} seconds ago", f"--format={fmt}",
                        "--name-only", "--", *paths], capture_output=True, text=True, timeout=15)
    commits, cur = [], None
    for line in p.stdout.splitlines():
        if "\x1f" in line:
            h, ct, an, subj = line.split("\x1f")
            cur = {"commit": h[:8], "time": float(ct), "author": an, "subject": subj, "files": []}
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    return commits
