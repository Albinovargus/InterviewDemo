"""Fault controller: inject and reset the demo's faults.

This module is used only by the console and the rehearsal harness. The agent's
tools never import it and cannot see flagd, the chaos-mesh namespace, or the
truth records it writes.
"""
from __future__ import annotations

import copy
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from noc.clients import KUBECONFIG, ROOT

NS = "otel-demo"
CHAOS_DIR = ROOT / "lab" / "chaos"
GITOPS = ROOT / "state" / "gitops"  # the change record: a separate local repo of manifests
CANARY_FILE = "cart-canary.yaml"
TRUTH_DIR = ROOT / "runs" / "truth"


@dataclass(frozen=True)
class Fault:
    id: str
    title: str
    mechanism: str  # flag | chaos | commit
    truth_service: str
    accept: frozenset
    summary: str
    flag: str | None = None
    variant: str | None = None
    chaos_file: str | None = None


FAULTS: dict[str, Fault] = {f.id: f for f in [
    Fault("payment-failure", "Payment failures", "flag", "payment", frozenset({"payment"}),
          "payment fails a share of charge requests", flag="paymentFailure", variant="50%"),
    Fault("db-lock", "Database lock contention", "flag", "product-catalog",
          frozenset({"product-catalog", "astronomy-db"}),
          "product-catalog holds a PostgreSQL table lock, slowing its own queries",
          flag="productCatalogLockContention", variant="on"),
    Fault("kafka-lag", "Kafka consumer lag", "flag", "fraud-detection",
          frozenset({"fraud-detection", "kafka", "checkout", "accounting"}),
          "the order queue floods while consumers slow down, so consumer lag climbs",
          flag="kafkaQueueProblems", variant="on"),
    Fault("cache-leak", "Cache memory leak", "flag", "recommendation", frozenset({"recommendation"}),
          "recommendation's cache grows without limit until memory runs out",
          flag="recommendationCacheFailure", variant="on"),
    Fault("pg-network-delay", "Network delay to PostgreSQL", "chaos", "astronomy-db",
          frozenset({"astronomy-db"}), "every packet from the PostgreSQL pod is delayed",
          chaos_file="network-delay-postgres.yaml"),
    Fault("dns-failure", "DNS failure for one service", "chaos", "quote",
          frozenset({"quote", "coredns", "kube-dns", "dns"}),
          "DNS lookups of quote fail for its caller; quote itself is healthy",
          chaos_file="dns-quote.yaml"),
    Fault("bad-config", "Bad config on half the pods", "commit", "cart", frozenset({"cart", "cart-canary"}),
          "a commit ships a canary of cart with a broken cache address behind the same Service"),
]}


@dataclass
class LabState:
    flags_on: dict = field(default_factory=dict)  # flag -> variant, for flags not "off"
    chaos_objects: list = field(default_factory=list)
    canary_replicas: int = 0


def set_flag(doc: dict, flag: str, variant: str) -> dict:
    out = copy.deepcopy(doc)
    f = out["flags"][flag]
    if variant not in f["variants"]:
        raise ValueError(f"{flag} has no variant {variant!r}; options: {sorted(f['variants'])}")
    f["defaultVariant"] = variant
    return out


def residue(state: LabState) -> list[str]:
    out = [f"flag {k}={v}" for k, v in sorted(state.flags_on.items())]
    out += [f"chaos {c}" for c in state.chaos_objects]
    if state.canary_replicas:
        out.append(f"canary cart-canary replicas={state.canary_replicas}")
    return out


# ---- cluster plumbing ---------------------------------------------------------------

def _kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    p = subprocess.run(["kubectl", "--kubeconfig", KUBECONFIG, *args], input=stdin, capture_output=True,
                       text=True, timeout=60)
    if check and p.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {p.stderr.strip()[:400]}")
    return p.stdout


def _flagd_pod() -> str:
    return _kubectl("-n", NS, "get", "pod", "-l", "app.kubernetes.io/component=flagd", "-o",
                    "jsonpath={.items[0].metadata.name}").strip()


FLAG_FILE = "/app/data/demo.flagd.json"  # flagd-ui sidecar's view of flagd's file


def read_flags() -> dict:
    return json.loads(_kubectl("-n", NS, "exec", _flagd_pod(), "-c", "flagd-ui", "--", "cat", FLAG_FILE))


def write_flags(doc: dict) -> None:
    body = json.dumps(doc, indent=2)
    _kubectl("-n", NS, "exec", "-i", _flagd_pod(), "-c", "flagd-ui", "--", "sh", "-c",
             f"cat > {FLAG_FILE}.tmp && mv {FLAG_FILE}.tmp {FLAG_FILE}", stdin=body)


def _git(*args: str) -> str:
    p = subprocess.run(["git", "-C", str(GITOPS), *args], capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return p.stdout


def _commit_canary(text: str, message: str) -> None:
    (GITOPS / CANARY_FILE).write_text(text)
    _git("add", CANARY_FILE)
    _git("-c", "user.name=platform-bot", "-c", "user.email=platform-bot@example.invalid", "commit", "-q",
         "-m", message)
    _kubectl("apply", "-f", str(GITOPS / CANARY_FILE))


def chaos_objects() -> list[str]:
    out = []
    for kind in ("networkchaos", "dnschaos", "podchaos", "stresschaos"):
        names = _kubectl("-n", "chaos-mesh", "get", kind, "-o", "name", check=False).split()
        out += names
    return out


def lab_state() -> LabState:
    flags = read_flags()["flags"]
    on = {k: v["defaultVariant"] for k, v in flags.items() if v["defaultVariant"] not in ("off", False)}
    reps = _kubectl("-n", NS, "get", "deploy", "cart-canary", "-o", "jsonpath={.spec.replicas}", check=False)
    return LabState(flags_on=on, chaos_objects=chaos_objects(), canary_replicas=int(reps or 0))


# ---- inject / reset ---------------------------------------------------------------------

@dataclass
class InjectionRecord:
    id: str
    started_at: float
    truth_service: str
    accept: list
    summary: str


def inject(fault_id: str) -> InjectionRecord:
    f = FAULTS[fault_id]
    if f.mechanism == "flag":
        write_flags(set_flag(read_flags(), f.flag, f.variant))
    elif f.mechanism == "chaos":
        _kubectl("apply", "-f", str(CHAOS_DIR / f.chaos_file))
    else:
        bad = (ROOT / "lab" / "manifests" / "canary" / "cart-canary.bad.yaml").read_text()
        _commit_canary(bad, "cart: tune valkey connection settings for canary")
    rec = InjectionRecord(f.id, time.time(), f.truth_service, sorted(f.accept), f.summary)
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    (TRUTH_DIR / f"{int(rec.started_at)}-{f.id}.json").write_text(json.dumps(asdict(rec), indent=2))
    return rec


@dataclass
class ResetReport:
    clean: bool
    residue: list
    actions: list


def reset_all() -> ResetReport:
    actions = []
    doc = read_flags()
    changed = {k for k, v in doc["flags"].items() if v["defaultVariant"] not in ("off", False)}
    for k in changed:
        off = "off" if "off" in doc["flags"][k]["variants"] else next(iter(doc["flags"][k]["variants"]))
        doc = set_flag(doc, k, off)
    if changed:
        write_flags(doc)
        actions.append(f"flags off: {', '.join(sorted(changed))}")
    for obj in chaos_objects():
        _kubectl("-n", "chaos-mesh", "delete", obj, "--wait=true")
        actions.append(f"deleted {obj}")
    reps = _kubectl("-n", NS, "get", "deploy", "cart-canary", "-o", "jsonpath={.spec.replicas}", check=False)
    if reps and int(reps) > 0:
        good = (ROOT / "lab" / "manifests" / "canary" / CANARY_FILE).read_text()
        _commit_canary(good, "cart: revert canary valkey settings")
        actions.append("reverted canary commit")
    left = residue(lab_state())
    return ResetReport(clean=not left, residue=left, actions=actions)


def init_gitops() -> None:
    """Create the change-record repo (idempotent) and apply the baseline canary (0 replicas)."""
    if not (GITOPS / ".git").exists():
        GITOPS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(GITOPS)], check=True)
        (GITOPS / CANARY_FILE).write_text((ROOT / "lab" / "manifests" / "canary" / CANARY_FILE).read_text())
        _git("add", CANARY_FILE)
        env_date = "2026-11-01T09:00:00Z"
        subprocess.run(["git", "-C", str(GITOPS), "-c", "user.name=platform-bot",
                        "-c", "user.email=platform-bot@example.invalid", "commit", "-q",
                        "-m", "cart: add canary deployment (scaled to zero)"],
                       check=True, env={**__import__("os").environ, "GIT_AUTHOR_DATE": env_date,
                                        "GIT_COMMITTER_DATE": env_date})
    _kubectl("apply", "-f", str(GITOPS / CANARY_FILE))


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "init":
        init_gitops()
        print("gitops ready")
    elif cmd == "inject":
        print(json.dumps(asdict(inject(sys.argv[2])), indent=2))
    elif cmd == "reset":
        print(json.dumps(asdict(reset_all()), indent=2))
    else:
        print(json.dumps(asdict(lab_state()), indent=2))
