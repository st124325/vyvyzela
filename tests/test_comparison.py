"""The comparison verdict must answer the question the operator asked: it is
judged by the *selected goal*, reports resource differences, and refuses to
crown a single winner when the two branches pursue different objectives."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from service.comparison import compare  # noqa: E402


def _record(goal, summary, label='x', algorithm='scoring', events=()):
    """Minimal stand-in for a store Record: compare() only needs the goal,
    the summary and the received events."""
    return SimpleNamespace(
        label=label, algorithm=algorithm,
        planner=SimpleNamespace(goal=goal),
        session=SimpleNamespace(summary=lambda s=summary: s, events=list(events)),
    )


def _summary(**over):
    base = {
        'steps_executed': 288, 'jobs_completed': 100, 'jobs_due_missed': 10,
        'critical_jobs_completed_on_time': 50, 'revenue_usd': 1000.0,
        'work_steps_in_missed_jobs': 5, 'minimum_soc_pct': 60.0,
        'below_reserve_satellite_steps': 0, 'brownout_satellite_steps': 0,
        'blocked_command_count': 0,
    }
    base.update(over)
    return base


def test_verdict_follows_revenue_goal_not_priority():
    """Both branches in revenue mode: B earns more but completes fewer
    priority-3 jobs. The verdict must pick B — judging by priority here would
    answer a question the operator did not ask."""
    a = _record('revenue', _summary(critical_jobs_completed_on_time=60, revenue_usd=900.0), label='A')
    b = _record('revenue', _summary(critical_jobs_completed_on_time=40, revenue_usd=1500.0), label='B')
    result = compare(a, b)
    assert 'ветвь B' in result['verdict']
    assert 'коммерческая отдача' in result['verdict']


def test_verdict_follows_priority_goal():
    a = _record('priority', _summary(critical_jobs_completed_on_time=60, revenue_usd=900.0), label='A')
    b = _record('priority', _summary(critical_jobs_completed_on_time=40, revenue_usd=1500.0), label='B')
    result = compare(a, b)
    assert 'ветвь A' in result['verdict']
    assert 'приоритетное обслуживание' in result['verdict']


def test_different_goals_report_tradeoff_not_a_single_winner():
    """A optimises priority and wins on priority; B optimises revenue and wins
    on revenue. Declaring one winner would be misleading."""
    a = _record('priority', _summary(critical_jobs_completed_on_time=60, revenue_usd=900.0), label='A')
    b = _record('revenue', _summary(critical_jobs_completed_on_time=40, revenue_usd=1500.0), label='B')
    verdict = compare(a, b)['verdict']
    assert 'компромисс' in verdict
    assert 'A' in verdict and 'B' in verdict


def test_identical_branches_reported_as_comparable():
    a = _record('priority', _summary(), label='A')
    b = _record('priority', _summary(), label='B')
    assert 'сопоставимы' in compare(a, b)['verdict']


def test_diff_includes_resource_metrics():
    """O4 explicitly requires remaining-resource differences, not just jobs."""
    a = _record('priority', _summary(minimum_soc_pct=55.0, below_reserve_satellite_steps=12))
    b = _record('priority', _summary(minimum_soc_pct=70.0, below_reserve_satellite_steps=0))
    diff = compare(a, b)['diff']
    assert diff['minimum_soc_pct']['better'] == 'b'          # higher charge is better
    assert diff['below_reserve_satellite_steps']['better'] == 'b'  # fewer is better
    assert diff['jobs_due_missed']['better'] is None


def test_warns_when_branches_are_not_comparable():
    a = _record('priority', _summary(steps_executed=288))
    b = _record('priority', _summary(steps_executed=150))
    warnings = compare(a, b)['warnings']
    assert any('разных шагов' in w for w in warnings)


def test_warns_when_event_counts_differ():
    a = _record('priority', _summary(), events=[{'id': 'E1'}])
    b = _record('priority', _summary(), events=[])
    warnings = compare(a, b)['warnings']
    assert any('сообщени' in w for w in warnings)
