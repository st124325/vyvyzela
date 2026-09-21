"""Produces the saved-run examples required by "Что сдаёт команда" (item 4):
full result.json exports (schema cosmo-B-ops-result-1.0) with scenario,
events and commands, replayable with model/operations.py --result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.resource_env import load  # noqa: E402
from planner.greedy import make_scoring_planner  # noqa: E402
from planner.run import run_session  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / 'data'
EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'
OUT = EXAMPLES / 'saved_runs'


def save(name: str, scenario_name: str, goal: str, events=None) -> None:
    scenario = load(DATA / f'{scenario_name}.json')
    session = run_session(scenario, make_scoring_planner(goal), events=events)
    (OUT / f'{name}.json').write_text(
        json.dumps(session.result(), ensure_ascii=False, indent=2, allow_nan=False),
        encoding='utf-8')
    print(name, '->', session.summary())


def main() -> None:
    # Kept small on purpose: P01 (full official schema, tiny) and P02+events
    # (shows the events pipeline). Bigger scenarios (P03/P04) are covered by
    # experiments/results.json instead of full multi-megabyte trace dumps.
    OUT.mkdir(parents=True, exist_ok=True)
    save('P01_priority_scoring', 'P01_intro', 'priority')
    events = json.loads((EXAMPLES / 'events_demo.json').read_text(encoding='utf-8'))['events']
    save('P02_priority_scoring_with_events', 'P02_shift', 'priority', events=events)


if __name__ == '__main__':
    main()
