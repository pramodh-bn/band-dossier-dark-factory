"""A running build ends after an abrupt restart, with its units kept (§4, Restarts)."""
from __future__ import annotations

from app import store as state
from conftest import NOW, fixture

ACME = "acme-robotics.test"
SECOND = "second-company.test"


def test_a_running_build_is_ended_failed_with_its_spend(tmp_path):
    """The state file's job and checkpoint are read back as one failed build."""
    path = tmp_path / "state.json"
    store = state.Store(str(path))
    store.reset(fixture())
    _, dossier, created = store.create_or_existing(ACME, NOW)
    assert created
    generation = dossier.generation
    store.add_spend(dossier, generation, 3)
    store.add_spend(dossier, generation, 5)

    recovered = state.Store(str(path))
    dossier = recovered.dossiers[ACME]
    assert dossier.state == "failed" and dossier.running is False and dossier.job is None
    assert dossier.spent_units == 8
    assert dossier.researched_at is None
    assert dossier.builds[-1] == {"time": NOW, "state": "failed", "spent": 8,
                                  "fields": None, "versions": {}}
    assert all(field["value"] is None for field in dossier.fields.values())
    # A later refresh starts a new build normally.
    assert recovered.claim_build(dossier, NOW) is not None


def test_a_refresh_killed_mid_build_keeps_the_last_ready_fields(tmp_path):
    """A recovered refresh leaves the previous fields and adds one failed build."""
    path = tmp_path / "state.json"
    store = state.Store(str(path))
    store.reset(fixture())
    _, dossier, _ = store.create_or_existing(ACME, NOW)
    generation = dossier.generation
    ready = {"value": "Acme Robotics, Inc.", "status": "known",
             "citations": [{"url": "https://acme.test/x", "excerpt": "Acme Robotics, Inc.",
                            "provider": "nimble"}], "candidates": [],
             "true_from": None, "learned_at": NOW, "origins": ["acme.test"],
             "corroborated": False, "corroborated_since": None}
    dossier.fields = {**state.empty_fields(), "legal_name": ready}
    dossier.researched_at = NOW
    dossier.state = "ready"
    dossier.spent_units = 5
    dossier.running = False
    dossier.job = None
    dossier.builds.append({"time": NOW, "state": "ready", "spent": 5,
                           "fields": {**state.empty_fields(), "legal_name": ready},
                           "versions": {}})
    store.persist()
    assert store.claim_build(dossier, NOW) is not None
    store.add_spend(dossier, dossier.generation, 4)

    recovered = state.Store(str(path))
    dossier = recovered.dossiers[ACME]
    assert dossier.state == "failed" and dossier.researched_at == NOW
    assert dossier.fields["legal_name"]["value"] == "Acme Robotics, Inc."
    assert dossier.spent_units == 9
    assert [build["state"] for build in dossier.builds] == ["ready", "failed"]
    assert dossier.builds[-1]["spent"] == 4


def test_a_running_batch_build_is_ended_and_its_budget_kept(tmp_path):
    """A batch's pending build ends too, and the units it spent stay in the batch."""
    path = tmp_path / "state.json"
    store = state.Store(str(path))
    store.reset(fixture())
    batch, started = store.create_batch("u_alice", [ACME, SECOND], 100, NOW)
    assert batch.pending == 2 and len(started) == 2
    first = started[0]
    shared = store.shared_budget(first)
    assert shared is not None and shared.reserve(7)
    store.add_spend(first, first.generation, 7)
    assert batch.spent_units == 7

    recovered = state.Store(str(path))
    batch = recovered.batches[batch.batch_id]
    assert batch.budget_units == 100 and batch.spent_units == 7
    assert batch.pending == 0  # both running builds are recovered as failed
    for domain in (ACME, SECOND):
        assert recovered.dossiers[domain].state == "failed"
    assert batch.view(recovered.dossiers)["state"] == "done"


def test_a_batch_that_spent_nothing_before_the_stop_keeps_zero(tmp_path):
    """Recovery never invents a unit."""
    path = tmp_path / "state.json"
    store = state.Store(str(path))
    store.reset(fixture())
    batch, _ = store.create_batch("u_alice", [ACME], 50, NOW)
    recovered = state.Store(str(path))
    batch = recovered.batches[batch.batch_id]
    assert batch.spent_units == 0 and batch.pending == 0
    assert recovered.dossiers[ACME].spent_units == 0
