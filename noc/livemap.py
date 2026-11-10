"""Build the service map from the live lab: Tempo service graph + OBI flows + Kubernetes."""
from __future__ import annotations

import time

from noc import clients
from noc.servicemap import Flow, ServiceMap, TraceEdge, Workload, reconcile

APP_NAMESPACES = ("otel-demo", "default")
# Telemetry plumbing and the fault machinery: not part of the estate's dependency map.
INFRA = {"otel-collector", "otel-collector-agent", "lgtm", "opamp-server", "coredns", "flagd",
         "blast-radius-control-plane", "image-provider", "load-generator", "user", "frontend-web"}
# Tempo names some virtual nodes from span attributes; map them to the workload.
ALIASES = {"postgresql": "astronomy-db", "postgres": "astronomy-db", "otel": "astronomy-db",
           "valkey": "valkey-cart", "redis": "valkey-cart", "kafka:9092": "kafka",
           "flagd:8013": "flagd", "flagd:8016": "flagd"}


def canon(name: str) -> str:
    return ALIASES.get(name, name)


def trace_edges(window_s: int, at: float | None = None) -> list[TraceEdge]:
    w = f"{max(window_s, 60)}s"
    req = clients.prom_query(f"sum by (client, server, connection_type)"
                             f"(rate(traces_service_graph_request_total[{w}]))", at)
    failed = {(s["metric"].get("client"), s["metric"].get("server")): float(s["value"][1]) for s in
              clients.prom_query(f"sum by (client, server)(rate(traces_service_graph_request_failed_total[{w}]))", at)}
    p95 = {(s["metric"].get("client"), s["metric"].get("server")): float(s["value"][1]) for s in
           clients.prom_query(f"histogram_quantile(0.95, sum by (client, server, le)"
                              f"(rate(traces_service_graph_request_server_seconds_bucket[{w}])))", at)}
    merged: dict[tuple[str, str], dict] = {}
    for s in req:
        m = s["metric"]
        raw = (m.get("client", ""), m.get("server", ""))
        a, b = canon(raw[0]), canon(raw[1])
        rate = float(s["value"][1])
        if not a or not b or a == b or a in INFRA or b in INFRA:
            continue
        kind = {"messaging_system": "messaging", "database": "db"}.get(m.get("connection_type", ""), "sync")
        if b in ("astronomy-db", "valkey-cart"):
            kind = "db"
        e = merged.setdefault((a, b), {"kind": kind, "rate": 0.0, "failed": 0.0, "p95": None})
        e["rate"] += rate
        e["failed"] += failed.get(raw, 0.0)
        p = p95.get(raw)
        if p == p and p is not None:  # not NaN
            e["p95"] = max(e["p95"] or 0.0, p * 1000)
    out = []
    for (a, b), e in merged.items():
        err = e["failed"] / e["rate"] if e["rate"] > 0 else 0.0
        out.append(TraceEdge(a, b, e["kind"], round(e["rate"], 3), round(min(err, 1.0), 4),
                             round(e["p95"], 1) if e["p95"] is not None else None))
    return out


def flows(window_s: int, at: float | None = None) -> list[Flow]:
    w = f"{max(window_s, 60)}s"
    ns = "|".join(APP_NAMESPACES)
    res = clients.prom_query(
        f'sum by (k8s_src_owner_name, k8s_dst_owner_name, direction)(rate(obi_network_flow_bytes_total'
        f'{{k8s_src_namespace=~"{ns}", k8s_dst_namespace=~"{ns}"}}[{w}]))', at)
    out = []
    for s in res:
        m = s["metric"]
        a, b = m.get("k8s_src_owner_name", ""), m.get("k8s_dst_owner_name", "")
        if not a or not b or a in INFRA or b in INFRA:
            continue
        direction = m.get("direction")
        if direction == "unknown":
            continue
        if direction == "response":
            a, b = b, a  # response bytes flow callee -> caller
        rate = float(s["value"][1])
        if rate > 0:
            out.append(Flow(a, b, rate, directed=True))
    return out


def _matches(selector: dict, labels: dict) -> bool:
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


def inventory() -> dict[str, Workload]:
    out: dict[str, Workload] = {}
    for ns in APP_NAMESPACES:
        services = [s["spec"].get("selector") or {} for s in clients.kubectl_json("get", "svc", "-n", ns)["items"]]
        for kind in ("deployment", "statefulset"):
            for d in clients.kubectl_json("get", kind, "-n", ns)["items"]:
                name = d["metadata"]["name"]
                if name in INFRA:
                    continue
                labels = d["spec"]["template"]["metadata"].get("labels", {})
                out[name] = Workload(name, ns, kind.capitalize(), labels, any(_matches(s, labels) for s in services))
        for p in clients.kubectl_json("get", "pod", "-n", ns)["items"]:
            if p["metadata"].get("ownerReferences"):
                continue  # owned pods are covered by their controller
            name = p["metadata"]["name"]
            labels = p["metadata"].get("labels", {})
            out[name] = Workload(name, ns, "Pod", labels, any(_matches(s, labels) for s in services))
    return out


def build_map(window_s: int = 900, at: float | None = None) -> ServiceMap:
    at = at or time.time()
    inv = inventory()
    edges = reconcile(trace_edges(window_s, at), flows(window_s, at), inv)
    nodes = {}
    for e in edges:
        for n in (e.caller, e.callee):
            w = inv.get(n)
            nodes[n] = {"namespace": w.namespace if w else None, "labelled": bool(w and w.labelled),
                        "has_service": bool(w and w.has_service), "kind": w.owner_kind if w else "virtual"}
    for n in nodes:
        nodes[n]["instrumented"] = any("traces" in e.sources and n in (e.caller, e.callee) for e in edges)
    return ServiceMap(edges=edges, captured_at=at, window_s=window_s, nodes=nodes)
