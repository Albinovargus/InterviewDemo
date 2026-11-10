"""Pre-demo health check: `uv run python -m noc.doctor [--live]`.

Every line is PASS, WARN or FAIL with the fix. Exit code 1 if anything FAILs.
--live also runs a tiny headless Claude call to prove login and network (costs a fraction of a cent).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time

from noc import clients, faults, tools
from noc.agent_run import RUNS
from noc.console import REPLAYS, SCORECARD

results: list[tuple[str, str, str]] = []


def check(name: str):
    def deco(fn):
        try:
            status, detail = fn()
        except Exception as e:  # a check that crashes is a failed check, with the reason
            status, detail = "FAIL", f"{e.__class__.__name__}: {str(e)[:200]}"
        results.append((status, name, detail))
        print(f"{status:<5} {name:<28} {detail}", flush=True)
        return fn
    return deco


def run(*cmd: str, timeout: int = 30) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:200])
    return p.stdout


def main(live: bool) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    @check("docker + cluster node")
    def _():
        out = run("kubectl", "get", "nodes", "--no-headers")
        return ("PASS", out.split()[1]) if " Ready" in out else ("FAIL", f"node not Ready: {out.strip()} — start Docker Desktop")

    @check("workloads ready")
    def _():
        bad = []
        for ns in ("otel-demo", "observability", "chaos-mesh", "obi"):
            d = json.loads(run("kubectl", "-n", ns, "get", "deploy,daemonset", "-o", "json"))
            for it in d["items"]:
                st, spec = it.get("status", {}), it.get("spec", {})
                want = spec.get("replicas", st.get("desiredNumberScheduled", 1))
                have = st.get("readyReplicas", st.get("numberReady", 0)) or 0
                if want and have < want:
                    bad.append(f"{ns}/{it['metadata']['name']} {have}/{want}")
        return ("PASS", "all deployments and daemonsets ready") if not bad else ("FAIL", "; ".join(bad[:5]) + " — kubectl -n <ns> describe")

    @check("backends")
    def _():
        down = []
        for name, url in [("grafana", f"{clients.GRAFANA}/api/health"), ("prometheus", f"{clients.PROM}/-/ready"),
                          ("loki", f"{clients.LOKI}/ready"), ("tempo", f"{clients.TEMPO}/ready")]:
            try:
                if clients._http.get(url, timeout=5).status_code != 200:
                    down.append(name)
            except Exception:
                down.append(name)
        return ("PASS", "grafana, prometheus, loki, tempo up") if not down else ("FAIL", f"down: {', '.join(down)}")

    @check("traffic")
    def _():
        rps = clients.prom_query('sum(rate(traces_spanmetrics_calls_total{span_kind="SPAN_KIND_SERVER"}[2m]))')
        v = float(rps[0]["value"][1]) if rps else 0.0
        return ("PASS" if v > 10 else "FAIL", f"{v:.0f} server requests/s (want > 10; load generator running?)")

    @check("kafka consumers")
    def _():
        r = clients.prom_query('sum by (service)(rate(traces_spanmetrics_calls_total{service=~"fraud-detection|accounting"}[2m]))')
        got = {x["metric"]["service"]: float(x["value"][1]) for x in r}
        idle = [s for s in ("fraud-detection", "accounting") if got.get(s, 0) <= 0]
        # Seen 2026-11-09: after Kafka restarts, checkout's producer never reconnects and orders stop reaching Kafka.
        return ("PASS", "fraud-detection and accounting consuming") if not idle else (
            "FAIL", f"idle: {', '.join(idle)} — kubectl -n otel-demo rollout restart deploy/checkout")

    @check("eBPF flows (OBI)")
    def _():
        r = clients.prom_query("sum(rate(obi_network_flow_bytes_total[2m]))")
        v = float(r[0]["value"][1]) if r else 0.0
        return ("PASS" if v > 0 else "FAIL", f"{v / 1024:.0f} KiB/s observed" if v else "no flow metrics — check the obi daemonset")

    @check("no faults active")
    def _():
        left = faults.residue(faults.lab_state())
        return ("PASS", "flags off, no chaos, canary at 0") if not left else ("FAIL", f"{left} — uv run python -m noc.faults reset")

    @check("no alerts firing")
    def _():
        a = tools._active_alerts()
        return ("PASS", "quiet") if not a else ("WARN", f"{[(x['service'], x['alert']) for x in a]} — wait 2–3 min after a reset")

    @check("healthy baseline map")
    def _():
        b = tools.load_baseline()
        if not b:
            return "FAIL", "baselines/map-latest.json missing"
        age_h = (time.time() - b.captured_at) / 3600
        st = "PASS" if len(b.edges) >= 20 and age_h < 48 else "WARN"
        return st, f"{len(b.edges)} edges, captured {age_h:.1f} h ago"

    @check("grafana viewer token")
    def _():
        tok = clients.grafana_token()
        r = clients._http.get(f"{clients.GRAFANA}/api/user", headers={"Authorization": f"Bearer {tok}"}, timeout=5)
        return ("PASS", "token accepted (Viewer)") if r.status_code == 200 else ("FAIL", f"HTTP {r.status_code} — re-apply infra/3-config")

    @check("grafana MCP binary")
    def _():
        p = clients.ROOT / "bin" / "mcp-grafana.exe"
        return ("PASS", str(p.name)) if p.exists() else ("FAIL", "bin/mcp-grafana.exe missing")

    @check("claude CLI")
    def _():
        exe = shutil.which("claude")
        if not exe:
            return "FAIL", "claude not on PATH"
        if not live:
            return "PASS", f"{exe} (use --live to test login)"
        out = run("claude", "-p", "Reply with the single word: ready", "--model", "claude-haiku-5-5", "--output-format", "json",
                  "--tools", "", "--setting-sources", "local", "--no-session-persistence", timeout=90)
        res = json.loads(out)
        return ("PASS", f"logged in, answered {res.get('result', '').strip()[:20]!r}") if not res.get("is_error") else ("FAIL", res.get("result", "")[:200])

    @check("usage headroom")
    def _():
        runs = sorted(RUNS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-5:]
        for p in reversed(runs):
            for line in reversed(p.read_text(encoding="utf-8").splitlines()):
                if '"rate_limit_event"' in line:
                    info = json.loads(line)["raw"]["rate_limit_info"]
                    w = info.get("unifiedWindows", {})
                    parts = [f"{k} {v.get('utilization', 0):.0%}" for k, v in w.items()]
                    worst = max((v.get("utilization", 0) for v in w.values()), default=0)
                    return ("PASS" if worst < 0.8 else "WARN"), ", ".join(parts) + f" (as of {time.strftime('%H:%M', time.localtime(p.stat().st_mtime))})"
        return "WARN", "no recent run reported usage"

    @check("replays")
    def _():
        reps = sorted(REPLAYS.glob("*.jsonl")) if REPLAYS.exists() else []
        beats = {}
        for p in reps:
            meta = json.loads(p.read_text(encoding="utf-8").splitlines()[0]).get("meta", {})
            beats[meta.get("beat")] = beats.get(meta.get("beat"), 0) + 1
        ok = beats.get(5, 0) >= 1 and beats.get(7, 0) >= 1
        return ("PASS" if ok else "FAIL"), f"{len(reps)} replays; by beat {dict(sorted((k or 0, v) for k, v in beats.items()))}"

    @check("scorecard")
    def _():
        return ("PASS", json.loads(SCORECARD.read_text()).get("headline", "")) if SCORECARD.exists() else ("FAIL", "no scorecard")

    @check("console")
    def _():
        try:
            r = clients._http.get("http://127.0.0.1:8765/api/health", timeout=5)
            return ("PASS", "http://127.0.0.1:8765") if r.status_code == 200 else ("FAIL", f"HTTP {r.status_code}")
        except Exception:
            return "FAIL", "not running — uv run python -m noc.console"

    @check("laptop memory")
    def _():
        out = run("docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemPerc}}")
        node = [l for l in out.splitlines() if l.startswith("blast-radius")]
        pct = float(node[0].split()[1].rstrip("%")) if node else 0
        return ("PASS" if pct < 80 else "WARN"), f"kind node at {pct:.0f}% of Docker's memory"

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    print(f"\n{len(results) - len(fails) - len(warns)} pass, {len(warns)} warn, {len(fails)} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main("--live" in sys.argv))
