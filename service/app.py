"""REST API for the constellation planning service.

The web UI (web/) talks to this over HTTP; nothing here reimplements the
resource/eligibility rules — every constraint check goes through
``model.resource_env`` via ``Session``/``GreedyPlanner``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from service import scenarios
from service.comparison import compare
from service.diagnostics import diagnostics
from service.schedule import schedule
from service.store import Record, store

WEB_DIR = Path(__file__).resolve().parent.parent / 'web'

app = FastAPI(title='Constellation Planning Service')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])


class CreateSessionRequest(BaseModel):
    scenario: str
    goal: str = 'priority'
    algorithm: str = 'scoring'
    overrides: dict[str, Any] | None = None
    label: str = ''


class AdvanceRequest(BaseModel):
    steps: int | None = None
    until_step: int | None = None


class EventRequest(BaseModel):
    event: dict[str, Any]


class GoalRequest(BaseModel):
    goal: str


class ForkRequest(BaseModel):
    goal: str | None = None
    algorithm: str | None = None
    label: str = ''


def record_view(record: Record) -> dict[str, Any]:
    session = record.session
    return {
        'id': record.id,
        'root_id': record.root_id,
        'fork_step': record.fork_step,
        'label': record.label,
        'scenario_name': record.scenario_name,
        'algorithm': record.algorithm,
        'goal': record.planner.goal,
        'goal_switches': record.goal_switches,
        'total_steps': session.env.s['time']['steps'],
        'capacities_wh': {sid: v['capacity_wh'] for sid, v in session.env.sats.items()},
        'observation': session.observation(),
        'summary': session.summary(),
        'events_count': len(session.events),
        'commands_count': len(session.commands),
    }


def _advance(record: Record, steps: int | None, until_step: int | None) -> list[dict]:
    total = record.session.env.s['time']['steps']
    target = until_step if until_step is not None else record.session.env.k + (steps or 1)
    if target < record.session.env.k:
        raise ValueError('Cannot advance to a step before the current one')
    target = min(target, total)
    rows: list[dict] = []
    while record.session.env.k < target:
        actions = record.planner.plan_step(record.session)
        rows.extend(record.session.advance(actions))
    return rows


@app.exception_handler(KeyError)
async def _not_found(_request: Any, exc: KeyError) -> None:
    raise HTTPException(status_code=404, detail=str(exc))


@app.exception_handler(FileNotFoundError)
async def _scenario_not_found(_request: Any, exc: FileNotFoundError) -> None:
    raise HTTPException(status_code=404, detail=str(exc))


@app.exception_handler(ValueError)
async def _bad_request(_request: Any, exc: ValueError) -> None:
    raise HTTPException(status_code=400, detail=str(exc))


@app.get('/api/scenarios')
def list_scenarios() -> list[dict[str, Any]]:
    return scenarios.list_scenarios()


@app.post('/api/sessions')
def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    scenario = scenarios.load_scenario(req.scenario)
    scenario = scenarios.apply_overrides(scenario, req.overrides)
    record = store.create(scenario, req.scenario, req.goal, req.algorithm, req.label)
    return record_view(record)


@app.get('/api/sessions')
def list_sessions() -> list[dict[str, Any]]:
    return [record_view(r) for r in store.list()]


@app.get('/api/sessions/{session_id}')
def get_session(session_id: str) -> dict[str, Any]:
    return record_view(store.get(session_id))


@app.post('/api/sessions/{session_id}/advance')
def advance_session(session_id: str, req: AdvanceRequest) -> dict[str, Any]:
    record = store.get(session_id)
    rows = _advance(record, req.steps, req.until_step)
    view = record_view(record)
    view['rows'] = rows
    return view


@app.post('/api/sessions/{session_id}/event')
def send_event(session_id: str, req: EventRequest) -> dict[str, Any]:
    record = store.get(session_id)
    record.session.apply_event(req.event)
    return record_view(record)


@app.post('/api/sessions/{session_id}/goal')
def set_goal(session_id: str, req: GoalRequest) -> dict[str, Any]:
    record = store.get(session_id)
    if req.goal not in ('priority', 'revenue'):
        raise ValueError('goal must be "priority" or "revenue"')
    record.planner.goal = req.goal
    record.goal_switches.append({'step': record.session.env.k, 'goal': req.goal})
    return record_view(record)


@app.post('/api/sessions/{session_id}/fork')
def fork_session(session_id: str, req: ForkRequest) -> dict[str, Any]:
    record = store.fork(session_id, req.goal, req.algorithm, req.label)
    return record_view(record)


@app.get('/api/sessions/{session_id}/jobs/{job_id}/explain')
def explain_job(session_id: str, job_id: str) -> dict[str, Any]:
    """Per-step account of what every eligible satellite actually did while
    this job's window was open: who worked it, who was busy with something
    else, who was unavailable, and why a requested attempt was rejected.
    Distinguishes idle/simply-not-attempted from a rejected attempt, per O3
    ("причины простоя, отказа в операции и срыва задания не следует
    смешивать") — it does not claim the job was unwinnable, only what
    happened under this run.
    """
    env = store.get(session_id).session.env
    job = env.jobs.get(job_id)
    if job is None:
        raise KeyError(f'Unknown job: {job_id}')
    window_end = min(job['deadline_step'], env.k)
    eligible = set(job['eligible_satellites'])
    timeline = [
        {'step': row['step'], 'satellite_id': row['satellite_id'],
         'requested': row['requested'], 'executed': row['executed'], 'reason': row['reason']}
        for row in env.trace
        if row['satellite_id'] in eligible and job['release_step'] <= row['step'] < window_end
    ]
    status = 'done' if job['completed_step'] is not None else (
        'missed' if job['deadline_step'] <= env.k else 'active')
    return {'job': job, 'status': status, 'eligible_satellites': sorted(eligible), 'timeline': timeline}


@app.delete('/api/sessions/{session_id}')
def delete_session(session_id: str) -> dict[str, str]:
    store.delete(session_id)
    return {'status': 'deleted'}


@app.get('/api/sessions/{session_id}/diagnostics')
def session_diagnostics(session_id: str) -> dict[str, Any]:
    """Aggregate loss analysis: blocked-command reasons and a breakdown of
    missed jobs into hard task limits vs planner-influenceable misses."""
    return diagnostics(store.get(session_id).session.env)


@app.get('/api/sessions/{session_id}/schedule')
def session_schedule(session_id: str) -> dict[str, Any]:
    """Per-satellite executed schedule for the Gantt timeline, with contact
    windows, outages and event markers — all from real state, no orbits."""
    record = store.get(session_id)
    return schedule(record.session.env, record.session.events)


@app.get('/api/sessions/{session_id}/export')
def export_session(session_id: str) -> dict[str, Any]:
    record = store.get(session_id)
    result = record.session.result()
    result['run_metadata'] = dict(result['run_metadata'], label=record.label,
                                   root_id=record.root_id, fork_step=record.fork_step,
                                   goal_switches=record.goal_switches)
    return result


@app.get('/api/sessions/{a_id}/compare/{b_id}')
def compare_sessions(a_id: str, b_id: str) -> dict[str, Any]:
    a, b = store.get(a_id), store.get(b_id)
    if a.root_id != b.root_id:
        raise ValueError('Sessions do not share a common branch point; fork one from the other to compare')
    return {'a': record_view(a), 'b': record_view(b), **compare(a, b)}


# Registered last so it only catches paths the API routes above did not.
app.mount('/', StaticFiles(directory=WEB_DIR, html=True), name='web')
