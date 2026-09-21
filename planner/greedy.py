"""Per-step scheduling strategies for the satellite constellation planner.

Both planners share the same assignment engine: score every feasible
(satellite, action) pair with a strategy-specific job-scoring function, then
assign candidates highest score first, honouring one action per satellite,
one executor per job per step, and the shared downlink limit. The library
does not resolve these conflicts itself (see 03_Opisanie_dannyh.md), so the
planner is responsible for it.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

JobScoreFn = Callable[[dict, int, str], float]


def priority_job_score(job: dict, k: int, goal: str) -> float:
    """Scores a job candidate by deadline pressure, priority and value.

    ``slack`` is how many spare steps remain before the job would miss its
    deadline if worked every remaining step; it goes to zero as the job
    becomes unmissable. ``priority`` goal weights priority cubically so
    priority-3 jobs dominate; ``revenue`` goal weights value density instead.
    """
    slack = max(0, (job['deadline_step'] - k) - job['remaining_steps'])
    urgency = 1.0 / (slack + 1)
    density = job['value_usd'] / max(1, job['work_steps'])
    if goal == 'priority':
        return job['priority'] ** 3 * urgency + density * 1e-3
    return density * urgency + job['priority'] * 1e-3


def earliest_deadline_score(job: dict, k: int, goal: str) -> float:
    """Baseline rule for comparison: earliest deadline wins, nothing else."""
    return -job['deadline_step']


class GreedyPlanner:
    name: str

    def __init__(self, goal: str, job_score_fn: JobScoreFn, name: str,
                 calibration_margin: int = 1):
        if goal not in ('priority', 'revenue'):
            raise ValueError(f'Unknown goal: {goal}')
        self.goal = goal
        self.job_score_fn = job_score_fn
        self.name = name
        self.calibration_margin = calibration_margin
        # Index of job id -> eligible satellites, built incrementally as new
        # jobs appear (initial scenario, then add_jobs events). Scenarios can
        # carry thousands of jobs (P04: 8120), so scanning every job for
        # every satellite on every step (O(satellites x jobs)) is wasteful;
        # this keeps per-step candidate generation proportional to the
        # number of (satellite, eligible job) pairs instead.
        self._jobs_by_satellite: dict[str, list[str]] = defaultdict(list)
        self._indexed_job_ids: set[str] = set()

    def _sync_job_index(self, env: Any) -> None:
        new_ids = env.jobs.keys() - self._indexed_job_ids
        for jid in new_ids:
            for sid in env.jobs[jid]['eligible_satellites']:
                self._jobs_by_satellite[sid].append(jid)
        self._indexed_job_ids |= new_ids

    def plan_step(self, session: Any) -> dict[str, dict]:
        env = session.env
        k, m = env.k, env.s['model']
        self._sync_job_index(env)
        candidates: list[tuple[float, str, dict, str | None, str]] = []
        for sid in env.sats:
            if not env.available(sid):
                continue
            for jid in self._jobs_by_satellite.get(sid, ()):
                job = env.jobs[jid]
                if job['completed_step'] is not None or job['remaining_steps'] <= 0:
                    continue
                action = {'action': 'job', 'job_id': jid}
                ok, _, _ = env.can_execute(sid, action)
                if ok:
                    score = self.job_score_fn(job, k, self.goal)
                    candidates.append((score, sid, action, jid, job['kind']))
            age = env.state[sid]['calibration_age_steps']
            if age >= m['calibration_valid_steps'] - self.calibration_margin:
                action = {'action': 'calibrate'}
                ok, _, _ = env.can_execute(sid, action)
                if ok:
                    candidates.append((float('inf'), sid, action, None, 'calibrate'))

        candidates.sort(key=lambda c: c[0], reverse=True)
        actions: dict[str, dict] = {}
        used_sats: set[str] = set()
        used_jobs: set[str] = set()
        downlinks = 0
        for _, sid, action, jid, kind in candidates:
            if sid in used_sats:
                continue
            if jid is not None:
                if jid in used_jobs:
                    continue
                if kind == 'downlink' and downlinks >= m['downlink_parallel_limit']:
                    continue
            actions[sid] = action
            used_sats.add(sid)
            if jid is not None:
                used_jobs.add(jid)
                if kind == 'downlink':
                    downlinks += 1
        return actions


def make_scoring_planner(goal: str) -> GreedyPlanner:
    return GreedyPlanner(goal, priority_job_score, name='scoring-greedy')


def make_baseline_planner(goal: str = 'priority') -> GreedyPlanner:
    return GreedyPlanner(goal, earliest_deadline_score, name='baseline-edf')
