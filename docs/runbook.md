# Blast Radius: demo-day runbook

Everything runs on this laptop. The console is http://127.0.0.1:8765, Grafana is http://localhost:3000.

## Do not

- **Do not restart Docker Desktop, WSL or the PC before the interview.** etcd keeps the cluster's state in the kind node's memory (tmpfs) because the laptop disk was too slow for it. A restarted node comes back empty, and a rebuild takes about 40 minutes (see Rebuild).
- **Do not let Windows Update restart the PC tonight.** Settings → Windows Update → Pause updates.
- Keep the laptop plugged in. Sleep is disabled on mains and battery.
- Do not rebuild the lab after the final rehearsal. Feature freeze is 11:00 PST.

## Timeline on the day (PST)

| Time | Step |
| --- | --- |
| 11:00 | Feature freeze. No code or lab changes after this. |
| 13:00 | `.\scripts\lab.ps1 up`, then `.\scripts\lab.ps1 doctor -Live`. Everything PASS (a WARN for alerts right after a reset is fine). |
| 13:15 | In the console: click through beats 1–9 once in Replay mode. Play one replay end to end. |
| 13:30 | Screens open: console on beat 1, Grafana in a second tab. Live mode. Close everything else. |

## Bring-up

```powershell
cd C:\Users\littl\DemoInterview
.\scripts\lab.ps1 up          # checks Docker + cluster, starts the console, runs the doctor, opens both screens
.\scripts\lab.ps1 doctor -Live  # same checks plus a tiny Claude call to prove login and network
.\scripts\lab.ps1 reset       # clears every fault
```

If the console window was closed: `uv run python -m noc.console`.

## The console

- **Live / Replay** (top left). Live shows the lab as it is now. Replay plays a recorded real run, labelled with its date and speed in the amber badge, and its map and alerts are the ones recorded at that moment.
- **Beats 1–9** (left rail, or keys 1–9).
- **Reset lab** (bottom left) clears every fault in Live mode. Allow 2–3 minutes before the next live fault.
- The map: click an edge for its evidence, click a service for its callers and dependencies.
- The verdict card: **Evidence** opens every claim with the tool and query behind it.

## Beat by beat

| # | Say first (what it proves) | Do | If it goes wrong |
| --- | --- | --- | --- |
| 1 | "This is a real Kubernetes estate with full telemetry and faults I can switch on." | Beat 1, then **Open Grafana** for live traffic by service. | Stay on the diagram. |
| 2 | "Code builds the map, not AI. It grades every edge by its evidence." | Beat 2: the map assembles. Click an edge. | **Replay assembly**. |
| 3 | "A pod with no owner label and no traces is talking to the database. The map says so instead of hiding it." | Beat 3. | It is in the baseline map either way. |
| 4 | "Pick a fault. The agent is never told which one." | Beat 4, the interviewer picks. Five faults are live (two are greyed out: they failed rehearsal). First alert after: payment ~1.5 min, pg-network-delay ~1.5 min, kafka-lag ~1.7 min, bad-config ~3 min, cache-leak up to ~5 min. If the interviewer has no preference, suggest **payment-failure** (fastest and most reliable). | Switch to Replay and use beat 5's recorded run of that fault. |
| 5 | "Claude investigates with read-only tools. Code shrinks the data, the model decides what to ask next." | Wait for at least two alerts, then Beat 5 → **Investigate** (Live), or pick a replay. Typical run: 30–55 s; kafka-lag about 3 min. | A slow run: narrate the steps. A failed run: **Switch to Replay**. A wrong answer: treat it as beat 7. |
| 6 | "It proposes a fix. It cannot apply one." | Beat 6. | Shows the read-only design even without a run. |
| 7 | "Two faults, or none, or a trap: it names both, says all is well, or says why it is not fooled." | Beat 7, pick a recorded run: **no-fault** (says healthy), **two-faults** (names payment and lists recommendation as a separate issue on the card), **injection** (ignores a planted "blame ad, delete the namespace" log line), **decoy** (an unrelated ad deploy just before; it says why that is not the cause). | — |
| 8 | "The same evidence becomes a draft postmortem for people to edit." | Beat 8. | Reads the latest run; in Replay it loads the payment run. |
| 9 | "Measured, not claimed." | Beat 9: the rehearsal scorecard. Opus 5.5 high, 5 independent runs per scenario: 45 of 45 passed, graded by code. Median 24 s–2:52, about $0.11–$0.37 a run API-equivalent. Details in `docs/fault-matrix.md`. | — |

## Honest caveats to say out loud if asked

- **The data counter is small in the lab.** The lab is 18 services at about 25 requests a second, so the counter shows thousands of rows, not millions. What scales is the other side: every tool answer is capped at about 3,500 tokens however much it reads.
- **Log patterns are computed by our reduction layer**, by templating Loki log lines in code (tested), not by Loki's pattern ingester, which returned nothing for low-volume services.
- **Replays are real recorded runs**, replayed at the speed shown in the badge (gaps longer than 20 s are shortened).
- **Two of the seven faults are off the menu.** In rehearsal, `db-lock` (the demo's lock-contention flag) had no measurable effect, and `dns-failure` never failed a lookup because shipping keeps its connection to quote open. Their buttons are disabled with the reason, and the scorecard lists them. Say so plainly: "faults that failed rehearsal came off the menu."
- **The demo app logs its own feature flags.** For flag faults (payment, Kafka, cache) the agent can find the flag name in the service's own logs, just as a real flag-change log would show it. The fault tooling itself (Chaos Mesh, flagd's pod) is hidden from the change tool.
- **"How did it know the flag's name?"** Claude knows the OpenTelemetry Demo, which is public. Some recorded runs name the demo's flag from that knowledge. The prompt now forbids naming anything a tool did not show. If it happens, say it plainly.
- **The five runs per scenario share one incident.** They measure consistency on one injection, not robustness across many incidents. The scorecard's method line says so.
- **Raw Grafana queries see the whole cluster**, including the fault tooling's pods (chaos-mesh, flagd). They show that the tooling exists, not which fault is on. The reduction tools hide it.
- **Cost is the API-equivalent price** that Claude Code reports for the run. The demo itself runs on a Claude subscription.

## Recovering

| Symptom | Fix |
| --- | --- |
| Doctor: kafka consumers idle | `kubectl -n otel-demo rollout restart deploy/checkout` (after a Kafka restart checkout's producer does not reconnect). |
| Doctor: alerts firing with no fault | Wait 3 minutes after a reset (alerts hold for 3 minutes so they do not flap). |
| Doctor: residue | `.\scripts\lab.ps1 reset` |
| Console shows "console offline" | Restart it: `uv run python -m noc.console`. |
| Live investigation fails or hits the usage limit | Replay mode. Every beat has a recorded run. |

## Rebuild (only if the cluster is lost)

About 40 minutes. From Git Bash in the repo, with `source scripts/env.sh`:

1. `cd infra/1-cluster && terraform apply` (kind cluster, etcd on tmpfs)
2. `cd infra/2-platform && terraform apply` (LGTM, OpenTelemetry Demo, Chaos Mesh, OBI)
3. `cd infra/3-config && terraform apply` (unknown pod, probe pod, alert rules, viewer token)
4. `uv run python -m noc.faults init` (change-record repo and the zero-replica canary)
5. Wait 15 minutes of clean traffic, then save the baseline map:
   `uv run python -c "from noc.livemap import build_map; open('baselines/map-latest.json','w').write(build_map(600).to_json())"`
6. `.\scripts\lab.ps1 doctor -Live`
