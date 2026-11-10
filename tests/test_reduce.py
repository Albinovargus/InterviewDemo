from noc.reduce import Handles, fit, movers, pattern_diff, NO_DATA


def test_pattern_diff_classifies_new_spiking_vanished():
    base = {"connected to <_>": 100.0, "order <_> placed": 50.0, "cache hit <_>": 40.0}
    now = {"connected to <_>": 102.0, "order <_> placed": 400.0, "payment failed: <_>": 30.0}
    out = {c.pattern: c.status for c in pattern_diff(base, now)}
    assert out == {"order <_> placed": "spiking", "payment failed: <_>": "new", "cache hit <_>": "vanished"}


def test_pattern_diff_ignores_tiny_new_patterns():
    assert pattern_diff({}, {"rare thing <_>": 1.0}) == []


def test_pattern_diff_orders_by_magnitude():
    base = {"a": 10.0}
    now = {"a": 10.0, "small new": 6.0, "big new": 600.0}
    assert [c.pattern for c in pattern_diff(base, now)] == ["big new", "small new"]


def test_movers_orders_by_first_deviation_and_reports_drops_and_missing():
    t0 = 1000.0
    baseline = {"payment errors": 0.0, "checkout latency": 100.0, "fraud consumer rate": 5.0, "ad rate": 3.0}
    series = {
        "payment errors": [(t0, 0.0), (t0 + 30, 4.0), (t0 + 60, 9.0)],
        "checkout latency": [(t0, 100.0), (t0 + 30, 105.0), (t0 + 60, 400.0)],
        "fraud consumer rate": [(t0, 5.0), (t0 + 30, 0.0), (t0 + 60, 0.0)],
        "ad rate": [],
    }
    out = movers(series, baseline)
    assert [m.signal for m in out[:3]] == ["payment errors", "fraud consumer rate", "checkout latency"]
    assert next(m for m in out if m.signal == "fraud consumer rate").kind == "dropped_to_zero"
    assert next(m for m in out if m.signal == "ad rate").kind == "missing"
    assert next(m for m in out if m.signal == "payment errors").first_moved_at == t0 + 30


def test_movers_quiet_signal_is_not_reported():
    out = movers({"steady": [(1.0, 10.0), (2.0, 10.5)]}, {"steady": 10.0})
    assert out == []


def test_fit_truncates_with_hint_under_budget():
    items = [{"line": "x" * 400} for _ in range(100)]
    kept, note = fit(items, budget_tokens=1000, hint="narrow by service")
    assert 0 < len(kept) < 100
    assert note == f"truncated: showing {len(kept)} of 100; narrow by service"
    kept2, note2 = fit(items[:2], budget_tokens=1000, hint="narrow by service")
    assert len(kept2) == 2 and note2 is None


def test_handles_are_short_and_stable():
    h = Handles()
    a = h.short("4bf92f3577b34da6a3ce929d0e0e4736")
    b = h.short("00f067aa0ba902b7ffffffffffffffff")
    assert (a, b) == ("t1", "t2")
    assert h.short("4bf92f3577b34da6a3ce929d0e0e4736") == "t1"
    assert h.full("t2") == "00f067aa0ba902b7ffffffffffffffff"


def test_no_data_marker_is_explicit():
    assert "no data" in NO_DATA.lower()
    assert "not" in NO_DATA.lower() and "healthy" in NO_DATA.lower()


from noc.reduce import template, count_templates


def test_template_masks_variable_parts():
    assert template("Charge request 7f3a9c21-1b2c-4d5e-8f90-1234567890ab for 42.50 USD") == "Charge request <_> for <_> USD"
    assert template("GET /api/products/OLJCESPC7Z 200 in 13ms") == "GET /api/products/<_> <_> in <_>"
    assert template("connected to 10.244.0.12:5432") == "connected to <_>"


def test_template_keeps_words_and_settings_without_digits():
    line = "Payment request failed. Invalid token. demo.user_context.loyalty_level=gold"
    assert template(line) == line


def test_count_templates_groups_lines():
    lines = ["order 1 placed", "order 22 placed", "cache miss for user 9", "order 3 placed"]
    assert count_templates(lines) == {"order <_> placed": 3, "cache miss for user <_>": 1}


def test_mover_reports_the_value_it_moved_to_as_well_as_the_latest():
    # dropped to zero first, then recovered far above baseline: the label must match the value it describes
    out = movers({"rate": [(1.0, 0.4), (2.0, 0.0), (3.0, 3535.0)]}, {"rate": 0.4})
    assert len(out) == 1
    m = out[0]
    assert (m.kind, m.moved_to, m.latest, m.first_moved_at) == ("dropped_to_zero", 0.0, 3535.0, 2.0)


def test_movers_per_signal_thresholds_catch_one_restart_and_ignore_small_lag():
    series = {"cart · restarts": [(1, 5.0), (2, 6.0)],          # one new restart on a pod that had 5
              "accounting · consumer lag": [(1, 0.0)],           # lag 3 -> 0 is noise, not an outage
              "fraud-detection · consumer lag": [(1, 400.0)]}
    baseline = {"cart · restarts": 5.0, "accounting · consumer lag": 3.0, "fraud-detection · consumer lag": 3.0}
    floors = {"restarts": (0.0, 0.5), "consumer lag": (0.5, 50.0)}
    got = {m.signal: m.kind for m in movers(series, baseline, rel=0.5, abs_floor=0.02, per_signal=floors)}
    assert got == {"cart · restarts": "rose", "fraud-detection · consumer lag": "rose"}
