"""The console's map: the healthy baseline overlaid with the live map, edge by edge."""
from __future__ import annotations

import json
import time

from noc import clients, livemap, tools
from noc.servicemap import compare


def payload(window_s: int = 300) -> dict:
    base = tools.load_baseline()
    now = livemap.build_map(window_s)
    report_path = clients.ROOT / "baselines" / "map-report-latest.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    diff = compare(base, now) if base else None
    edges = []
    # Edges come from the baseline so a silent edge keeps its place; the live map adds new ones.
    for e in (base.edges if base else now.edges):
        n = now.edge(e.caller, e.callee)
        edges.append({"caller": e.caller, "callee": e.callee, "kind": e.kind, "sentence": e.sentence(),
                      "grade": e.grade, "sources": sorted(e.sources), "evidence": e.evidence,
                      "baseline": {"rate": e.rate, "error_rate": e.error_rate, "p95_ms": e.p95_ms},
                      "now": None if n is None else {"rate": n.rate, "error_rate": n.error_rate, "p95_ms": n.p95_ms},
                      "silent": bool(diff and diff.merged_grade(e.caller, e.callee) == "silent")})
    if base:
        for e in now.edges:
            if base.edge(e.caller, e.callee) is None:
                edges.append({"caller": e.caller, "callee": e.callee, "kind": e.kind, "sentence": e.sentence(),
                              "grade": e.grade, "sources": sorted(e.sources), "evidence": e.evidence, "new": True,
                              "baseline": None, "now": {"rate": e.rate, "error_rate": e.error_rate, "p95_ms": e.p95_ms},
                              "silent": False})
    nodes = {**(base.nodes if base else {}), **now.nodes}
    return {"edges": edges, "nodes": nodes, "report": report,
            "baseline_captured_at": base.captured_at if base else None, "now_at": now.captured_at}


def alerts() -> dict:
    items = sorted(tools._active_alerts(), key=lambda a: a["started"])
    return {"alerts": items, "count": len(items), "services": sorted({a["service"] for a in items}), "at": time.time()}
