"""Drives a full Session to completion with a given planner, step by step."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.operations import Session  # noqa: E402
from model.resource_env import load  # noqa: E402


def run_session(scenario: dict, planner: Any, run_metadata: dict | None = None,
                 events: list[dict] | None = None) -> Session:
    """Runs ``planner`` to the end of ``scenario``, applying ``events`` (each
    tagged with ``at_step``) exactly at the step they announce."""
    meta = {'goal': planner.goal, 'algorithm': planner.name, 'version': '1.0'}
    meta.update(run_metadata or {})
    session = Session(scenario, run_metadata=meta)
    events_by_step: dict[int, list[dict]] = {}
    for event in events or []:
        events_by_step.setdefault(event['at_step'], []).append(event)
    steps = scenario['time']['steps']
    while session.env.k < steps:
        for event in events_by_step.get(session.env.k, []):
            session.apply_event(event)
        actions = planner.plan_step(session)
        session.advance(actions)
    return session


def run_scenario_file(path: str, planner: Any, run_metadata: dict | None = None,
                       events_path: str | None = None) -> Session:
    scenario = load(path)
    events = None
    if events_path:
        import json
        events = json.loads(Path(events_path).read_text(encoding='utf-8'))['events']
    return run_session(scenario, planner, run_metadata, events)
