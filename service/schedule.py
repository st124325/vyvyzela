"""Per-satellite schedule for the Gantt timeline view.

Everything here is reconstructed from real state — the executed trace, the
scenario's contact-availability rows and outage intervals — so the timeline
reflects what actually happened and what the constellation can do, not any
invented orbital geometry (the case provides no coordinates; contact
availability is given directly as boolean rows).

Action codes (compact for the canvas): 0 idle, 1 downlink, 2 relay,
3 calibrate.
"""
from __future__ import annotations

from typing import Any

CODE_IDLE, CODE_DOWNLINK, CODE_RELAY, CODE_CALIBRATE = 0, 1, 2, 3


def schedule(env: Any, events: list[dict]) -> dict[str, Any]:
    n = env.s['time']['steps']
    k = env.k

    per_sat = {sid: {'codes': [CODE_IDLE] * k, 'jobs': [None] * k, 'completed': [None] * k}
               for sid in env.sats}
    for row in env.trace:
        sid, step, executed = row['satellite_id'], row['step'], row['executed']
        if executed == 'job':
            job = env.jobs.get(row['requested'].get('job_id'))
            per_sat[sid]['codes'][step] = CODE_DOWNLINK if (job and job['kind'] == 'downlink') else CODE_RELAY
            per_sat[sid]['jobs'][step] = row['requested'].get('job_id')
        elif executed == 'calibrate':
            per_sat[sid]['codes'][step] = CODE_CALIBRATE
        if row['completed_job']:
            per_sat[sid]['completed'][step] = row['completed_job']

    satellites = []
    for sid in sorted(env.sats):
        env_row = env.s['environment'][sid]
        codes = per_sat[sid]['codes']
        satellites.append({
            'id': sid,
            'codes': codes,
            'jobs': per_sat[sid]['jobs'],
            'completed': per_sat[sid]['completed'],
            'downlink_available': env_row['downlink_available'],
            'relay_available': env_row['relay_available'],
            'outage': [[f['start_step'], f['end_step']] for f in env.s['failures']
                       if f['satellite_id'] == sid],
            'counts': {
                'downlink': codes.count(CODE_DOWNLINK),
                'relay': codes.count(CODE_RELAY),
                'calibrate': codes.count(CODE_CALIBRATE),
                'idle': codes.count(CODE_IDLE),
            },
        })

    return {
        'steps_executed': k,
        'total_steps': n,
        'satellites': satellites,
        'events': [{'at_step': e['at_step'], 'type': e['type'], 'id': e['id']} for e in events],
    }
