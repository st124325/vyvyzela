"""Runs baseline (earliest-deadline) vs the scoring planner across every
bundled scenario and both management goals, dumping a comparison table plus
the continuation-after-events case, per postановка's "Исследование и
проверка подхода" requirement. Prints a markdown table and writes
experiments/results.json with the raw summaries for the report.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.resource_env import load  # noqa: E402
from planner.greedy import make_baseline_planner, make_scoring_planner  # noqa: E402
from planner.run import run_session  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / 'data'
EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'

SCENARIOS = ['P01_intro', 'P02_shift', 'P03_energy', 'P04_demand']
GOALS = ['priority', 'revenue']
FIELDS = ['jobs_completed', 'jobs_due_missed', 'critical_jobs_due',
          'critical_jobs_completed_on_time', 'revenue_usd',
          'below_reserve_satellite_steps', 'brownout_satellite_steps',
          'work_steps_in_missed_jobs']


def run_one(scenario_name: str, goal: str, algorithm: str, events=None) -> dict:
    scenario = load(DATA / f'{scenario_name}.json')
    factory = make_scoring_planner if algorithm == 'scoring' else make_baseline_planner
    t0 = time.perf_counter()
    session = run_session(scenario, factory(goal), events=events)
    elapsed = time.perf_counter() - t0
    summary = session.summary()
    return {'scenario': scenario_name, 'goal': goal, 'algorithm': algorithm,
            'elapsed_s': round(elapsed, 2), **{f: summary[f] for f in FIELDS}}


def main() -> None:
    rows = []
    for scenario_name in SCENARIOS:
        for goal in GOALS:
            for algorithm in ('baseline', 'scoring'):
                row = run_one(scenario_name, goal, algorithm)
                rows.append(row)
                print(f"{scenario_name:12s} {goal:9s} {algorithm:9s} "
                      f"completed={row['jobs_completed']:5d} missed={row['jobs_due_missed']:5d} "
                      f"crit_ok={row['critical_jobs_completed_on_time']:3d}/{row['critical_jobs_due']:<3d} "
                      f"revenue=${row['revenue_usd']:10.2f} "
                      f"brownout={row['brownout_satellite_steps']:3d} "
                      f"time={row['elapsed_s']:.2f}s")

    # Continuation-after-events case (required scenario for T3/T4): P02 with
    # the bundled demo message sequence, both goals, both algorithms.
    events = json.loads((EXAMPLES / 'events_demo.json').read_text(encoding='utf-8'))['events']
    for goal in GOALS:
        for algorithm in ('baseline', 'scoring'):
            row = run_one('P02_shift', goal, algorithm, events=events)
            row['scenario'] = 'P02_shift+events_demo'
            rows.append(row)
            print(f"{'P02+events':12s} {goal:9s} {algorithm:9s} "
                  f"completed={row['jobs_completed']:5d} missed={row['jobs_due_missed']:5d} "
                  f"crit_ok={row['critical_jobs_completed_on_time']:3d}/{row['critical_jobs_due']:<3d} "
                  f"revenue=${row['revenue_usd']:10.2f} "
                  f"brownout={row['brownout_satellite_steps']:3d} "
                  f"time={row['elapsed_s']:.2f}s")

    out = Path(__file__).resolve().parent / 'results.json'
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nWrote {out}")


if __name__ == '__main__':
    main()
