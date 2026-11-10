# Blast Radius: AI-Assisted NOC Demo Proposal

Nov 8, 2026 · @Gern

A live demo in which an AI first maps a Kubernetes estate's service dependencies from ordinary telemetry, then diagnoses an outage the audience causes, using only the kinds of data a NOC already collects.

The depth behind every component, for answering follow-up questions, is in the second tab: Presenter briefing

## The pitch

The demo shows an AI "second analyst" for the NOC that adds value from telemetry the organisation already pays to collect, with no new agents and no change to the stack.

It runs in two acts on a small Kubernetes estate on a laptop:

1. **Discover.** Starting from no map at all, Claude builds the service dependency graph from traces, network flows and Kubernetes metadata. Every link shows its confidence and its evidence.
2. **Diagnose.** Someone in the room picks a failure. Hundreds of alerts fire, and Claude uses the map to separate cause from victims, proposes a fix that a human must approve, and drafts the postmortem.

The two acts make one argument: an accurate service map is what makes incident response fast, and both can be produced from data already flowing into Grafana.

It is built as a set of self-contained beats of 30–90 seconds each, so any one of them can be shown mid-interview when a question calls for it, either live or as a replay of a real recorded run.

The demo is built on public, open-source components and synthetic traffic. It assumes only a standard OpenTelemetry-to-Grafana pipeline on Kubernetes, so it needs no knowledge of any internal system.

## The problem

The demo targets three problems that almost every NOC in a large Kubernetes shop shares, whatever its tools:

- **Alert storms.** One upstream fault pages dozens of teams. Analysts spend the first stretch of every incident working out which alert is the cause and which are symptoms.
- **No reliable service map.** Dependencies live in people's heads, stale wiki pages and partial trace coverage. The map is weakest exactly where incidents start: queues, external services, uninstrumented legacy components and DNS.
- **Too much telemetry to read.** Logs, metrics and traces arrive in terabytes a day. The answer is usually in there, but no person can read it fast enough, and neither can a language model if it is pointed at raw data.

The demo claims to help with all three while being honest about the third: the model never reads raw telemetry.

## Architecture

&#91;embedded content: demo architecture · telemetry flows down, summaries flow to Claude\]

Telemetry flows down through a standard pipeline. Claude sits below the reduction layer, so it only ever sees small query results and has no direct access to the cluster.

## The lab

The whole estate runs on one laptop, built by Terraform in three stages so a failure in any stage is easy to see and retry. Every piece is open source and pinned to a version.

| Component | What it is | What it stands in for |
| --- | --- | --- |
| Terraform, three stages | Stage 1 creates the kind cluster, stage 2 installs the Helm charts, stage 3 applies custom resources and demo config. Uses the tehcyx/kind, helm and kubernetes providers | The organisation's infrastructure-as-code |
| kind, single node | Kubernetes running inside Docker. One node keeps the trace-based service graph accurate | Production clusters |
| [OpenTelemetry Demo](https://opentelemetry.io/docs/demo/) 3.1 (Helm chart 0.42.x) | An online shop of about 20 services in about 11 languages, with Kafka, PostgreSQL, a Valkey cache, a load generator and flagd failure switches. Its bundled Jaeger, Prometheus, Grafana and OpenSearch are turned off | The business services |
| [grafana/otel-lgtm](https://github.com/grafana/docker-otel-lgtm), pinned | One container holding an OpenTelemetry Collector, Prometheus, Loki, Tempo and Grafana, already wired together. Tempo builds the service graph from traces, and exemplars link metrics to example traces | The existing observability stack |
| Loki pattern ingester | Off by default, turned on explicitly. Groups similar log lines into patterns and counts them over time | Log analytics |
| Grafana alerting | Grafana's built-in Alertmanager with a small set of standard alert rules | The NOC's alert feed |
| [OBI](https://opentelemetry.io/docs/zero-code/obi/) (OpenTelemetry eBPF Instrumentation, formerly Grafana Beyla) | Records network connections between pods without code changes | Legacy or third-party components |
| An "unknown" pod | One unlabelled, uninstrumented pod that queries PostgreSQL | Shadow IT and forgotten dependencies |
| [Chaos Mesh](https://chaos-mesh.org/) 2.8.8 or later | Injects network delay, DNS failures, pod kills and CPU or memory stress. Configured for kind's containerd runtime | Real infrastructure faults |
| A Git repo of manifests | All configuration lives here, so every intentional change is a commit | The change record |
| Reduction MCP server (custom) | Six summary tools; see The AI design | The integration layer a team would build |
| [Grafana MCP server](https://github.com/grafana/mcp-grafana) | Read-only query tools for Claude | A standard integration point |
| Claude Code, headless | The investigating agent | The AI analyst |
| Demo screen | One web page showing the map, alert feed, agent steps and data counter, with a button per beat | The NOC console |

**Faults stay hidden from the agent.** Its tools cannot see the Chaos Mesh namespace or the flag service, because real failures don't announce themselves. Only the bad-config fault leaves a Git commit, as a bad change would in real life.

**Laptop.** A 32 GB machine with Docker given 12–16 GB; Docker Desktop defaults to half the host's memory. The demo app alone asks for 6 GB, so 16 GB laptops are marginal. Expect 5–10 minutes to build the lab with images cached and longer from cold, so build it before the interview and leave it running.

## Act 1: Discover

In Act 1, a service map is built from three sources that each see part of the picture. Plain code extracts the edges; Claude reconciles the sources, grades each link and explains the gaps.

| Source | What it shows | Blind spots |
| --- | --- | --- |
| Tempo service graph (from traces) | Who calls whom, with request rate, error rate and latency per edge. Kafka hops appear as a direct edge such as checkout → fraud-detection, marked as messaging. Uninstrumented databases appear as "virtual nodes" named from span attributes | Anything without traces; Kafka edges drop out when consumer lag exceeds the pairing window |
| OBI network flows (eBPF) | Actual connections between pods, labelled with their Kubernetes owners, whether instrumented or not | Why a call happens; traffic leaving the cluster shows only an address |
| Kubernetes API | Deployments, Services, namespaces, owner and team labels | What talks to what at runtime |

**How the map is built**

1. **Baseline first.** The map is built from a healthy hour and saved as a versioned file. During an incident it is rebuilt and compared with that baseline.
2. **Direction in words.** Every edge is written as a sentence, "checkout calls payment", with its rate, errors and latency. Published research found that misreading caller and callee is the most common reason AI diagnoses fail.
3. **Grades.** Claude marks each edge *confirmed* (two sources agree), *likely* (one strong source) or *unexplained*.
4. **Gaps named.** Claude lists what it cannot explain, for example: "An unlabelled pod in the default namespace opens about 400 connections an hour to PostgreSQL. It has no owner label, no traces and no Service." This is the moment that gets the room's attention.
5. **Silent edges.** An edge in the baseline that is missing now is shown as *silent*, not deleted. A dependency that stops talking is evidence.

**Why it matters beyond the demo.** The map is useful on its own: rebuilt nightly, it shows ownership gaps, uninstrumented services and drift between what is deployed and what actually talks.

## Act 2: Diagnose

In Act 2, someone picks a fault from a menu, alerts flood in, and Claude works from the alert summary and the map to one primary cause. Timings are quoted from rehearsal, not promised in advance.

**The fault menu.** Each fault is real and shows symptoms somewhere other than where it starts. Only faults that pass rehearsal consistently stay on the menu.

| Fault | How it is injected | Where the alerts fire | What it tests |
| --- | --- | --- | --- |
| Kafka consumer lag | Demo flag `kafkaQueueProblems` | Order backlog, fraud-detection and accounting | Spotting a *silent* edge: the checkout → fraud-detection link drops out of the map |
| Database lock contention | Demo flag `productCatalogLockContention` | Product catalog latency, frontend errors | A shared database hurting a service that looks fine |
| Payment failures | Demo flag `paymentFailure` | Checkout and frontend error rates | Following errors upstream to the real source |
| Cache memory leak | Demo flag `recommendationCacheFailure` | Recommendation restarts, frontend latency | Telling a resource problem from a code error |
| Network delay to PostgreSQL | Chaos Mesh NetworkChaos | Almost every service that uses the database | A loud, wide storm with one quiet cause |
| DNS failure for one service | Chaos Mesh DNSChaos | Callers report connection errors; the callee looks healthy | Not blaming the loudest service |
| Bad config on half the pods | Git commit to a canary Deployment behind the same Service | About half of requests fail | Using the change record ("what changed?") |

Certificate expiry was considered and dropped: cert-manager cannot issue certificates shorter than 1 hour and renews them automatically, so a reliable expiry would need an extra TLS layer just for the demo.

**How Claude investigates.** Each step is a read-only tool call, shown on screen as it runs.

1. **Start from one incident.** The first prompt already holds the grouped alerts (for example 280 alerts in 1 incident across 9 services) and the baseline map.
2. **Walk upstream.** Using the map's directions, Claude lists the services every affected service depends on. Those are the suspects.
3. **Ask what changed.** Commits, deploys and Kubernetes events in the 30 minutes before the first alert.
4. **Find the first mover.** Which signal moved first, including anything that dropped to zero or went silent.
5. **Read the evidence.** New, spiking or vanished log patterns, and a few example traces, for the top suspect only.
6. **Run the checklist.** Timing, baseline, direction, resources and evidence against, as set out in The AI design.
7. **Conclude.** One primary cause, the symptoms listed separately, a confidence level, and a link from every piece of evidence to its Grafana query.
8. **Propose, do not act.** A suggested fix that a person must apply.
9. **Draft the postmortem.** Timeline, root cause, blast radius and action items, including any map gaps that slowed the diagnosis.

**The credibility test.** Two runs show judgment rather than luck. With two faults at once, Claude should say it is unsure, name both candidates and ask for the one query that would separate them. With no fault at all, it should say nothing is wrong. Published agents often invent a fault in that case.

## Handling scale

The model never reads raw telemetry. It decides what to ask; Loki, Prometheus, Tempo and the reduction tools do the heavy lifting, so the approach does not depend on data volume.

At production scale, one incident window can hold millions of log lines. Research benchmarks found that agents reading raw telemetry overwhelm their context and do worse. Instead, each step narrows the data before Claude sees anything:

1. **Narrow by topology.** The map limits the search to the few services upstream of the alerts.
2. **Narrow by time.** Compare the incident window with a healthy baseline window.
3. **Turn logs into patterns.** Loki's pattern ingester groups similar lines into templates with counts. Comparing two windows shows which patterns are new, spiking or gone. Patterns are kept for 3 hours by default, so both windows must fall inside that.
4. **Report absences as well as spikes.** A metric that drops to zero or a dependency that goes silent is often the clue.
5. **Return summaries, not dumps.** Every tool answers within a fixed size: a top-10 list, a before-and-after difference, or up to 3 example lines.

**Show the numbers on screen.** A counter beside the investigation makes the point at a glance, for example *2.1M log lines → 340 patterns → 12 anomalous → 3 shown to the model*. These figures are illustrative; the demo shows its real counts.

**Division of labour.** Code does the counting, grouping and graph building. Claude does what code does badly: choosing the next question, weighing conflicting evidence and explaining the result in plain language. Cost and timing are covered under The AI design.

## The AI design

The agent is deliberately simple: one Claude agent, a small set of read-only tools that return summaries, and a short reasoning checklist. Published research supports each of those choices; see the Presenter briefing tab for the evidence.

**Harness.** Claude Code in headless mode (`claude -p` with `--output-format stream-json`). It is the same agent loop as interactive Claude Code, run as a script. Every step streams out as an event, which the demo screen displays, and the final event reports the run's cost.

**Model.** Claude Opus 5.5 ($4 per million input tokens, $20 per million output, cache reads $0.20). Set the effort level explicitly, because its default is medium. Pin the model and effort for every rehearsal and the demo. Claude Sonnet 5.5 costs half as much and is the fallback if rehearsals show equal accuracy.

**Tools: two MCP servers, both read-only.**

- **Grafana MCP server** for direct queries, started with `--disable-write`, a Viewer-role service account, and only the query categories enabled. By default it can write dashboards, alert rules and silences, and its full tool list costs about 15,000 tokens of context.
- **A small custom "reduction" MCP server** with six tools. This is the layer that turns big data into small answers, and it is the easiest part of the design to explain.

| Tool | Returns | Why |
| --- | --- | --- |
| incident\_summary | Alerts grouped into incidents, with affected services and first-fire times | Starts the agent from 1 incident, not 280 alerts |
| service\_map | Directed edges in words ("checkout calls payment"), each with rate, errors and latency now against a healthy baseline | Prevents the most common mistake in published failures: reading caller and callee the wrong way round |
| what\_changed | Git commits, deploys, config changes and Kubernetes events in a time window | "What changed?" is the first question every engineer asks |
| metric\_movers | Which signals moved first, including drops to zero and missing series | Causes usually move first; a service going silent is evidence too |
| log\_pattern\_diff | New, spiking and vanished log patterns against a baseline, with up to 3 example lines each | Keeps logs available without reading millions of lines |
| trace\_examples | A few example traces for one service, with short handles in place of long IDs | Lets the agent confirm a call chain directly |

Every tool answer stays under about 2,000–5,000 tokens and says how to narrow the query when it truncates. Claude Code warns above 10,000 tokens per tool result and saves very large results to a file, which would let raw data back in.

**Context.** The first prompt already contains the incident summary and the service map. Everything else is fetched as needed.

**Reasoning checklist, not a fixed script.** The system prompt asks the agent to test each hypothesis against five checks before concluding:

1. Did the suspect move before the symptoms?
2. How does it compare with the healthy baseline?
3. Does the dependency direction support it?
4. Is a resource limit (CPU, memory, connections) involved?
5. What evidence argues against it?

The answer names one primary cause, lists symptoms separately, states its confidence and links each piece of evidence to its query. Letting the model choose its evidence beat fixed, pre-scripted pipelines in a 2026 study.

**Guardrails.**

- Only the two MCP servers are allowed. Claude Code's built-in shell, file and web tools are explicitly disallowed, and a turn limit caps every run.
- The agent cannot apply a fix. A person runs the proposed change outside the agent.
- Logs can contain text written by anyone, including instructions. The system prompt treats all tool results as data, and with no write or outbound tools an injected instruction has nothing to act on. One rehearsal plants a malicious log line to prove it.

**Rehearsal evaluation.** Run every fault on the menu at least 5 times on fresh injections, plus one run with no fault and one with a harmless decoy commit. Record whether the primary cause was right, time to first hypothesis, total time and cost. Keep only faults that pass consistently, and quote the measured results ("5 of 5, median 70 seconds") rather than estimates. Save the best real runs as replays.

**Cost and speed.** Each step re-sends the growing conversation, so one investigation bills a few hundred thousand input tokens, mostly as cheap cache reads. A rough estimate is $1–2 per investigation on Opus 5.5; measure the real figure in rehearsal. Published agents take from about 45 seconds to several minutes depending on model and effort, so the demo quotes its own rehearsal times.

## Design decisions

Each row is a choice the presenter can defend in one breath: what was chosen, what was rejected, and why. This table is the core of the architecture conversation.

| Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- |
| Where the lab runs | kind on a laptop | A cloud cluster | Free, no account needed, and already warm when the interview starts |
| How it is built | Terraform in three stages | One big apply, or shell scripts | Shows infrastructure-as-code; the Kubernetes provider warns that creating a cluster and using it in the same apply causes intermittent errors |
| Observability backend | grafana/otel-lgtm, one container | Separate Loki, Tempo and Mimir charts | One box to explain, pre-wired service graphs and exemplars; Mimir alone wants about 16 GB and object storage. Production would use the scalable versions behind the same query APIs |
| Map sources | Traces, network flows and Kubernetes, reconciled | Traces only | Each sees what the others miss, and where they disagree is the interesting finding |
| Data reduction | Code and Grafana queries before the model | Sending logs to the model | Volume, cost and accuracy; raw telemetry overwhelms the model's context |
| Agent shape | One agent with a reasoning checklist | A fixed pipeline, or several agents | Easiest to explain and debug; in published tests, fixed pipelines skipped decisive evidence |
| Integration | MCP: Grafana's server plus one custom server | Custom API code | An open standard with built-in read-only flags; works with any MCP-capable client |
| Safety | Read-only tools; a person applies fixes | Automatic remediation | Trust has to be earned, and prompt injection is not a solved problem |
| Fault injection | Real faults, hidden from the agent | Fake, scripted telemetry | Nobody can call it canned |
| Delivery | Self-contained beats, live or replay | One long live run | Fits into an interview, and survives a bad network |

## Demo beats

The demo is nine beats, each one button on the demo screen and each under 90 seconds. Any beat can be pulled up on its own when an interview question invites it.

| # | Beat | What is shown | Time | Interview cue | Mode |
| --- | --- | --- | --- | --- | --- |
| 1 | The stack | Grafana with live traffic, next to the architecture diagram | 60 s | "Walk me through something you've built" | Live |
| 2 | The map | The map assembles; edges coloured confirmed, likely or unexplained | 90 s | "How would you learn an estate you don't know?" | Live |
| 3 | The unknown caller | The unlabelled pod talking to PostgreSQL | 30 s | "How do you find ownership gaps?" | Live |
| 4 | Break something | The interviewer picks a fault; alerts flood in and the map turns red | 60 s | "What does a bad night look like?" | Live |
| 5 | The diagnosis | Agent steps, the shrinking data counter, the verdict with evidence links | 90 s replay, longer live | "How would you use AI in operations?" | Replay or live |
| 6 | The guardrail | The proposed fix waiting for approval; the read-only design | 30 s | "Would you let AI touch production?" | Replay |
| 7 | The honest answer | Two faults at once, or none; the agent hedges or says all is well | 60 s | "What if it's wrong?" | Replay |
| 8 | The postmortem | The drafted timeline, cause and actions | 30 s | "How do you learn from incidents?" | Replay |
| 9 | The scorecard | Rehearsal results: passes out of runs, median time, cost per run | 30 s | "Does it actually work?" | Static |

**Three ways to run it**

- **Mid-interview:** one beat at a time, when a question invites it.
- **5-minute slot:** beats 2, 4, 5 and 6.
- **15-minute slot:** all nine in order.

**Rules for the day**

- The lab is built and warm before the call. Open Grafana and the demo screen in advance.
- Every replay is a real recorded run and is labelled as one. Say "this is a recorded real run; I can run it live" and mean it.
- After a live fault, the demo screen's reset button clears it. Allow 2–3 minutes for metrics to settle before the next live fault, or switch to replays.
- Lead each beat with one sentence of what it proves, then show it. Go deeper only when asked; the Presenter briefing tab holds the detail.

## Tough questions

Senior I&O people will probe scale, trust, accuracy and overlap with existing products. These answers are short and honest; deeper technical follow-ups are in the Presenter briefing tab.

| Question | Answer |
| --- | --- |
| "Our log volume is enormous. You can't feed that to an LLM." | Agreed, and it doesn't. Loki, Prometheus and the reduction tools shrink the data; each answer the model sees is a few thousand tokens at most. The counter on screen shows it. |
| "Doesn't Grafana already do this?" | Partly, yes. Grafana Cloud's Assistant Investigations runs multi-agent investigations over metrics, logs, traces and profiles, and Sift runs Kubernetes triage checks. Both are Grafana Cloud features; self-managed Grafana doesn't get Investigations. This demo is the open path: Grafana's own open-source MCP server, read-only, inside existing access controls. If you're on Grafana Cloud, Investigations is the native option to compare against. |
| "How accurate is it?" | On the demo's faults: the rehearsal scorecard, for example 5 of 5 runs right with a median time. On realistic, messy incidents, published benchmarks still put the best agents under 50% under strict scoring. That is why it proposes and explains rather than acts. |
| "What if it's wrong?" | Every conclusion links to the query behind it and carries a confidence level. A person decides. The two-fault and no-fault runs show how it behaves when unsure. |
| "Would you let it touch production?" | Not as built. Its tools are read-only, its shell and file tools are disabled, and its Grafana account has viewer rights only. Fixes go through the normal change process. |
| "What about prompt injection through logs?" | Logs can contain anyone's text, so the agent treats tool results as data. More importantly, it has no tool that writes or sends anything, so an injected instruction has nothing to act on. A rehearsal plants a malicious log line to prove it. |
| "Where does our data go?" | Only small query results go to the model, never raw telemetry. Claude is available through Anthropic's API and through AWS, Google Cloud and Microsoft, so it can follow existing data agreements. |
| "Does it work without full tracing?" | Partly. eBPF network data and Kubernetes metadata fill gaps, and the map shows exactly where coverage is missing. |
| "What does it cost?" | Roughly $1–2 per investigation on the largest model, measured in rehearsal. It scales with incidents investigated, not data ingested. |
| "How does it handle 1,000 services?" | Code builds the map, which scales. Investigations follow the map, so they examine a handful of suspects whatever the estate's size. |
| "Isn't this canned?" | The faults are real and the interviewer picks one. Offer to run another live. |
| "What would a pilot look like?" | Read-only access to one cluster's Grafana for a few weeks: rebuild the map nightly, run the agent alongside the NOC on real incidents, and compare its answers with what the team found. |

## Risks and fallbacks

The biggest risks on the day are the network and the laptop, not the AI, so every live beat has a recorded replay behind it.

| Risk | Fallback |
| --- | --- |
| No internet, or the venue blocks the API | Switch every beat to replay; replays run from local files |
| Claude reaches a wrong conclusion live | Treat it as beat 7: show the evidence chain and let the room spot the flaw, then show a replay of a correct run |
| A live investigation runs slowly | Narrate the steps as they stream; the data counter fills the time. Or switch to the replay |
| Laptop runs out of memory | Give Docker 12–16 GB, turn off the demo's bundled backends, and rehearse on the exact laptop |
| OBI doesn't run on a Mac | OBI needs a Linux kernel with BTF, and Docker Desktop's VM may not have it. Check before building; if missing, use a Linux laptop or replay beat 3 |
| Network delay doesn't work on a Mac | Chaos Mesh network faults need a kernel module in Docker Desktop's VM. Smoke-test early; drop the fault if it fails |
| A fault doesn't trigger alerts reliably | Rehearse every fault at least 5 times; remove any that don't pass consistently |
| Kafka edges vanish from the map during the Kafka fault | Expected: trace pairing times out when lag is high. Present it as a silent edge, which is the point of that fault |
| Questions about internal systems | Stay generic: the demo assumes only OpenTelemetry, Grafana and Kubernetes |

**Keep it clean.** Build everything from public components and synthetic data, with no internal names, configs, data or screenshots. That keeps the interview fair and avoids any confidentiality issues.

## What it takes

One person can build and rehearse the demo in 7–10 focused days. Build it beat by beat, so something demo-ready exists after every phase.

| Phase | Delivers beats | Work | Estimate |
| --- | --- | --- | --- |
| 1. Lab | 1 | Terraform stages 1–3: kind, the OTel Demo, otel-lgtm with the pattern ingester on, OBI, Chaos Mesh; alert rules | 1–2 days |
| 2. Map | 2, 3 | Edge extraction from the three sources; baseline save and compare; Claude grading and gap report | 1–2 days |
| 3. Faults | 4 | Script each fault and its reset; confirm alerts fire; hide injection from the agent's tools | 1 day |
| 4. Agent | 5, 6, 7, 8 | Reduction MCP server; Grafana MCP read-only; system prompt and checklist; headless run that streams events | 2 days |
| 5. Demo screen | All | One page: map, alerts, agent steps, counter, a button per beat, live or replay | 1–2 days |
| 6. Rehearsal | 9 | Five or more runs per fault, plus no-fault, two-fault, decoy-commit and planted-injection runs; record replays; build the scorecard | 1 day |

**Prerequisites**

- A 32 GB laptop, with Docker given 12–16 GB and about 40 GB of free disk. Linux is safest; on a Mac, check OBI and network faults first
- Terraform, kubectl, Helm and kind
- Claude Code with an Anthropic API key, or Claude through an approved AWS, Google Cloud or Microsoft account
- Reliable internet on the day, with the replays as backup

## Sources

Checked in November 2026. Claude model prices are Anthropic's published API rates at that date.

**Lab and observability**

- [OpenTelemetry Demo documentation](https://opentelemetry.io/docs/demo/) and [Kubernetes deployment](https://opentelemetry.io/docs/demo/kubernetes-deployment/)
- [grafana/docker-otel-lgtm](https://github.com/grafana/docker-otel-lgtm)
- [Tempo service graphs](https://grafana.com/docs/tempo/latest/metrics-from-traces/service_graphs/)
- [Loki HTTP API, including patterns](https://grafana.com/docs/loki/latest/reference/loki-http-api/)
- [OpenTelemetry eBPF Instrumentation (OBI)](https://opentelemetry.io/docs/zero-code/obi/)
- [Chaos Mesh Helm installation](https://chaos-mesh.org/docs/production-installation-using-helm/) and [DNS chaos](https://chaos-mesh.org/docs/simulate-dns-chaos-on-kubernetes/)
- [cert-manager Certificate resource](https://cert-manager.io/docs/usage/certificate/)
- [tehcyx/kind Terraform provider](https://registry.terraform.io/providers/tehcyx/kind)

**Grafana AI features**

- [Grafana MCP server](https://github.com/grafana/mcp-grafana)
- [Assistant Investigations](https://grafana.com/docs/grafana-cloud/machine-learning/assistant/platform/investigation/) and [Assistant on self-managed Grafana](https://grafana.com/docs/grafana-cloud/machine-learning/assistant/get-started/self-managed/)
- [Sift analyses](https://grafana.com/docs/grafana-cloud/machine-learning/sift/analyses/)

**Claude and agent design**

- [Claude Code headless mode](https://code.claude.com/docs/en/headless), [MCP in Claude Code](https://code.claude.com/docs/en/mcp) and [Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
- Anthropic engineering: [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents), [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Anthropic on prompt injection defenses](https://www.anthropic.com/research/prompt-injection-defenses) and [OWASP LLM01: Prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)

**Research on AI root-cause analysis**

- [OpenRCA](https://proceedings.iclr.cc/paper_files/paper/2025/file/d29b8d53678015079e1d245c023e49d2-Paper-Conference.pdf) (ICLR 2025)
- [ITBench](https://arxiv.org/html/2502.05352) and [ITBench-AA results](https://artificialanalysis.ai/articles/itbench-aa-launch)
- [AIOpsLab](https://arxiv.org/html/2501.06706)
- [RCA agent trajectory study](https://arxiv.org/html/2608.21310) (2026 preprint)
- ["Free to Look" evidence-package study](https://arxiv.org/html/2610.35767) (2026 preprint)
- [GALA+](https://arxiv.org/pdf/2608.08968) (ASE 2026)
