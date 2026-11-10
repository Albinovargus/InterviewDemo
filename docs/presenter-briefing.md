# Presenter briefing

Everything the presenter needs to explain each part of the demo in plain words and handle follow-up questions, organised by the same layers as the architecture diagram.

## The story in 60 seconds

**The opening, close to word for word:** "NOCs drown in alerts and rarely have an accurate map of what depends on what. I built a lab to show both problems can be tackled with data a standard OpenTelemetry and Grafana shop already collects. First it builds the dependency map automatically. Then it uses that map to let an AI analyst find the cause of an outage in a couple of minutes, with every step checkable and no ability to break anything."

**Three ideas to land, whatever else happens:**

1. **The map is the foundation.** Without knowing who calls whom, people and AI both blame the loudest service instead of the cause.
2. **Reduce first, reason second.** The model never reads raw telemetry. Code and queries shrink millions of lines into a few thousand tokens; the model decides what to ask next.
3. **Trust is designed in.** Read-only tools, evidence linked to every claim, measured accuracy, and an agent that says when it isn't sure.

**How to talk about it**

- Say what was measured, not what is hoped: "5 of 5 runs, median 70 seconds", not "it's fast and accurate".
- Frame choices as trade-offs: "I chose X over Y because…". The Design decisions table on the first tab has one per row.
- When something wasn't tested, say so: "I didn't test that. Here's how I'd find out." Senior people trust that more than a guess.
- Point at the architecture diagram while explaining. Each beat lives in one box.

## Core concepts

These are the words the conversation will run on. Each should be explainable in one sentence without notes.

| Term | Plain meaning | Where it shows up in the demo |
| --- | --- | --- |
| Metrics, logs, traces | The three kinds of telemetry: numbers over time, text events, and the path of one request through many services | All three feed the map and the diagnosis |
| Span | One step in a trace, such as "checkout called payment". It records which service, how long, and its parent step | Pairs of spans give the map its edges and their direction |
| Service graph (dependency map) | Which service calls which, with traffic, errors and latency on each link | Act 1 builds it; Act 2 walks it |
| Caller and callee | The direction of a call: the caller depends on the callee | Getting this backwards is the most common AI diagnosis failure |
| RED metrics | Rate, Errors, Duration: the three numbers that describe any request-serving service | Every edge on the map carries them |
| Alert storm | One fault triggering many alerts across many services | Beat 4 |
| Alert correlation | Grouping related alerts into one incident by time and dependency | The incident summary tool turns 280 alerts into 1 incident |
| Root cause and symptom | The cause is where the fault started; symptoms are everywhere it spread | The answer names one cause and lists symptoms separately |
| Salience bias | Blaming the loudest, noisiest service instead of the quieter culprit | The DNS and database faults are designed to tempt it |
| Blast radius | Everything affected by one fault | Listed in the verdict and the postmortem |
| Baseline | What normal looks like, from a healthy time window | The map and log patterns are compared with it |
| Silent edge | A dependency that was active at baseline and has stopped | The Kafka fault shows one |
| Exemplar | A sample trace attached to a metric data point, linking a spike to a real request | Lets the agent jump from "latency rose" to an example trace |
| Cardinality | How many distinct label combinations a metric has; high cardinality is costly | Why tools return top-10 lists, not every series |
| MTTD and MTTR | Mean time to detect and mean time to resolve | The business case: shorter diagnosis lowers both |

## Platform layer

This layer is the ground everything runs on: a Kubernetes cluster, built from code.

### Kubernetes, in the words that matter here

- A **pod** is one or more containers running together; a **Deployment** keeps a set number of identical pods running.
- A **Service** gives those pods one stable name and spreads traffic across them. Two Deployments sharing a Service label share its traffic, which is how the bad-config fault reaches half the requests.
- **Namespaces** separate groups of resources; **labels** tag them with owner, team or version. The unknown pod has none.
- **Events** record what the cluster did: scheduling, restarts, failed probes. The agent's "what changed" tool reads them.
- **Helm** installs bundles of Kubernetes resources (charts) with settings (values). Every component in the lab is a Helm chart.

### kind

Kubernetes in Docker: each cluster node is a container. It is standard for CI and local testing, and Chaos Mesh lists it as a test environment. The lab uses one node, because the trace-based service graph needs all the steps of one request to reach the same processor.

### Terraform

Terraform describes infrastructure as code, compares it with what exists (`plan`) and makes the changes (`apply`). It keeps a state file of what it built. The lab uses the tehcyx/kind, helm and kubernetes providers in three stages:

1. **Cluster:** create the kind cluster.
2. **Platform:** install the Helm charts.
3. **Configuration:** apply custom resources (chaos experiments, alert rules) and demo settings.

### Likely follow-ups

| Question | Answer |
| --- | --- |
| Why three Terraform stages? | The Kubernetes provider needs a running cluster when it plans, and custom resources need their definitions installed first. HashiCorp warns that creating a cluster and using it in one apply causes intermittent errors. |
| Why kind, not minikube or k3d? | Any of them would work. kind is the common choice for CI and is what Chaos Mesh tests against. |
| How does this map to production? | The same Helm charts run on EKS, AKS or GKE. Terraform swaps the kind module for the managed-cluster module, and the observability stack becomes the scalable Loki, Tempo and Mimir, or Grafana Cloud. |
| Why a second Deployment for the bad-config fault? | It mirrors a canary release. Changing one ConfigMap does not reliably reach only some pods: environment variables need a restart and mounted files update with a delay. |
| What would you change for a real team? | Remote Terraform state with locking, a CI pipeline running plan on every pull request, and secrets in a vault rather than variables. |

## Telemetry layer

This layer collects what the services emit and makes it queryable. It is the part of the demo that matches what the NOC already runs.

### OpenTelemetry

An open, vendor-neutral standard for producing and moving telemetry: language SDKs that instrument code, a wire protocol (OTLP), and the Collector. The OpenTelemetry Demo is instrumented in about 11 languages, so it behaves like a real mixed estate.

### The Collector

A pipeline that receives telemetry, processes it and exports it. Each pipeline runs receivers, then processors, then exporters; connectors turn one signal into another, such as spans into request metrics. Here the Collector inside otel-lgtm receives everything over OTLP.

### Traces and the service graph (Tempo)

- A trace is one request's journey. Each span records its service, timing and parent span.
- Span **kinds** carry direction. A *client* span in the caller pairs with a *server* span in the callee, and *producer* pairs with *consumer* for queues.
- Tempo's metrics-generator pairs them within a 10-second window and emits `traces_service_graph_request_total` with `client` and `server` labels, plus failure counts and latency histograms.
- An uninstrumented database appears as a **virtual node** named from span attributes. Kafka appears as a direct edge marked `messaging_system`.
- Pairs that never complete are counted as unpaired spans. That is why Kafka edges go silent when consumer lag is high.

### Metrics (Prometheus)

Time series queried with PromQL. The RED numbers per service and per edge come from here. Exemplars attach a trace ID to a data point, so a latency spike links straight to a slow request.

### Logs (Loki)

Loki indexes only labels such as service and namespace, not the log text, which keeps storage cheap; LogQL filters and counts lines. The **pattern ingester** groups lines that differ only in variable parts (IDs, numbers, timestamps) into templates with counts over time. It is off by default, keeps patterns for 3 hours and tracks up to 300 per stream.

### Alerting

Grafana alert rules run PromQL or LogQL on a schedule. Grafana's built-in Alertmanager groups, deduplicates and routes the resulting notifications.

### eBPF and OBI

eBPF lets small, verified programs run safely inside the Linux kernel. OBI (OpenTelemetry eBPF Instrumentation, formerly Grafana Beyla) uses it to observe network connections and HTTP or gRPC calls without touching application code. It runs as a privileged DaemonSet and needs Linux 5.8 or later with BTF type information. Its network metrics are labelled with each pod's Kubernetes owner, which is how the unknown pod is caught.

### Likely follow-ups

| Question | Answer |
| --- | --- |
| How does the map know direction? | From span kinds: the client span sits in the caller, the server span in the callee, linked by parent ID. |
| Why not build the map from traces alone? | Traces miss uninstrumented services, some async hops and external calls. Network flows and the Kubernetes API fill those gaps, and where sources disagree is the interesting finding. |
| Why Loki and not Elasticsearch? | Loki indexes only labels, so it is cheaper at volume; it relies on fast scanning plus patterns. Either can feed the same reduction tools. |
| Why one otel-lgtm container? | It is built for demos and testing and comes pre-wired. Production would run the scalable versions behind the same query APIs. |
| What changes at production scale? | Distributed Loki, Tempo and Mimir, or Grafana Cloud. Service-graph generation needs trace-aware load balancing so every span of a request reaches the same generator. |
| What about sampling? | Sampled traces undercount traffic on the map. Tempo can scale counts back up, and network flows aren't sampled, which is another reason to use both. |
| Is eBPF safe in production? | The kernel verifies eBPF programs before running them. It still needs elevated privileges, so it gets a security review like any node agent. |

## Fault layer

This layer breaks things on purpose, for real, in ways the agent cannot see directly. That is chaos engineering: inject a known fault and learn how the system, and the people or tools watching it, respond.

### Built-in failure flags (flagd)

The OpenTelemetry Demo ships a feature-flag service, flagd, built on the OpenFeature standard. Its flags switch on realistic bugs inside the services. The demo uses four:

- `kafkaQueueProblems`: the Kafka consumers slow down, so the order queue backs up.
- `productCatalogLockContention`: a PostgreSQL table lock slows the product catalog.
- `paymentFailure`: a share of payment calls fail.
- `recommendationCacheFailure`: the recommendation cache grows without limit, so memory leaks.

Read the flag list from the deployed config, not the website, which lags behind releases.

### Chaos Mesh

A CNCF project that turns faults into Kubernetes resources. Each fault type is a custom resource: NetworkChaos, DNSChaos, PodChaos, StressChaos. A daemon on each node applies the fault inside the target pod, for example adding delay with the Linux traffic-control tool. It must be pointed at kind's containerd runtime, since the chart defaults to Docker. DNSChaos runs its own DNS server and can fail or randomise A and AAAA lookups for chosen names.

### The canary config fault

A second Deployment of one service shares the first one's Service label, so it gets about half the traffic. A Git commit changes only its config, so about half of requests fail. This is the one fault that leaves a change record, as a real bad change would.

### Keeping the test fair

The agent's reduction tools hide the fault tooling: Chaos Mesh's objects and events, and flagd's own logs and spans. From its point of view, a flag is a code bug and a NetworkChaos object is a flaky network. Two honest limits: the demo's services log their own flag names ("FeatureFlag 'kafkaQueueProblems' is activated"), as a real app's logs might, and raw Grafana queries can see that the tooling's pods exist, though not which fault is on. A reset button removes the fault; metrics take 2–3 minutes to settle afterwards.

### Likely follow-ups

| Question | Answer |
| --- | --- |
| Isn't a feature flag cheating? | The agent can't see the flags. To it, the fault looks like a bug shipped to production, which is the realistic case. |
| Why no certificate expiry fault? | cert-manager won't issue certificates shorter than 1 hour and renews them automatically. A reliable expiry would need an extra TLS layer built just for the demo. |
| Why no disk or clock faults? | Disk faults need a mounted volume most demo services don't have, and clock faults crashed workloads on Apple Silicon before Chaos Mesh 2.8.8. Both are fragile on a laptop. |
| How would you run chaos in production? | As planned game days: state what normal looks like, start with a small blast radius, set abort conditions, and widen scope only as confidence grows. |
| Why use real faults at all? | Synthetic telemetry would be easy to fake and easy to dismiss. Real faults produce the same messy, partial signals an outage does. |

## AI layer

This layer is where interviewers will push hardest. The core idea: the model is a reasoning engine that chooses questions, and everything around it is ordinary, explainable software.

### The agent loop

1. The model reads its context: instructions, the incident summary, the map and everything so far.
2. It either answers or asks to call a tool, such as "what changed in the last 30 minutes?".
3. The harness, Claude Code, checks the call is allowed, runs it and appends the result.
4. Repeat until the model gives its answer or hits the turn limit.

The model never touches anything directly; every action is a tool call that the harness permits or refuses.

### MCP (Model Context Protocol)

An open standard for connecting AI applications to tools and data. An MCP **server** offers tools; an MCP **client**, here Claude Code, calls them. Any MCP-capable client can use the same servers, so the model is swappable. The demo uses two servers: Grafana's official one for direct queries, and a small custom one for the six summary tools.

### Tool design

Anthropic's published guidance, which the reduction server follows:

- **Few, purposeful tools** rather than a thin wrapper around every API call.
- **Readable names, not IDs.** Returning "checkout" instead of a UUID measurably reduces made-up answers.
- **Small, focused answers.** A short summary by default, a hint on how to narrow the query when results are cut, and error messages that say what to do next.

### Context

Context is the model's working memory for each step. Opus 5.5 can hold up to a million tokens, but bigger is slower, costlier and noisier, so the aim is the smallest set of high-signal information. Every step re-sends the conversation; prompt caching makes the repeated part cheap.

### Effort and thinking

Opus 5.5 always reasons before answering; the effort setting controls how much. Its default is medium, so the demo sets effort explicitly and pins it for rehearsal.

### Headless mode, streaming and replay

`claude -p` runs Claude Code as a script. With `--output-format stream-json`, every step arrives as a JSON event, and the demo screen draws them as they come. A replay is simply a saved event file played back, which is why replays are real runs.

### Guardrails, concretely

- **Claude Code:** only the two MCP servers are allowed; the built-in Bash, Read, Write, Edit, WebFetch and WebSearch tools are explicitly disallowed. Listing allowed tools only auto-approves them; disallowing is what removes the rest.
- **Grafana MCP:** started with `--disable-write`, using a Viewer-role service account. Without that flag it can edit dashboards, alert rules and silences.
- **People:** the agent proposes a fix; a person applies it through normal change control.

### Prompt injection

Any text the agent reads, including a log line, could contain instructions planted by someone else. No model is fully immune. The useful frame is the "lethal trifecta": private data, untrusted content, and a way to send data out. This agent has the first two but not the third. It has no tool that writes or sends anything, so an injected instruction has nothing to act on.

### Evaluation

One good run proves little. Rehearsal runs each fault at least 5 times on fresh injections and reports *k of N*. A useful idea is **pass^k**, the chance that all k runs succeed: 75% per run means only about 42% for 3 out of 3. Rehearsal also includes cases where the right answer is "nothing is wrong".

### Likely follow-ups

| Question | Answer |
| --- | --- |
| Why not fine-tune a model? | The hard part is fetching and weighing fresh evidence, not memorising. Tools give live data; fine-tuning would need labelled incidents and still go stale. |
| Why one agent, not several? | Easier to explain and debug. Grafana's Investigations runs specialist agents in parallel, which can be faster but costs more and is harder to trace. |
| Why Claude? | It is what was used here, and rehearsal results back it. MCP keeps the design model-agnostic, so the scorecard should decide. |
| How do you limit made-up answers? | Evidence linked to every claim, a checklist that demands evidence against, readable names instead of IDs, a stated confidence, and a person reviewing. |
| What would it cost a NOC? | Roughly $1–2 per investigation. At 50 investigations a week that is about $50–100, an estimate to confirm with real usage. |
| Why not add runbooks? | A good next step, but out of scope. The map already plays a similar role: structured context the model starts from. |
| What does the model see of our data? | Only the small tool results, never raw telemetry, and only through the model provider the organisation already approves. |

## What the research says

The honest summary: on clean, single faults, today's agents do well; on messy, realistic incidents, the best are under half right under strict scoring. That is why this design proposes and explains rather than acts, and why it quotes its own measured results.

### Published results

| Study | What it tests | Result | Lesson |
| --- | --- | --- | --- |
| [OpenRCA](https://proceedings.iclr.cc/paper_files/paper/2025/file/d29b8d53678015079e1d245c023e49d2-Paper-Conference.pdf) (ICLR 2025) | 335 failures, 68 GB of telemetry | Claude 3.5 found 11% of root causes | Raw telemetry suits language models poorly |
| [ITBench](https://arxiv.org/html/2502.05352) (IBM) | The same OpenTelemetry shop used in this demo | GPT-4o diagnosed 14%; one scenario passed anywhere from 1 to 8 runs out of 10 | Results vary run to run, so measure repeatedly |
| [AIOpsLab](https://arxiv.org/html/2501.06706) (Microsoft) | Microservice faults, including no-fault cases | 9–45%; most agents reported faults that weren't there | Test the "nothing is wrong" case |
| [ITBench-AA](https://artificialanalysis.ai/articles/itbench-aa-launch) (2026) | Raw Kubernetes snapshots; a run fails if any cause is missed | Best models about 47% (Claude Opus 4.7, at about $5 per task) | Even frontier models struggle with unreduced data |
| [Trajectory study](https://arxiv.org/html/2608.21310) (2026 preprint) | An easier benchmark, RCABench | A Claude Code harness reached 80%; the best system 90% | Simpler, cleaner cases score far higher |
| ["Free to Look"](https://arxiv.org/html/2610.35767) (2026 preprint) | Reduced "evidence packages" versus letting the agent choose | Package alone 65%; simple multi-turn agents 73–77%; fixed scripted pipelines 16–53% | Reduce the data, but let the model choose what to look at |
| [GALA+](https://arxiv.org/pdf/2608.08968) (ASE 2026) | Investigation guided by the service graph | 74% against 48% for a scripted baseline | A dependency map helps |

### Common failure modes, and what the design does about each

From a 2026 analysis of an agent's failed diagnoses; one failure can show several modes.

| Failure mode | Share of failures | Countermeasure in this design |
| --- | --- | --- |
| Reading caller and callee the wrong way round | 86% | Edges written as sentences: "checkout calls payment" |
| Claiming a dependency without checking it | 54% | Checklist step 3, and the trace examples tool |
| Blaming the loudest service (salience bias) | 42% | Walk upstream from the map; checklist step 1, who moved first |
| Treating missing data as healthy | 30% | Tools report drops, silences and missing series |
| Locking onto the first idea | 28% | Checklist step 5: look for evidence against |
| Never comparing with normal | 26% | Every tool compares with a baseline window |

### Why the map matters

Several studies support guiding the agent with a dependency graph. In GALA+, graph-constrained investigation beat a scripted baseline by 26 points. In KRCA, removing dependency drill-down dropped accuracy from 88% to 64%. No study yet isolates "map versus no map" for a frontier general-purpose agent, so say "the evidence points this way" rather than "it's proven".

### How to say it in one breath

"Published results say agents are good at clean faults and still under 50% on messy real incidents. So I designed for the known failure modes: direction, loud symptoms, missing data and anchoring. I measured my own runs, and the agent proposes rather than acts."
