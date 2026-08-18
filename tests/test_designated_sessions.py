from custom_components.enocean_ptm216b.designated_sessions import (
    DesignatedSessionCounter,
)


def test_burst_observations_share_one_session():
    counter = DesignatedSessionCounter(designated_identifier="device-a")

    counter.observe("device-a", 10.0)
    counter.observe("device-a", 10.2)
    counter.observe("device-a", 10.9)

    assert counter.observation_count == 3
    assert counter.session_count == 1


def test_unrelated_identifiers_are_ignored():
    counter = DesignatedSessionCounter(designated_identifier="device-a")

    counter.observe("device-b", 10.0)

    assert counter.observation_count == 0
    assert counter.session_count == 0


def test_exact_inactivity_boundary_starts_a_new_session():
    counter = DesignatedSessionCounter(designated_identifier="device-a")

    counter.observe("device-a", 10.0)
    counter.observe("device-a", 11.0)

    assert counter.observation_count == 2
    assert counter.session_count == 2


def test_no_designation_counts_nothing():
    counter = DesignatedSessionCounter()

    counter.observe("device-a", 10.0)

    assert counter.observation_count == 0
    assert counter.session_count == 0
