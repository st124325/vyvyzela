"""Exercises the HTTP API end to end: create a session, advance it, inject
an event mid-shift, fork for comparison, and export the result."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from service.app import app  # noqa: E402

client = TestClient(app)


def test_scenario_listing():
    resp = client.get('/api/scenarios')
    assert resp.status_code == 200
    names = {s['name'] for s in resp.json()}
    assert {'P01_intro', 'P02_shift', 'P03_energy', 'P04_demand'} <= names


def test_full_session_lifecycle():
    created = client.post('/api/sessions', json={
        'scenario': 'P01_intro', 'goal': 'priority', 'algorithm': 'scoring',
    })
    assert created.status_code == 200, created.text
    sid = created.json()['id']

    advanced = client.post(f'/api/sessions/{sid}/advance', json={'until_step': 10})
    assert advanced.status_code == 200
    assert advanced.json()['observation']['step'] == 10
    assert len(advanced.json()['rows']) == 16 * 10  # 16 satellites x 10 steps

    event = client.post(f'/api/sessions/{sid}/event', json={'event': {
        'id': 'E-TEST', 'at_step': 10, 'type': 'satellite_outage',
        'satellite_ids': ['S01'], 'end_step': 15,
    }})
    assert event.status_code == 200, event.text
    assert event.json()['events_count'] == 1

    switched = client.post(f'/api/sessions/{sid}/goal', json={'goal': 'revenue'})
    assert switched.status_code == 200
    assert switched.json()['goal'] == 'revenue'

    forked = client.post(f'/api/sessions/{sid}/fork', json={'goal': 'priority', 'label': 'branch-priority'})
    assert forked.status_code == 200
    other_id = forked.json()['id']
    assert forked.json()['root_id'] == created.json()['id']

    client.post(f'/api/sessions/{sid}/advance', json={'until_step': 48})
    client.post(f'/api/sessions/{other_id}/advance', json={'until_step': 48})

    compared = client.get(f'/api/sessions/{sid}/compare/{other_id}')
    assert compared.status_code == 200, compared.text
    assert 'verdict' in compared.json()

    exported = client.get(f'/api/sessions/{sid}/export')
    assert exported.status_code == 200
    body = exported.json()
    assert body['schema_version'] == 'cosmo-B-ops-result-1.0'
    assert body['run_metadata']['goal_switches'] == [{'step': 10, 'goal': 'revenue'}]


def test_unknown_scenario_returns_404():
    resp = client.post('/api/sessions', json={'scenario': 'does-not-exist'})
    assert resp.status_code == 404


def test_invalid_fork_algorithm_returns_400_not_404():
    created = client.post('/api/sessions', json={'scenario': 'P01_intro'})
    sid = created.json()['id']
    resp = client.post(f'/api/sessions/{sid}/fork', json={'algorithm': 'not-a-real-algorithm'})
    assert resp.status_code == 400


def test_invalid_goal_returns_400():
    created = client.post('/api/sessions', json={'scenario': 'P01_intro'})
    sid = created.json()['id']
    resp = client.post(f'/api/sessions/{sid}/goal', json={'goal': 'not-a-real-goal'})
    assert resp.status_code == 400


def test_explain_job_shows_per_satellite_timeline():
    created = client.post('/api/sessions', json={'scenario': 'P01_intro', 'algorithm': 'scoring'})
    sid = created.json()['id']
    client.post(f'/api/sessions/{sid}/advance', json={'until_step': 20})
    explained = client.get(f'/api/sessions/{sid}/jobs/JOB-0001/explain')
    assert explained.status_code == 200, explained.text
    body = explained.json()
    assert body['status'] in ('done', 'missed', 'active')
    assert body['eligible_satellites'] == ['S01']
    assert all(row['satellite_id'] == 'S01' for row in body['timeline'])
    assert body['timeline']  # JOB-0001's window (0-19) overlaps the 20 executed steps


def test_explain_unknown_job_returns_404():
    created = client.post('/api/sessions', json={'scenario': 'P01_intro'})
    sid = created.json()['id']
    resp = client.get(f'/api/sessions/{sid}/jobs/NOPE/explain')
    assert resp.status_code == 404


def test_invalid_event_returns_400_and_preserves_state():
    created = client.post('/api/sessions', json={'scenario': 'P01_intro'})
    sid = created.json()['id']
    bad = client.post(f'/api/sessions/{sid}/event', json={'event': {
        'id': 'E-BAD', 'at_step': 5, 'type': 'satellite_outage',  # at_step != current step (0)
        'satellite_ids': ['S01'], 'end_step': 10,
    }})
    assert bad.status_code == 400
    state = client.get(f'/api/sessions/{sid}').json()
    assert state['observation']['step'] == 0
    assert state['events_count'] == 0
