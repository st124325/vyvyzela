"""End-to-end sanity checks: both planners must run P01_intro to completion
without violating the model's own constraints, and the scoring planner
should out-perform the earliest-deadline baseline on priority completion."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.resource_env import load  # noqa: E402
from planner.greedy import make_baseline_planner, make_scoring_planner  # noqa: E402
from planner.run import run_session  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / 'data' / 'P01_intro.json'


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
