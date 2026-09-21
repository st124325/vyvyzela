"""Lists bundled scenarios and applies pre-run experiment overrides.

Overrides only touch the knobs the postановка explicitly allows changing
before a run (initial charge of a chosen satellite, solar power coefficient,
priority of an existing job, an outage window) — everything else about the
scenario (capacities, model parameters, job costs) stays untouched, per
"Начальные условия ... не являются параметрами оптимизации".
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from model.resource_env import load, validate

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


def list_scenarios() -> list[dict[str, Any]]:
    out = []
    for path in sorted(DATA_DIR.glob('*.json')):
        scenario = load(path)
        out.append({
            'name': path.stem,
            'title': scenario['meta'].get('title', path.stem),
            'satellites': len(scenario['satellites']),
            'steps': scenario['time']['steps'],
            'jobs': len(scenario['jobs']),
        })
    return out


def load_scenario(name: str) -> dict:
    path = DATA_DIR / f'{name}.json'
    if not path.is_file():
        raise FileNotFoundError(f'Unknown scenario: {name}')
    return load(path)


def apply_overrides(scenario: dict, overrides: dict[str, Any] | None) -> dict:
    if not overrides:
        return scenario
    s = copy.deepcopy(scenario)
    sat_ids = {v['id'] for v in s['satellites']}

    initial_soc = overrides.get('initial_soc') or {}
    for sid, pct in initial_soc.items():
        if sid not in sat_ids:
            raise ValueError(f'Unknown satellite in initial_soc: {sid}')
        if not 0 <= pct <= 100:
            raise ValueError(f'initial_soc for {sid} must be within 0..100')
        next(v for v in s['satellites'] if v['id'] == sid)['initial_soc_pct'] = pct

    multiplier = overrides.get('solar_multiplier')
    if multiplier is not None:
        if multiplier < 0:
            raise ValueError('solar_multiplier must be non-negative')
        for row in s['environment'].values():
            row['solar_w'] = [w * multiplier for w in row['solar_w']]

    job_priority = overrides.get('job_priority') or {}
    if job_priority:
        by_id = {j['id']: j for j in s['jobs']}
        for jid, priority in job_priority.items():
            if jid not in by_id:
                raise ValueError(f'Unknown job in job_priority: {jid}')
            if priority not in (1, 2, 3):
                raise ValueError('priority must be 1, 2 or 3')
            by_id[jid]['priority'] = priority

    for outage in overrides.get('outage') or []:
        sid, start, end = outage['satellite_id'], outage['start_step'], outage['end_step']
        if sid not in sat_ids:
            raise ValueError(f'Unknown satellite in outage: {sid}')
        if not 0 <= start < end <= s['time']['steps']:
            raise ValueError('Invalid outage interval')
        s['failures'].append({'satellite_id': sid, 'start_step': start, 'end_step': end})

    validate(s)
    return s
