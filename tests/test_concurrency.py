"""Concurrency and memory-safety of the session registry.

The model steps a session in place, so two requests on one session (an
impatient double-click, or two people sharing a link) must not interleave:
before per-session locking this produced duplicate (step, satellite) journal
rows and a command log that no longer replayed.
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from service.app import app  # noqa: E402
from service import store as store_module  # noqa: E402

client = TestClient(app)


def test_concurrent_advance_keeps_journal_consistent():
    sid = client.post('/api/sessions', json={'scenario': 'P01_intro'}).json()['id']

    errors = []

    def hammer():
        try:
            r = client.post(f'/api/sessions/{sid}/advance', json={'until_step': 48})
            if r.status_code != 200:
                errors.append(r.text)
        except Exception as exc:  # pragma: no cover - would be a regression
            errors.append(repr(exc))

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    record = store_module.store.get(sid)
    env = record.session.env
    assert env.k == 48
    # exactly one journal row per (step, satellite), no duplicates from interleaving
    pairs = [(row['step'], row['satellite_id']) for row in env.trace]
    assert len(pairs) == len(set(pairs)) == 48 * 16
    # Commands hold one entry per *assigned* action (idle satellites are simply
    # absent), so the invariant is uniqueness per (step, satellite), not a fixed
    # count — a duplicate there means two requests wrote the same step.
    cmd_pairs = [(c['step'], c['satellite_id']) for c in record.session.commands]
    assert len(cmd_pairs) == len(set(cmd_pairs))
    assert all(0 <= step < 48 for step, _ in cmd_pairs)


def test_export_of_a_hammered_session_still_replays():
    """The whole point of a consistent journal: it must reproduce the run."""
    from model.operations import replay_episode

    sid = client.post('/api/sessions', json={'scenario': 'P01_intro'}).json()['id']
    threads = [threading.Thread(
        target=lambda: client.post(f'/api/sessions/{sid}/advance', json={'until_step': 48}))
        for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    exported = client.get(f'/api/sessions/{sid}/export').json()
    replayed = replay_episode(exported['initial_scenario'], exported['events'],
                              exported['commands'], exported['steps_executed'])
    assert replayed.summary() == exported['summary']


def test_sessions_are_capped_and_evict_least_recently_used(monkeypatch):
    """A long evaluation must not exhaust host memory; a session still in use
    keeps its place, abandoned ones are dropped."""
    monkeypatch.setattr(store_module, 'MAX_SESSIONS', 3)
    ids = [client.post('/api/sessions', json={'scenario': 'P01_intro'}).json()['id']
           for _ in range(3)]

    # keep the first one "in use" so it is not the least recently used
    assert client.get(f'/api/sessions/{ids[0]}').status_code == 200

    fresh = client.post('/api/sessions', json={'scenario': 'P01_intro'}).json()['id']

    assert client.get(f'/api/sessions/{fresh}').status_code == 200   # newest kept
    assert client.get(f'/api/sessions/{ids[0]}').status_code == 200  # actively used kept
    assert client.get(f'/api/sessions/{ids[1]}').status_code == 404  # oldest idle evicted
