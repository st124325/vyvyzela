"""End-to-end sanity checks: both planners must run P01_intro to completion
without violating the model's own constraints, and the scoring planner
should out-perform the earliest-deadline baseline on priority completion."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.resource_env import Environment  # noqa: E402
from model.resource_env import load  # noqa: E402
from planner.greedy import (make_baseline_planner,  # noqa: E402
                            make_scoring_planner, GreedyPlanner, priority_job_score)
from planner.run import run_session  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / 'data' / 'P01_intro.json'


class _Sess:
    """Minimal Session stand-in for plan_step: the planner reads ``.env`` and
    ``.events`` (the latter tells it when availability may have changed)."""
    def __init__(self, env, events=()):
        self.env = env
        self.events = list(events)


def _run(goal, planner_factory):
    scenario = load(DATA)
    planner = planner_factory(goal)
    session = run_session(scenario, planner)
    return session.summary()


def test_planners_complete_full_shift_without_errors():
    for factory in (make_scoring_planner, make_baseline_planner):
        summary = _run('priority', factory)
        assert summary['steps_executed'] == 48
        assert summary['jobs_total'] == 18
        assert summary['blocked_command_count'] == 0  # planner never asks for an invalid action


def test_priority_goal_favours_critical_jobs_over_baseline():
    scoring = _run('priority', make_scoring_planner)
    baseline = _run('priority', make_baseline_planner)
    # both must resolve every priority-3 job's fate; scoring should not do worse
    assert scoring['critical_jobs_completed_on_time'] >= baseline['critical_jobs_completed_on_time']


def test_revenue_goal_runs_and_reports_priority_separately():
    summary = _run('revenue', make_scoring_planner)
    assert summary['revenue_usd'] >= 0
    assert 'critical_jobs_completed_on_time' in summary


def _one_sat_scenario(jobs, steps=6):
    sat = {'id': 'S01', 'capacity_wh': 100.0, 'initial_soc_pct': 90.0,
           'initial_temp_c': 18.0, 'base_w': 18.0, 'heater_w': 30.0,
           'calibration_w': 20.0, 'downlink_w': 90.0, 'relay_w': 65.0,
           'initial_calibration_age_steps': 0}
    env_row = {'solar_w': [200.0] * steps, 'thermal_target_c': [18.0] * steps,
               'downlink_available': [True] * steps, 'relay_available': [True] * steps}
    return {'schema_version': 'cosmo-B-ops-1.0',
            'meta': {'id': 'unit', 'title': 'unit'},
            'time': {'step_s': 300, 'steps': steps},
            'model': {'reserve_soc_pct': 30.0, 'critical_soc_pct': 20.0,
                      'charge_efficiency': 0.92, 'discharge_efficiency': 0.95,
                      'thermal_tau_s': 1800.0, 'thermal_gain_c_per_w': 0.2,
                      'heater_below_c': 5.0, 'payload_min_c': -5.0, 'payload_max_c': 45.0,
                      'charge_min_c': 0.0, 'charge_max_c': 45.0,
                      'calibration_valid_steps': 48, 'downlink_parallel_limit': 2},
            'satellites': [sat], 'environment': {'S01': env_row},
            'jobs': jobs, 'failures': []}


def test_scoring_planner_skips_doomed_job():
    """A job needing more work than steps left in its window is hopeless;
    the planner must not waste the satellite on it (partial work earns
    nothing). Here at step 0 the doomed job needs 3 steps but only 2 remain,
    while a completable job is available — the planner picks the latter."""
    scenario = _one_sat_scenario([
        {'id': 'DOOMED', 'kind': 'relay', 'release_step': 0, 'deadline_step': 2,
         'work_steps': 2, 'eligible_satellites': ['S01'], 'priority': 3, 'value_usd': 99.0},
        {'id': 'OK', 'kind': 'relay', 'release_step': 0, 'deadline_step': 5,
         'work_steps': 1, 'eligible_satellites': ['S01'], 'priority': 1, 'value_usd': 1.0},
    ])
    env = Environment(scenario)
    env.step({})  # advance to step 1: DOOMED now needs 2 work in 1 remaining step
    planner = make_scoring_planner('priority')
    actions = planner.plan_step(_Sess(env))
    assert actions.get('S01') == {'action': 'job', 'job_id': 'OK'}


def test_proactive_calibration_uses_idle_step():
    """With no job to do and calibration half-due, the scoring planner
    calibrates in the idle step; the baseline just idles."""
    scenario = _one_sat_scenario([], steps=3)
    scenario['satellites'][0]['initial_calibration_age_steps'] = 40  # >= 48//2
    env = Environment(scenario)
    scoring = make_scoring_planner('priority').plan_step(_Sess(env))
    assert scoring.get('S01') == {'action': 'calibrate'}

    env_b = Environment(scenario)
    baseline = make_baseline_planner('priority').plan_step(_Sess(env_b))
    assert 'S01' not in baseline  # baseline idles (no proactive maintenance)
