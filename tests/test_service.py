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


def test_diagnostics_breaks_down_misses():
    created = client.post('/api/sessions', json={
        'scenario': 'P02_shift', 'goal': 'priority', 'algorithm': 'scoring'})
    sid = created.json()['id']
    client.post(f'/api/sessions/{sid}/advance', json={'until_step': 288})
    diag = client.get(f'/api/sessions/{sid}/diagnostics')
    assert diag.status_code == 200, diag.text
    body = diag.json()
    # the two categories must sum to the total missed count
    assert (body['missed_no_contact_window_total'] + body['missed_had_opportunity_total']
            == body['missed_total'])
    assert isinstance(body['blocked_reasons'], dict)
    # sample lists are capped
    assert len(body['missed_had_opportunity']) <= 100


def test_diagnostics_flags_unwinnable_job_as_hard_limit():
    """A downlink job whose only satellite never has a contact window during
    its span must be classified as a task limit (no_contact_window), not as a
    planner miss."""
    from model.resource_env import Environment
    from service.diagnostics import diagnostics

    scenario = {
        'schema_version': 'cosmo-B-ops-1.0',
        'meta': {'id': 'unit', 'title': 'unit'},
        'time': {'step_s': 300, 'steps': 4},
        'model': {'reserve_soc_pct': 30.0, 'critical_soc_pct': 20.0,
                  'charge_efficiency': 0.92, 'discharge_efficiency': 0.95,
                  'thermal_tau_s': 1800.0, 'thermal_gain_c_per_w': 0.2,
                  'heater_below_c': 5.0, 'payload_min_c': -5.0, 'payload_max_c': 45.0,
                  'charge_min_c': 0.0, 'charge_max_c': 45.0,
                  'calibration_valid_steps': 48, 'downlink_parallel_limit': 2},
        'satellites': [{'id': 'S01', 'capacity_wh': 100.0, 'initial_soc_pct': 90.0,
                        'initial_temp_c': 18.0, 'base_w': 18.0, 'heater_w': 30.0,
                        'calibration_w': 20.0, 'downlink_w': 90.0, 'relay_w': 65.0,
                        'initial_calibration_age_steps': 0}],
        'environment': {'S01': {'solar_w': [200.0] * 4, 'thermal_target_c': [18.0] * 4,
                                'downlink_available': [False] * 4,  # never any contact
                                'relay_available': [True] * 4}},
        'jobs': [{'id': 'NOCONTACT', 'kind': 'downlink', 'release_step': 0,
                  'deadline_step': 3, 'work_steps': 1, 'eligible_satellites': ['S01'],
                  'priority': 3, 'value_usd': 50.0}],
        'failures': [],
    }
    env = Environment(scenario)
    for _ in range(3):
        env.step({})  # let the job's window elapse; it can never be worked
    body = diagnostics(env)
    assert body['missed_total'] == 1
    assert body['missed_no_contact_window_total'] == 1
    assert body['missed_had_opportunity_total'] == 0
    assert body['missed_no_contact_window'][0]['id'] == 'NOCONTACT'
    assert body['missed_no_contact_window'][0]['work_done'] == 0


def test_schedule_returns_per_satellite_timeline():
    created = client.post('/api/sessions', json={
        'scenario': 'P01_intro', 'goal': 'priority', 'algorithm': 'scoring'})
    sid = created.json()['id']
    client.post(f'/api/sessions/{sid}/advance', json={'until_step': 20})
    sch = client.get(f'/api/sessions/{sid}/schedule')
    assert sch.status_code == 200, sch.text
    body = sch.json()
    assert body['steps_executed'] == 20
    assert body['total_steps'] == 48
    assert len(body['satellites']) == 16
    sat = body['satellites'][0]
    # executed-action arrays cover the executed steps; availability spans the shift
    assert len(sat['codes']) == 20
    assert len(sat['downlink_available']) == 48
    assert set(sat['codes']) <= {0, 1, 2, 3}
    assert sat['counts']['downlink'] + sat['counts']['relay'] + \
           sat['counts']['calibrate'] + sat['counts']['idle'] == 20


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


def _minimal_scenario(sid='UPL-1', steps=4):
    return {
        'schema_version': 'cosmo-B-ops-1.0',
        'meta': {'id': sid, 'title': 'Загруженный сценарий'},
        'time': {'step_s': 300, 'steps': steps},
        'model': {'reserve_soc_pct': 30.0, 'critical_soc_pct': 20.0,
                  'charge_efficiency': 0.92, 'discharge_efficiency': 0.95,
                  'thermal_tau_s': 1800.0, 'thermal_gain_c_per_w': 0.2,
                  'heater_below_c': 5.0, 'payload_min_c': -5.0, 'payload_max_c': 45.0,
                  'charge_min_c': 0.0, 'charge_max_c': 45.0,
                  'calibration_valid_steps': 48, 'downlink_parallel_limit': 2},
        'satellites': [{'id': 'S01', 'capacity_wh': 100.0, 'initial_soc_pct': 90.0,
                        'initial_temp_c': 18.0, 'base_w': 18.0, 'heater_w': 30.0,
                        'calibration_w': 20.0, 'downlink_w': 90.0, 'relay_w': 65.0,
                        'initial_calibration_age_steps': 0}],
        'environment': {'S01': {'solar_w': [200.0] * steps,
                                'thermal_target_c': [18.0] * steps,
                                'downlink_available': [True] * steps,
                                'relay_available': [True] * steps}},
        'jobs': [{'id': 'J1', 'kind': 'relay', 'release_step': 0, 'deadline_step': 3,
                  'work_steps': 1, 'eligible_satellites': ['S01'], 'priority': 3,
                  'value_usd': 25.0}],
        'failures': [],
    }


def test_uploaded_scenario_becomes_runnable():
    """The jury may bring an additional scenario of the same format."""
    up = client.post('/api/scenarios', json={'scenario': _minimal_scenario(), 'name': 'jury-run'})
    assert up.status_code == 200, up.text
    meta = up.json()
    assert meta['name'] == 'jury-run' and meta['source'] == 'uploaded'
    assert meta['satellites'] == 1 and meta['steps'] == 4

    assert any(s['name'] == 'jury-run' for s in client.get('/api/scenarios').json())

    created = client.post('/api/sessions', json={'scenario': 'jury-run'})
    assert created.status_code == 200, created.text
    sid = created.json()['id']
    done = client.post(f'/api/sessions/{sid}/advance', json={'until_step': 4})
    assert done.status_code == 200
    assert done.json()['summary']['steps_executed'] == 4


def test_malformed_uploaded_scenario_is_rejected():
    bad = _minimal_scenario()
    del bad['satellites']          # violates the published schema
    resp = client.post('/api/scenarios', json={'scenario': bad})
    assert resp.status_code == 400
    # the broken upload must not become selectable
    assert not any(s['title'] == 'Загруженный сценарий' and s['name'].startswith('UPL')
                   for s in client.get('/api/scenarios').json())


def test_uploaded_scenario_name_does_not_clash_with_bundled():
    """Uploading under a bundled name must not shadow the bundled scenario."""
    up = client.post('/api/scenarios', json={'scenario': _minimal_scenario(), 'name': 'P01_intro'})
    assert up.status_code == 200
    assert up.json()['name'] != 'P01_intro'
    # the bundled P01 still loads with its own 16 satellites
    bundled = client.post('/api/sessions', json={'scenario': 'P01_intro'}).json()
    assert len(bundled['observation']['state']) == 16
