"""The six reduction tools. Each queries the lab, reduces, and returns a small dict.

Shared by the MCP server (the agent), the agent runner (the first prompt) and the
console. Every result stays under BUDGET tokens and carries a `counts` block
where it scanned data, so the screen can show how much was reduced.
"""
from __future__ import annotations

import time
from pathlib import Path

from noc import clients, livemap
from noc.reduce import NO_DATA, Handles, approx_tokens, count_templates, fit, movers, pattern_diff, template
from noc.servicemap import ServiceMap, compare

BUDGET = 3500  # tokens per tool result, well under Claude Code's 10k warning
BASELINE = clients.ROOT / "baselines" / "map-latest.json"
GITOPS = clients.ROOT / "state" / "gitops"
_handles = Handles()


def _ago(t: float) -> str:
    return time.strftime("%H:%M:%S UTC", time.gmtime(t))


# ---- incident window ---------------------------------------------------------------

def _active_alerts() -> list[dict]:
    out = []
    for a in clients.grafana_alerts():
        labels = a.get("labels", {})
        if labels.get("alertname", "").startswith("DatasourceNoData"):
            continue
        out.append({"alert": labels.get("alertname", "?"), "service": labels.get("service", "unknown"),
                    "severity": labels.get("severity", "warning"),
                    "started": clients._parse_ts(a.get("startsAt")), "summary": a.get("annotations", {}).get("summary", "")})
    return out


def incident_start(default_window_s: int = 900) -> float:
    alerts = _active_alerts()
    if alerts:
        return min(a["started"] for a in alerts) - 120  # alerts fire after a 1-2 min evaluation lag
    return time.time() - default_window_s


def _windows(window_minutes: int) -> tuple[tuple[float, float], tuple[float, float]]:
    """(baseline, incident) windows of equal length. Baseline ends 2 min before the incident starts."""
    now = time.time()
    w = window_minutes * 60
    start = min(incident_start(w), now - 60)
    incident = (max(start, now - w), now)
    length = incident[1] - incident[0]
    base_end = incident[0] - 120
    return (base_end - length, base_end), incident


# ---- 1. incident_summary --------------------------------------------------------------

def incident_summary(window_minutes: int = 30) -> dict:
    alerts = [a for a in _active_alerts() if a["started"] >= time.time() - window_minutes * 60]
    if not alerts:
        return {"incidents": [], "message": "No alerts are firing in the last "
                f"{window_minutes} minutes.", "counts": {"alerts": 0}}
    alerts.sort(key=lambda a: a["started"])
    groups: list[list[dict]] = [[alerts[0]]]
    for a in alerts[1:]:  # alerts within 10 minutes of the previous one join the same incident
        if a["started"] - groups[-1][-1]["started"] <= 600:
            groups[-1].append(a)
        else:
            groups.append([a])
    incidents = []
    for i, g in enumerate(groups, 1):
        by_service: dict[str, dict] = {}
        for a in g:
            s = by_service.setdefault(a["service"], {"service": a["service"], "alerts": [], "first_fired": a["started"]})
            s["alerts"].append(a["alert"])
            s["first_fired"] = min(s["first_fired"], a["started"])
        services = sorted(by_service.values(), key=lambda s: s["first_fired"])
        for s in services:
            s["first_fired"] = _ago(s["first_fired"])
            s["alerts"] = sorted(set(s["alerts"]))
        incidents.append({"id": f"INC-{i}", "alert_count": len(g), "service_count": len(services),
                          "first_fired": _ago(g[0]["started"]), "services": services})
    return {"incidents": incidents, "counts": {"alerts": len(alerts)},
            "note": "Services are listed in the order their first alert fired. Firing order is a clue, not a verdict."}


# ---- 2. service_map ---------------------------------------------------------------------

def load_baseline() -> ServiceMap | None:
    return ServiceMap.from_json(BASELINE.read_text()) if BASELINE.exists() else None


def _spans_in_window(window_minutes: int) -> int:
    try:
        r = clients.prom_query(f"sum(increase(traces_spanmetrics_calls_total[{window_minutes}m]))")
        return int(float(r[0]["value"][1])) if r else 0
    except clients.QueryError:
        return 0


def service_map(compare_to_baseline: bool = True, window_minutes: int = 5) -> dict:
    now = livemap.build_map(window_minutes * 60)
    base = load_baseline() if compare_to_baseline else None
    edges = []
    silent = []
    changed = 0
    if base:
        diff = compare(base, now)
        changed = len(diff.silent) + len(diff.changed) + len(diff.new)
        silent = [{"edge": e.sentence(), "baseline_rate": e.rate, "grade": "silent",
                   "meaning": "active in the healthy baseline, no traffic now"} for e in diff.silent]
    for e in now.edges:
        row = {"edge": e.sentence(), "grade": e.grade, "kind": e.kind,
               "rate_rps": e.rate, "error_rate": e.error_rate, "p95_ms": e.p95_ms}
        if base and (b := base.edge(e.caller, e.callee)):
            row["baseline"] = {"rate_rps": b.rate, "error_rate": b.error_rate, "p95_ms": b.p95_ms}
        if e.grade == "unexplained":
            row["evidence"] = e.evidence
        edges.append(row)
    edges.sort(key=lambda r: (-(r["error_rate"] or 0), r["edge"]))
    kept, note = fit(edges, BUDGET - 600, "call service_map again later or ask about one service with metric_movers")
    out = {"window": f"last {window_minutes} min", "edges": kept, "silent_edges": silent,
           "how_to_read": "'A calls B' means A depends on B. Errors in B surface as errors in A.",
           "uninstrumented": sorted(n for n, v in now.nodes.items() if not v.get("instrumented")),
           "unlabelled": sorted(n for n, v in now.nodes.items() if not v.get("labelled") and v.get("kind") != "virtual"),
           # records summarised = every span the window's span metrics were built from
           "counts": {"scanned_requests": _spans_in_window(window_minutes),
                      "edges": len(now.edges), "changed_edges": changed}}
    if not base:
        out["baseline"] = "missing: no healthy baseline saved, so silent edges cannot be detected"
    if note:
        out["truncated"] = note
    return out


# ---- 3. what_changed ---------------------------------------------------------------------

def what_changed(minutes_before_first_alert: int = 30) -> dict:
    since = incident_start() - minutes_before_first_alert * 60
    span_s = time.time() - since
    commits = []
    if (GITOPS / ".git").exists():
        commits = [c for c in clients.git_log(span_s, repo=GITOPS) if c["time"] >= since]
        for c in commits:
            c["time"] = _ago(c["time"])
            c["diff"] = clients.git_show_stat(c["commit"], repo=GITOPS)
    events = [e for e in clients.kube_events(span_s)
              if e["reason"] not in ("Pulling", "Pulled", "Created", "Started", "Scheduled", "SuccessfulCreate")
              or e["kind"] in ("Deployment", "ReplicaSet")]
    for e in events:
        e["time"] = _ago(e["time"])
    rollouts, r_note = fit(clients.recent_rollouts(since), 700, "narrow minutes_before_first_alert")
    ev_kept, note = fit(events, 1500, "narrow minutes_before_first_alert")
    note = note or r_note
    out = {"since": _ago(since), "commits": commits, "rollouts": rollouts, "kubernetes_events": ev_kept,
           "summary": f"{len(commits)} commits, {len(rollouts)} rollouts, {len(events)} notable Kubernetes events",
           "counts": {"scanned_events": len(commits) + len(rollouts) + len(events)}}
    if note:
        out["truncated"] = note
    return out


# ---- 4. metric_movers ----------------------------------------------------------------------

def _signals(services: list[str]) -> dict[str, str]:
    sel = "|".join(services)
    return {
        "request rate": f'sum by (service)(rate(traces_spanmetrics_calls_total{{service=~"{sel}",span_kind="SPAN_KIND_SERVER"}}[1m]))',
        "error rate": f'sum by (service)(rate(traces_spanmetrics_calls_total{{service=~"{sel}",span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER",status_code="STATUS_CODE_ERROR"}}[1m]))'
                      f' / sum by (service)(rate(traces_spanmetrics_calls_total{{service=~"{sel}",span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"}}[1m]))',
        "p95 latency ms": f'1000 * histogram_quantile(0.95, sum by (service, le)(rate(traces_spanmetrics_latency_bucket{{service=~"{sel}",span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"}}[1m])))',
        "client error rate": f'sum by (service)(rate(traces_spanmetrics_calls_total{{service=~"{sel}",span_kind=~"SPAN_KIND_CLIENT|SPAN_KIND_PRODUCER",status_code="STATUS_CODE_ERROR"}}[1m]))',
        "restarts": f'sum by (service)(label_replace(k8s_container_restarts{{k8s_namespace_name="otel-demo"}}, "service", "$1", "k8s_container_name", "({sel})"))',
        "memory MiB": f'sum by (service)(label_replace(container_memory_working_set_bytes{{k8s_namespace_name="otel-demo"}}, "service", "$1", "k8s_container_name", "({sel})")) / 1048576',
        "consumer lag": 'sum by (service)(label_replace(kafka_consumer_group_lag_sum_ratio, "service", "$1", "group", "(.*)"))',
    }


def metric_movers(services: list[str] | None = None, window_minutes: int = 15) -> dict:
    if not services:
        services = sorted({n for e in livemap.build_map(300).edges for n in (e.caller, e.callee)})
    (b0, b1), (i0, i1) = _windows(window_minutes)
    series: dict[str, list[tuple[float, float]]] = {}
    baseline: dict[str, float] = {}
    scanned = 0
    for label, expr in _signals(services).items():
        try:
            res = clients.prom_range(expr, b0, i1, step=15)
        except clients.QueryError:
            continue
        for s in res:
            svc = s["metric"].get("service") or "?"
            name = f"{svc} · {label}"
            pts = [(float(t), float(v)) for t, v in s["values"] if v not in ("NaN", "+Inf", "-Inf")]
            scanned += len(pts)
            base_pts = [v for t, v in pts if b0 <= t <= b1]
            inc_pts = [(t, v) for t, v in pts if t >= i0]
            if base_pts:
                baseline[name] = sum(base_pts) / len(base_pts)
            elif inc_pts and max(v for _, v in inc_pts) > 0:
                baseline[name] = 0.0  # signal appeared from nothing
            series[name] = inc_pts
    moved = movers(series, baseline, rel=0.5, abs_floor=0.02)
    rows = [{"signal": m.signal, "change": m.kind, "baseline": round(m.baseline, 3),
             "latest": None if m.latest is None else round(m.latest, 3),
             "first_moved": _ago(m.first_moved_at) if m.first_moved_at else None} for m in moved]
    kept, note = fit(rows, BUDGET - 400, "pass fewer services")
    out = {"baseline_window": f"{_ago(b0)}–{_ago(b1)}", "incident_window": f"{_ago(i0)}–{_ago(i1)}",
           "movers": kept, "counts": {"scanned_series_points": scanned, "signals": len(series), "moved": len(moved)}}
    if not series:
        out["message"] = NO_DATA
    elif not moved:
        out["message"] = "No signal moved beyond 50% of its baseline."
    if note:
        out["truncated"] = note
    return out


# ---- 5. log_pattern_diff --------------------------------------------------------------------

LINE_CAP = 5000  # lines templated per window; counts above this are sampled, and the result says so


def log_pattern_diff(service: str, window_minutes: int = 15) -> dict:
    (b0, b1), (i0, i1) = _windows(window_minutes)
    sel = f'{{service_name="{service}"}}'
    n_base, n_inc = int(clients.loki_count(sel, b0, b1)), int(clients.loki_count(sel, i0, i1))
    base_lines = clients.loki_lines(sel, b0, b1, limit=LINE_CAP)
    inc_lines = clients.loki_lines(sel, i0, i1, limit=LINE_CAP)
    base, now = count_templates(base_lines), count_templates(inc_lines)
    # Scale sampled windows so the two counts stay comparable.
    if base_lines and n_base > len(base_lines):
        base = {k: v * n_base / len(base_lines) for k, v in base.items()}
    if inc_lines and n_inc > len(inc_lines):
        now = {k: v * n_inc / len(inc_lines) for k, v in now.items()}
    changes = pattern_diff(base, now)
    examples: dict[str, list[str]] = {}
    for ln in inc_lines:
        t = template(ln)
        if len(examples.setdefault(t, [])) < 3:
            examples[t].append(ln[:300])
    rows = []
    for c in changes[:8]:
        row = {"pattern": c.pattern, "status": c.status, "baseline_count": int(round(c.baseline_count)),
               "incident_count": int(round(c.incident_count))}
        if c.status != "vanished" and len(rows) < 4:
            row["examples"] = examples.get(c.pattern, [])
        rows.append(row)
    kept, note = fit(rows, BUDGET - 300, "ask for a narrower window_minutes")
    out = {"service": service, "baseline_window": f"{_ago(b0)}–{_ago(b1)}",
           "incident_window": f"{_ago(i0)}–{_ago(i1)}", "changes": kept,
           "counts": {"scanned_log_lines": n_base + n_inc, "patterns": len(set(base) | set(now)),
                      "anomalous": len(changes), "returned": len(kept)}}
    if n_base > LINE_CAP or n_inc > LINE_CAP:
        out["sampling"] = f"templated the most recent {LINE_CAP} lines per window; counts scaled to the full volume"
    if not base_lines and not inc_lines:
        out["message"] = NO_DATA
    elif not changes:
        out["message"] = "Log patterns match the baseline: nothing new, spiking or vanished."
    if note:
        out["truncated"] = note
    return out


# ---- 6. trace_examples ------------------------------------------------------------------------

def _span_rows(trace: dict) -> list[dict]:
    rows = []
    for batch in trace.get("trace", trace).get("resourceSpans", []):
        svc = next((a["value"].get("stringValue") for a in batch.get("resource", {}).get("attributes", [])
                    if a["key"] == "service.name"), "?")
        for ss in batch.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                start, end = int(sp.get("startTimeUnixNano", 0)), int(sp.get("endTimeUnixNano", 0))
                status = sp.get("status", {})
                rows.append({"service": svc, "span": sp.get("name", "")[:80],
                             "kind": sp.get("kind", "").replace("SPAN_KIND_", "").lower(),
                             "ms": round((end - start) / 1e6, 1), "error": status.get("code") == "STATUS_CODE_ERROR",
                             "message": (status.get("message") or "")[:160], "start": start,
                             "id": sp.get("spanId"), "parent": sp.get("parentSpanId")})
    rows.sort(key=lambda r: r["start"])
    return rows


def trace_examples(service: str, only_errors: bool = True, limit: int = 3) -> dict:
    (_, _), (i0, i1) = _windows(15)
    q = f'{{resource.service.name="{service}"' + (' && status=error}' if only_errors else '}')
    found = clients.tempo_search(q, i0, i1, limit=min(limit, 3))
    examples = []
    for t in found:
        rows = _span_rows(clients.tempo_trace(t["traceID"]))
        errs = [r for r in rows if r["error"]]
        focus = errs or [r for r in rows if r["service"] == service][:5]
        keep = (rows[:4] + focus[:6]) if len(rows) > 10 else rows
        seen, chain = set(), []
        for r in keep:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            chain.append({k: r[k] for k in ("service", "span", "kind", "ms", "error", "message") if r[k] not in ("", None)})
        examples.append({"trace": _handles.short(t["traceID"]), "root": f'{t.get("rootServiceName")}: {t.get("rootTraceName")}',
                         "duration_ms": t.get("durationMs"), "spans_total": len(rows), "spans": chain})
    kept, note = fit(examples, BUDGET - 200, "use limit=1")
    out = {"service": service, "query": q, "examples": kept, "counts": {"scanned_traces": len(found)}}
    if not found:
        out["message"] = (f"No {'error ' if only_errors else ''}traces for {service} in the incident window. "
                          "Absence of error traces is not proof of health: the service may not be traced, "
                          "or its errors may be recorded by its callers.")
    if note:
        out["truncated"] = note
    assert approx_tokens(out) < 9000
    return out
