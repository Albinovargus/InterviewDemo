"""Service map: reconcile traces, network flows and Kubernetes inventory into graded edges.

Code does the counting and the grading; Claude only adds reasons and names gaps.
Grades: confirmed (two sources agree), likely (one strong source), unexplained
(traffic nobody can account for), silent (in the baseline, gone now).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

OWNER_LABELS = ("app.kubernetes.io/component", "app.kubernetes.io/name", "app", "team", "owner")
BROKERS = {"kafka"}


@dataclass(frozen=True)
class TraceEdge:
    caller: str
    callee: str
    kind: str  # sync | messaging | db
    rate: float
    error_rate: float
    p95_ms: float | None


@dataclass(frozen=True)
class Flow:
    """One OBI network flow between two workloads (direction unreliable: bytes go both ways)."""
    a: str
    b: str
    bytes_rate: float


@dataclass(frozen=True)
class Workload:
    name: str
    namespace: str
    owner_kind: str
    labels: dict
    has_service: bool

    @property
    def labelled(self) -> bool:
        return any(k in self.labels for k in OWNER_LABELS)


@dataclass
class Edge:
    caller: str
    callee: str
    kind: str  # sync | messaging | db | network
    rate: float = 0.0  # requests/s from traces
    error_rate: float = 0.0  # fraction of failed requests
    p95_ms: float | None = None
    flow_bytes: float = 0.0  # bytes/s from OBI
    sources: set = field(default_factory=set)
    grade: str = "likely"
    evidence: list = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.caller, self.callee)

    def sentence(self) -> str:
        via = " (via Kafka)" if self.kind == "messaging" else ""
        return f"{self.caller} calls {self.callee}{via}"


@dataclass
class EdgeChange:
    caller: str
    callee: str
    rate_before: float
    rate_now: float
    error_rate_before: float
    error_rate_now: float
    p95_before: float | None
    p95_now: float | None


@dataclass
class MapDiff:
    silent: list[Edge]
    new: list[Edge]
    changed: list[EdgeChange]
    now: ServiceMap

    def merged_grade(self, caller: str, callee: str) -> str | None:
        if any(e.key == (caller, callee) for e in self.silent):
            return "silent"
        for e in self.now.edges:
            if e.key == (caller, callee):
                return e.grade
        return None


@dataclass
class ServiceMap:
    edges: list[Edge]
    captured_at: float = 0.0
    window_s: int = 0
    nodes: dict = field(default_factory=dict)  # name -> {namespace, labelled, has_service, instrumented}

    def to_json(self) -> str:
        d = asdict(self)
        for e in d["edges"]:
            e["sources"] = sorted(e["sources"])
        return json.dumps(d, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ServiceMap:
        d = json.loads(text)
        edges = [Edge(**{**e, "sources": set(e["sources"])}) for e in d.pop("edges")]
        return cls(edges=edges, **d)

    def edge(self, caller: str, callee: str) -> Edge | None:
        return next((e for e in self.edges if e.key == (caller, callee)), None)


def _flow_pairs(flows: list[Flow]) -> dict[frozenset, float]:
    pairs: dict[frozenset, float] = {}
    for f in flows:
        if f.a == f.b:
            continue
        k = frozenset((f.a, f.b))
        pairs[k] = pairs.get(k, 0.0) + f.bytes_rate
    return pairs


def _orient(a: str, b: str, inventory: dict[str, Workload]) -> tuple[str, str]:
    """Pick caller/callee for a flow-only pair: the side with a Service is the callee."""
    wa, wb = inventory.get(a), inventory.get(b)
    a_srv = bool(wa and wa.has_service)
    b_srv = bool(wb and wb.has_service)
    if a_srv and not b_srv:
        return b, a
    if b_srv and not a_srv:
        return a, b
    # Both or neither expose a Service: the unlabelled side is the caller; else alphabetical.
    if wa and not wa.labelled:
        return a, b
    if wb and not wb.labelled:
        return b, a
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def reconcile(trace_edges: list[TraceEdge], flows: list[Flow], inventory: dict[str, Workload]) -> list[Edge]:
    pairs = _flow_pairs(flows)
    used: set[frozenset] = set()
    out: list[Edge] = []

    for t in trace_edges:
        e = Edge(t.caller, t.callee, t.kind, rate=t.rate, error_rate=t.error_rate, p95_ms=t.p95_ms,
                 sources={"traces"})
        e.evidence.append(f"traces: {t.rate:.2f} req/s, {t.error_rate:.0%} errors")
        if t.kind == "messaging":
            broker_flows = [frozenset((side, b)) for side in (t.caller, t.callee) for b in BROKERS]
            hits = [p for p in broker_flows if p in pairs]
            if len({next(iter(p - BROKERS), None) for p in hits}) == 2:
                e.sources.add("flows")
                e.flow_bytes = sum(pairs[p] for p in hits)
                e.evidence.append("flows: both sides connect to the Kafka broker")
        else:
            k = frozenset((t.caller, t.callee))
            if k in pairs:
                used.add(k)
                e.sources.add("flows")
                e.flow_bytes = pairs[k]
                e.evidence.append(f"flows: {pairs[k]:.0f} B/s between the pods")
        e.grade = "confirmed" if len(e.sources) >= 2 else "likely"
        out.append(e)

    traced_pairs = {frozenset(e.key) for e in out}
    for k, bytes_rate in pairs.items():
        if k in used or k in traced_pairs:
            continue
        a, b = sorted(k)
        caller, callee = _orient(a, b, inventory)
        e = Edge(caller, callee, "network", flow_bytes=bytes_rate, sources={"flows"})
        e.evidence.append(f"flows: {bytes_rate:.0f} B/s, no traces")
        w = inventory.get(caller)
        if w is None or not w.labelled:
            e.grade = "unexplained"
            e.evidence.append(f"{caller}: no owner label, no traces"
                              + ("" if (w and w.has_service) else ", no Service"))
        else:
            e.grade = "likely"
        out.append(e)

    out.sort(key=lambda e: e.key)
    return out


SILENT_RATE = 0.01  # req/s below which a baseline edge counts as gone


def compare(baseline: ServiceMap, now: ServiceMap) -> MapDiff:
    silent, changed = [], []
    for b in baseline.edges:
        n = now.edge(b.caller, b.callee)
        was_active = b.rate > SILENT_RATE or b.flow_bytes > 0
        is_active = n is not None and (n.rate > SILENT_RATE or n.flow_bytes > 0)
        if was_active and not is_active:
            silent.append(b)
        elif n is not None and (abs(n.error_rate - b.error_rate) >= 0.05
                                or _latency_jump(b.p95_ms, n.p95_ms)
                                or _rate_jump(b.rate, n.rate)):
            changed.append(EdgeChange(b.caller, b.callee, b.rate, n.rate, b.error_rate, n.error_rate,
                                      b.p95_ms, n.p95_ms))
    base_keys = {e.key for e in baseline.edges}
    new = [e for e in now.edges if e.key not in base_keys]
    return MapDiff(silent=silent, new=new, changed=changed, now=now)


def _latency_jump(before: float | None, now: float | None) -> bool:
    return before is not None and now is not None and now > max(2 * before, before + 50)


def _rate_jump(before: float, now: float) -> bool:
    return before > 0.1 and (now < before / 3 or now > before * 3)
