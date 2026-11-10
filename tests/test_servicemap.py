from noc.servicemap import Edge, ServiceMap, TraceEdge, Flow, Workload, reconcile, compare


def wl(name, ns="otel-demo", labelled=True, has_service=True):
    labels = {"app.kubernetes.io/component": name} if labelled else {}
    return Workload(name=name, namespace=ns, owner_kind="Deployment" if labelled else "Pod",
                    labels=labels, has_service=has_service)


INVENTORY = {w.name: w for w in [
    wl("frontend"), wl("checkout"), wl("payment"), wl("kafka"), wl("fraud-detection"),
    wl("product-catalog"), wl("astronomy-db"),
    wl("unknown-7f9c", ns="default", labelled=False, has_service=False),
]}


def by_pair(edges):
    return {(e.caller, e.callee): e for e in edges}


def test_trace_and_flow_agree_is_confirmed():
    edges = reconcile(
        [TraceEdge("checkout", "payment", "sync", rate=2.0, error_rate=0.0, p95_ms=12.0)],
        [Flow("checkout", "payment", bytes_rate=900.0)],
        INVENTORY,
    )
    e = by_pair(edges)[("checkout", "payment")]
    assert e.grade == "confirmed"
    assert e.sources == {"traces", "flows"}


def test_flow_direction_does_not_matter_for_corroboration():
    # OBI sees bytes both ways; a response flow payment->checkout still corroborates.
    edges = reconcile(
        [TraceEdge("checkout", "payment", "sync", rate=2.0, error_rate=0.0, p95_ms=12.0)],
        [Flow("payment", "checkout", bytes_rate=400.0)],
        INVENTORY,
    )
    assert by_pair(edges)[("checkout", "payment")].grade == "confirmed"
    assert ("payment", "checkout") not in by_pair(edges)


def test_trace_only_is_likely():
    edges = reconcile([TraceEdge("frontend", "checkout", "sync", 1.0, 0.0, 30.0)], [], INVENTORY)
    assert by_pair(edges)[("frontend", "checkout")].grade == "likely"


def test_flow_only_between_known_services_is_likely_and_points_at_the_service():
    # Only one side has a Service, so that side is the callee.
    inv = dict(INVENTORY)
    inv["fraud-detection"] = wl("fraud-detection", has_service=False)
    edges = reconcile([], [Flow("kafka", "fraud-detection", 50.0)], inv)
    e = by_pair(edges)[("fraud-detection", "kafka")]
    assert e.grade == "likely"
    assert e.kind == "network"


def test_flow_from_unlabelled_workload_is_unexplained():
    edges = reconcile([], [Flow("astronomy-db", "unknown-7f9c", 120.0)], INVENTORY)
    e = by_pair(edges)[("unknown-7f9c", "astronomy-db")]
    assert e.grade == "unexplained"
    assert any("no owner label" in s for s in e.evidence)


def test_messaging_edge_confirmed_when_both_sides_talk_to_broker():
    edges = reconcile(
        [TraceEdge("checkout", "fraud-detection", "messaging", 0.5, 0.0, None)],
        [Flow("checkout", "kafka", 10.0), Flow("kafka", "fraud-detection", 10.0)],
        INVENTORY,
    )
    e = by_pair(edges)[("checkout", "fraud-detection")]
    assert e.grade == "confirmed"
    assert e.kind == "messaging"


def test_sentence_puts_caller_first():
    e = Edge("checkout", "payment", "sync")
    assert e.sentence() == "checkout calls payment"
    m = Edge("checkout", "fraud-detection", "messaging")
    assert m.sentence() == "checkout calls fraud-detection (via Kafka)"


def test_compare_marks_missing_baseline_edge_silent():
    base = ServiceMap(edges=[Edge("checkout", "fraud-detection", "messaging", rate=0.5, grade="confirmed"),
                             Edge("checkout", "payment", "sync", rate=2.0, grade="confirmed")])
    now = ServiceMap(edges=[Edge("checkout", "payment", "sync", rate=2.1, grade="confirmed")])
    diff = compare(base, now)
    assert [(e.caller, e.callee) for e in diff.silent] == [("checkout", "fraud-detection")]
    assert diff.merged_grade("checkout", "fraud-detection") == "silent"
    assert diff.merged_grade("checkout", "payment") == "confirmed"


def test_compare_reports_error_rate_change():
    base = ServiceMap(edges=[Edge("checkout", "payment", "sync", rate=2.0, error_rate=0.0, grade="confirmed")])
    now = ServiceMap(edges=[Edge("checkout", "payment", "sync", rate=2.0, error_rate=0.4, grade="confirmed")])
    diff = compare(base, now)
    (change,) = diff.changed
    assert change.caller == "checkout" and change.callee == "payment"
    assert change.error_rate_before == 0.0 and change.error_rate_now == 0.4


def test_map_json_round_trip():
    m = ServiceMap(edges=[Edge("a", "b", "sync", rate=1.0, grade="likely", sources={"traces"}, evidence=["x"])],
                   captured_at=123.0, window_s=3600)
    again = ServiceMap.from_json(m.to_json())
    assert again == m
