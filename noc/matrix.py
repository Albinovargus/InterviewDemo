"""Fault matrix: inject each fault, record which alerts fire and when, reset, wait for quiet.

`uv run python -m noc.matrix [fault ...]` writes runs/fault-matrix.json and docs/fault-matrix.md.
"""
from __future__ import annotations

import json
import sys
import time

from noc import clients, faults

FIRE_TIMEOUT = 330  # s to wait for the first alert
OBSERVE = 150  # s to keep watching after the first alert, for the storm to develop
QUIET_TIMEOUT = 900  # s to wait for every alert to clear after reset


def active() -> list[dict]:
    out = []
    for a in clients._get(f"{clients.GRAFANA}/api/alertmanager/grafana/api/v2/alerts", {"active": "true"},
                          {"Authorization": f"Bearer {clients.grafana_token()}"}):
        out.append({"alert": a["labels"].get("alertname"), "service": a["labels"].get("service"),
                    "started": clients._parse_ts(a.get("startsAt"))})
    return out


def wait_quiet(timeout: float = QUIET_TIMEOUT) -> float:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not active():
            return time.time() - t0
        time.sleep(15)
    raise RuntimeError(f"alerts still firing after {timeout}s: {active()}")


def run_one(fid: str) -> dict:
    rep = faults.reset_all()
    if not rep.clean:
        raise RuntimeError(f"reset left residue: {rep.residue}")
    settled = wait_quiet()
    rec = faults.inject(fid)
    first = None
    t0 = time.time()
    while time.time() - t0 < FIRE_TIMEOUT:
        if active():
            first = time.time()
            break
        time.sleep(10)
    seen: dict[tuple, float] = {}
    if first:
        while time.time() - first < OBSERVE:
            for a in active():
                seen.setdefault((a["alert"], a["service"]), a["started"])
            time.sleep(15)
    reset = faults.reset_all()
    return {"fault": fid, "truth": rec.truth_service, "injected_at": rec.started_at,
            "settle_before_s": round(settled), "first_alert_after_s": round(first - rec.started_at) if first else None,
            "alerts": [{"alert": k[0], "service": k[1], "after_s": round(v - rec.started_at)} for k, v in
                       sorted(seen.items(), key=lambda kv: kv[1])],
            "reset_clean": reset.clean, "reset_actions": reset.actions}


def to_markdown(rows: list[dict]) -> str:
    out = ["# Fault matrix", "", "Measured by `noc.matrix`: inject, watch alerts for 150 s after the first one, reset.", "",
           "| Fault | Truth | First alert after | Alerts (service: rule @ s) | Reset clean |", "|---|---|---|---|---|"]
    for r in rows:
        alerts = "; ".join(f"{a['service']}: {a['alert']} @{a['after_s']}" for a in r["alerts"]) or "none fired"
        first = f"{r['first_alert_after_s']} s" if r["first_alert_after_s"] is not None else "no alert"
        out.append(f"| {r['fault']} | {r['truth']} | {first} | {alerts} | {'yes' if r['reset_clean'] else 'NO'} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ids = sys.argv[1:] or list(faults.FAULTS)
    path = clients.ROOT / "runs" / "fault-matrix.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    rows = [r for r in rows if r["fault"] not in ids]
    for fid in ids:
        print(time.strftime("%H:%M:%S"), "fault", fid, flush=True)
        try:
            row = run_one(fid)
        except Exception as e:  # record and move on; one bad fault must not stop the matrix
            row = {"fault": fid, "truth": faults.FAULTS[fid].truth_service, "error": str(e), "alerts": [],
                   "first_alert_after_s": None, "reset_clean": faults.reset_all().clean}
        print(json.dumps(row), flush=True)
        rows.append(row)
        path.write_text(json.dumps(rows, indent=2))
        (clients.ROOT / "docs" / "fault-matrix.md").write_text(to_markdown(rows), encoding="utf-8")
