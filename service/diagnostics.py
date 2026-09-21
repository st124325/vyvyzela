"""Fleet-level loss analysis: why work was not done, and — for missed jobs —
whether the cause was a hard task limit or something the planner influenced.

This complements the per-job ``explain`` view with an aggregate picture, and
it is careful to keep the two apart, as O3 requires ("различают ограничения
задачи и недостатки своего алгоритма"):

* ``no_contact_window`` — over the job's window, no eligible satellite ever
  had the required contact available (and was not in an outage). The job was
  unwinnable regardless of scheduling; this is a limit of the scenario, not
  of the planner.
* ``had_opportunity`` — there was at least one feasible-looking contact step,
  so the miss came from contention, energy/thermal/calibration limits, or the
  planner deprioritising it. Any concrete rejection reasons the planner did
  hit for this job (from the trace) are attached so the operator can see
  which constraint bit.

The contact-window test uses only the static scenario availability (contacts
and outages, both already reflecting any applied events); it is a necessary
condition for feasibility, so a negative result is a confident "hard limit".
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

SAMPLE_CAP = 100  # cap per-job lists; scenarios can miss thousands of jobs (P04)


def _in_outage(env: Any, sid: str, step: int) -> bool:
    return any(f['satellite_id'] == sid and f['start_step'] <= step < f['end_step']
               for f in env.s['failures'])


def _had_contact_window(env: Any, job: dict) -> bool:
    """True if some eligible satellite had the required contact available and
    was not in an outage on some step of the job's (elapsed) window."""
    kind_key = job['kind'] + '_available'
    end = min(job['deadline_step'], env.k)
    for sid in job['eligible_satellites']:
        availability = env.s['environment'][sid][kind_key]
        for step in range(job['release_step'], end):
            if availability[step] and not _in_outage(env, sid, step):
                return True
    return False


def _job_row(job: dict, rejections: dict[str, int]) -> dict[str, Any]:
    return {
        'id': job['id'], 'kind': job['kind'], 'priority': job['priority'],
        'value_usd': job['value_usd'],
        'work_done': job['work_steps'] - job['remaining_steps'],
        'work_steps': job['work_steps'],
        'attempt_rejections': rejections,
    }


def diagnostics(env: Any) -> dict[str, Any]:
    blocked = Counter(r['reason'] for r in env.trace
                      if r['reason'] not in ('accepted', 'idle'))
    rejections_by_job: dict[str, Counter] = defaultdict(Counter)
    for row in env.trace:
        requested = row['requested']
        if requested.get('action') == 'job' and row['reason'] not in ('accepted', 'idle'):
            rejections_by_job[requested['job_id']][row['reason']] += 1

    missed = [j for j in env.jobs.values()
              if j['deadline_step'] <= env.k and j['completed_step'] is None]
    # Surface the most important losses first (priority, then value).
    missed.sort(key=lambda j: (j['priority'], j['value_usd']), reverse=True)

    no_contact: list[dict] = []
    had_opportunity: list[dict] = []
    for job in missed:
        row = _job_row(job, dict(rejections_by_job.get(job['id'], {})))
        (had_opportunity if _had_contact_window(env, job) else no_contact).append(row)

    return {
        'blocked_reasons': dict(blocked.most_common()),
        'idle_satellite_steps': sum(1 for r in env.trace if r['reason'] == 'idle'),
        'missed_total': len(missed),
        'missed_no_contact_window_total': len(no_contact),
        'missed_had_opportunity_total': len(had_opportunity),
        'missed_no_contact_window': no_contact[:SAMPLE_CAP],
        'missed_had_opportunity': had_opportunity[:SAMPLE_CAP],
    }
