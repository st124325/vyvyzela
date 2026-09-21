"""Checks the transition formulas from 03_Opisanie_dannyh.md ("Изменение
состояния на шаге") against a hand-built one-satellite scenario, and the
documented worked example for job windows."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.resource_env import Environment, validate  # noqa: E402

MODEL = {
    'reserve_soc_pct': 30.0, 'critical_soc_pct': 20.0,
    'charge_efficiency': 0.92, 'discharge_efficiency': 0.95,
    'thermal_tau_s': 1800.0, 'thermal_gain_c_per_w': 0.2,
    'heater_below_c': 5.0, 'payload_min_c': -5.0, 'payload_max_c': 45.0,
    'charge_min_c': 0.0, 'charge_max_c': 45.0,
    'calibration_valid_steps': 48, 'downlink_parallel_limit': 2,
}


def make_scenario(steps=5, solar_w=200.0, thermal_target_c=18.0,
                   initial_temp_c=18.0, initial_soc_pct=50.0):
    sat = {
        'id': 'S01', 'capacity_wh': 100.0, 'initial_soc_pct': initial_soc_pct,
        'initial_temp_c': initial_temp_c, 'base_w': 18.0, 'heater_w': 30.0,
        'calibration_w': 20.0, 'downlink_w': 90.0, 'relay_w': 65.0,
        'initial_calibration_age_steps': 0,
    }
    env_row = {
        'solar_w': [solar_w] * steps,
        'thermal_target_c': [thermal_target_c] * steps,
        'downlink_available': [True] * steps,
        'relay_available': [True] * steps,
    }
    return {
        'schema_version': 'cosmo-B-ops-1.0',
        'meta': {'id': 'unit-test', 'title': 'unit test'},
        'time': {'step_s': 300, 'steps': steps},
        'model': MODEL,
        'satellites': [sat],
        'environment': {'S01': env_row},
        'jobs': [], 'failures': [],
    }


def test_idle_energy_and_temperature_match_formula():
    scenario = make_scenario(solar_w=200.0, thermal_target_c=18.0,
                              initial_temp_c=18.0, initial_soc_pct=50.0)
    validate(scenario)
    env = Environment(scenario)
    rows = env.step({})  # idle for the sole satellite
    row = rows[0]

    # L = base_w + heater(0, since 18 >= 5) + payload(0) = 18
    load_w = 18.0
    delta_wh = (200.0 - load_w) * 300 / 3600 * MODEL['charge_efficiency']
    expected_energy = min(100.0, 50.0 + delta_wh)
    assert math.isclose(row['energy_after_wh'], round(expected_energy, 6), rel_tol=1e-6)

    equilibrium = 18.0 + MODEL['thermal_gain_c_per_w'] * load_w
    expected_temp = equilibrium + (18.0 - equilibrium) * math.exp(-300 / 1800)
    assert math.isclose(row['temp_after_c'], round(expected_temp, 6), rel_tol=1e-6)


def test_heater_engages_below_threshold_and_drains_battery():
    scenario = make_scenario(solar_w=0.0, thermal_target_c=-20.0,
                              initial_temp_c=2.0, initial_soc_pct=90.0)
    env = Environment(scenario)
    row = env.step({})[0]
    # temp 2 < heater_below_c(5) -> heater engages: L = 18 + 30 = 48
    load_w = 48.0
    delta_wh = (0.0 - load_w) * 300 / 3600 / MODEL['discharge_efficiency']
    expected_energy = min(100.0, max(0.0, 90.0 + delta_wh))
    assert math.isclose(row['energy_after_wh'], round(expected_energy, 6), rel_tol=1e-6)
    assert row['heater_w'] == 30.0


def test_job_window_matches_documented_example():
    """release_step=2, deadline_step=5, work_steps=2: steps 2 and 4 (with a
    gap at 3) still finish exactly on the step-5 boundary; starting at 5 is
    too late."""
    scenario = make_scenario(steps=6)
    scenario['jobs'] = [{
        'id': 'JOB-1', 'kind': 'relay', 'release_step': 2, 'deadline_step': 5,
        'work_steps': 2, 'eligible_satellites': ['S01'], 'priority': 2,
        'value_usd': 10.0,
    }]
    env = Environment(scenario)
    for _ in range(2):
        env.step({})  # steps 0, 1: job not yet released
    ok, reason, _ = env.can_execute('S01', {'action': 'job', 'job_id': 'JOB-1'})
    assert ok, reason
    env.step({'S01': {'action': 'job', 'job_id': 'JOB-1'}})  # step 2: 1 of 2 done
    env.step({})  # step 3: gap, progress preserved
    env.step({'S01': {'action': 'job', 'job_id': 'JOB-1'}})  # step 4: completes at boundary 5
    assert env.jobs['JOB-1']['completed_step'] == 5
    assert env.jobs['JOB-1']['remaining_steps'] == 0


def test_below_reserve_blocks_new_operations():
    scenario = make_scenario(solar_w=0.0, initial_soc_pct=29.0)  # below 30% reserve
    scenario['jobs'] = [{
        'id': 'JOB-1', 'kind': 'relay', 'release_step': 0, 'deadline_step': 5,
        'work_steps': 1, 'eligible_satellites': ['S01'], 'priority': 1,
        'value_usd': 5.0,
    }]
    env = Environment(scenario)
    ok, reason, _ = env.can_execute('S01', {'action': 'job', 'job_id': 'JOB-1'})
    assert not ok and reason == 'energy_reserve'


def test_downlink_parallel_limit_enforced_by_planner_side():
    scenario = make_scenario(steps=3)
    scenario['satellites'].append(dict(scenario['satellites'][0], id='S02'))
    scenario['environment']['S02'] = scenario['environment']['S01']
    scenario['satellites'].append(dict(scenario['satellites'][0], id='S03'))
    scenario['environment']['S03'] = scenario['environment']['S01']
    scenario['jobs'] = [
        {'id': f'JOB-{i}', 'kind': 'downlink', 'release_step': 0, 'deadline_step': 3,
         'work_steps': 1, 'eligible_satellites': [sid], 'priority': 3, 'value_usd': 1.0}
        for i, sid in enumerate(('S01', 'S02', 'S03'))
    ]
    env = Environment(scenario)
    rows = env.step({
        'S01': {'action': 'job', 'job_id': 'JOB-0'},
        'S02': {'action': 'job', 'job_id': 'JOB-1'},
        'S03': {'action': 'job', 'job_id': 'JOB-2'},
    })
    executed = [r['executed'] for r in rows]
    assert executed.count('job') == 2  # downlink_parallel_limit == 2
    assert executed.count('idle') == 1
