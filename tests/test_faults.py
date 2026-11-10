import pytest

from noc.faults import FAULTS, LabState, residue, set_flag


FLAGS = {"flags": {
    "paymentFailure": {"state": "ENABLED", "defaultVariant": "off",
                       "variants": {"off": 0, "50%": 0.5, "100%": 1}},
    "kafkaQueueProblems": {"state": "ENABLED", "defaultVariant": "off", "variants": {"off": 0, "on": 100}},
}}


def test_set_flag_changes_only_the_default_variant():
    out = set_flag(FLAGS, "paymentFailure", "50%")
    assert out["flags"]["paymentFailure"]["defaultVariant"] == "50%"
    assert out["flags"]["kafkaQueueProblems"]["defaultVariant"] == "off"
    assert FLAGS["flags"]["paymentFailure"]["defaultVariant"] == "off"  # input untouched


def test_set_flag_rejects_unknown_variant():
    with pytest.raises(ValueError):
        set_flag(FLAGS, "paymentFailure", "75%")


def test_residue_lists_everything_left_behind():
    state = LabState(flags_on={"paymentFailure": "50%"}, chaos_objects=["networkchaos/pg-delay"],
                     canary_replicas=1)
    assert residue(state) == ["flag paymentFailure=50%", "chaos networkchaos/pg-delay", "canary cart-canary replicas=1"]


def test_clean_state_has_no_residue():
    assert residue(LabState(flags_on={}, chaos_objects=[], canary_replicas=0)) == []


def test_every_fault_names_an_accepted_cause():
    assert set(FAULTS) == {"kafka-lag", "db-lock", "payment-failure", "cache-leak",
                           "pg-network-delay", "dns-failure", "bad-config"}
    for f in FAULTS.values():
        assert f.truth_service in f.accept
        assert f.mechanism in {"flag", "chaos", "commit"}
