"""Comparison of two continuation branches from a common control state.

O4 requires the comparison to show differences in completed/missed jobs,
priority service, revenue *and remaining resources*, and to explain which
branch is preferable **for the selected goal** — or state honestly that the
results are comparable.

Two subtleties handled here:

* The verdict is judged by the goal actually selected, not by a fixed metric
  order. Judging a revenue-mode pair by priority-3 first would answer a
  question the operator did not ask.
* When the two branches pursue *different* goals (the "compare both goals on
  one scenario" case), declaring a single winner by one metric is misleading:
  each branch optimises a different objective. We then report the trade-off,
  unless one branch happens to dominate on both.
"""
from __future__ import annotations

from typing import Any

# Metric -> is "more" better? Used for both the diff table and the verdict.
HIGHER_IS_BETTER = {
    'jobs_completed': True,
    'critical_jobs_completed_on_time': True,
    'revenue_usd': True,
    'jobs_due_missed': False,
    'work_steps_in_missed_jobs': False,
    'minimum_soc_pct': True,
    'below_reserve_satellite_steps': False,
    'brownout_satellite_steps': False,
    'blocked_command_count': False,
}

GOAL_PRIMARY = {'priority': 'critical_jobs_completed_on_time', 'revenue': 'revenue_usd'}
GOAL_NAME = {'priority': 'приоритетное обслуживание', 'revenue': 'коммерческая отдача'}
METRIC_NAME = {
    'critical_jobs_completed_on_time': 'приоритетных заданий в срок',
    'revenue_usd': 'выручки',
}


def _branch_label(record: Any) -> str:
    return f'{record.label or record.algorithm}/{record.planner.goal}'


def _primary_value(summary: dict, goal: str) -> float:
    return summary[GOAL_PRIMARY[goal]]


def _fmt(metric: str, value: float) -> str:
    return f'${value:,.2f}' if metric == 'revenue_usd' else f'{value:g}'


def compare(a: Any, b: Any) -> dict[str, Any]:
    """Builds the diff table, fairness warnings and the verdict for two
    branch records (``a`` and ``b`` are store Records)."""
    sa, sb = a.session.summary(), b.session.summary()
    goal_a, goal_b = a.planner.goal, b.planner.goal

    diff = {}
    for metric, higher_better in HIGHER_IS_BETTER.items():
        if metric not in sa or metric not in sb:
            continue
        delta = sa[metric] - sb[metric]
        diff[metric] = {
            'a': sa[metric], 'b': sb[metric],
            'delta': round(delta, 6),
            # Who is better on this metric alone: 'a', 'b' or None when equal.
            'better': None if delta == 0 else ('a' if (delta > 0) == higher_better else 'b'),
        }

    warnings = []
    if sa['steps_executed'] != sb['steps_executed']:
        warnings.append(
            f'Ветви досчитаны до разных шагов ({sa["steps_executed"]} и '
            f'{sb["steps_executed"]}). Сравнение корректно только на одинаковом '
            f'числе выполненных шагов — досчитайте обе до одного шага.')
    if len(a.session.events) != len(b.session.events):
        warnings.append(
            f'Ветви получили разное число сообщений ({len(a.session.events)} и '
            f'{len(b.session.events)}). Для честного сравнения события должны '
            f'передаваться в обе ветви одинаково.')

    verdict = _verdict(a, b, sa, sb, goal_a, goal_b, diff)
    return {'diff': diff, 'warnings': warnings, 'verdict': verdict,
            'goal_a': goal_a, 'goal_b': goal_b}


def _verdict(a: Any, b: Any, sa: dict, sb: dict, goal_a: str, goal_b: str,
             diff: dict) -> str:
    la, lb = _branch_label(a), _branch_label(b)

    if goal_a == goal_b:
        metric = GOAL_PRIMARY[goal_a]
        d = diff[metric]
        if d['delta'] != 0:
            winner, wl = ('A', la) if d['better'] == 'a' else ('B', lb)
            return (f'Для выбранной цели «{GOAL_NAME[goal_a]}» предпочтительнее '
                    f'ветвь {winner} ({wl}): {_fmt(metric, d["a"])} против '
                    f'{_fmt(metric, d["b"])} {METRIC_NAME[metric]}.')
        # Primary metric tied — fall back to the secondary one, honestly framed.
        other = 'revenue_usd' if metric != 'revenue_usd' else 'critical_jobs_completed_on_time'
        od = diff.get(other)
        if od and od['delta'] != 0:
            winner, wl = ('A', la) if od['better'] == 'a' else ('B', lb)
            return (f'По основной метрике цели «{GOAL_NAME[goal_a]}» результаты '
                    f'равны ({_fmt(metric, sa[metric])}). Ветвь {winner} '
                    f'({wl}) лучше по сопутствующему показателю: '
                    f'{_fmt(other, od["a"])} против {_fmt(other, od["b"])} '
                    f'{METRIC_NAME[other]}.')
        return ('Результаты сопоставимы: обе ветви дали одинаковый результат '
                'и по основной метрике выбранной цели, и по сопутствующей. '
                'Разные настройки не обязаны давать разные расписания.')

    # Different goals: each branch optimises its own objective.
    da = diff[GOAL_PRIMARY[goal_a]]   # A's own objective
    db = diff[GOAL_PRIMARY[goal_b]]   # B's own objective
    a_wins_own = da['better'] == 'a'
    b_wins_own = db['better'] == 'b'
    if a_wins_own and b_wins_own:
        return (f'Каждая ветвь лучше по своей цели: A ({la}) даёт '
                f'{_fmt(GOAL_PRIMARY[goal_a], da["a"])} {METRIC_NAME[GOAL_PRIMARY[goal_a]]}, '
                f'B ({lb}) даёт {_fmt(GOAL_PRIMARY[goal_b], db["b"])} '
                f'{METRIC_NAME[GOAL_PRIMARY[goal_b]]}. Это и есть компромисс: '
                f'выбор зависит от того, что важнее оператору в эту смену.')
    if a_wins_own and not b_wins_own:
        return (f'Ветвь A ({la}) не уступает и по своей цели, и по цели ветви B — '
                f'на этой смене она предпочтительнее при обеих целях.')
    if b_wins_own and not a_wins_own:
        return (f'Ветвь B ({lb}) не уступает и по своей цели, и по цели ветви A — '
                f'на этой смене она предпочтительнее при обеих целях.')
    return ('Результаты сопоставимы: ни одна ветвь не получила преимущества '
            'по своей цели.')
