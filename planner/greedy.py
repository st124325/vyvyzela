"""Per-step scheduling strategies for the satellite constellation planner.

Both planners share the same assignment engine: score every feasible
(satellite, action) pair, then assign candidates best first, honouring one
action per satellite, one executor per job per step, and the shared downlink
limit. The library does not resolve these conflicts itself (see
03_Opisanie_dannyh.md), so the planner is responsible for it.

Two strategies are provided:

* ``baseline-edf`` — a deliberately simple rule (earliest deadline first) for
  the comparison the postановка requires. No goal awareness, no proactive
  maintenance.
* ``scoring-greedy`` — our planner. A goal-aware score (priority-weighted or
  value-weighted, with deadline urgency as a tie-breaker) plus proactive
  calibration: a satellite that would otherwise idle, whose calibration is at
  least half-due and whose charge is comfortable, calibrates now so a forced
  calibration never has to fall on a scarce contact window ("отложенная
  калибровка может сорвать следующий сеанс связи").

Both planners skip provably-doomed jobs: partial work earns nothing, so
spending a slot on a job that can no longer finish in time is never useful.
A job counts as doomed when the steps on which it *could* still be worked —
i.e. some eligible satellite has the required contact and is not in a known
outage — number fewer than the work it still needs. Counting bare steps is
not enough: a relay job with five steps left but only two contact windows is
already lost, and starting it burns slots that a completable job could use.

Availability is precomputed per satellite as a bitmask over the shift, so the
test is a shift-and-popcount rather than a scan — it has to run for thousands
of jobs on every step. This is shared correctness, applied to both planners,
which also keeps the baseline a fair opponent rather than a strawman.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

JobScoreFn = Callable[[dict, int, str], float]

# Assignment tiers, high to low. A candidate in a higher tier is always
# assigned before any candidate in a lower one; within a tier the score
# decides. Forced calibration must happen before the satellite can work again;
# a real job always beats filling the step with proactive maintenance.
TIER_FORCED_CALIBRATION = 2
TIER_JOB = 1
TIER_PROACTIVE_CALIBRATION = 0


# How much of the work already done on a job still counts against its value
# density. A job pays out only when finished, so what a further step really
# buys is value per *remaining* step (weight 0). Counting the full original
# work (weight 1) ignores progress and makes the planner abandon half-finished
# jobs under contention, throwing that work away. Pure marginal value is too
# eager the other way: on an uncontended shift it lets cheap nearly-done jobs
# crowd out more valuable fresh ones. A quarter weight measured best overall —
# see docs/REPORT.md for the sweep.
DONE_WORK_WEIGHT = 0.25


def priority_job_score(job: dict, k: int, goal: str) -> float:
    """Scores a job candidate by deadline pressure, priority and value.

    ``slack`` is how many spare steps remain before the job would miss its
    deadline if worked every remaining step. ``priority`` goal weights
    priority cubically so priority-3 jobs dominate; ``revenue`` goal weights
    value density instead. Urgency (``1/(slack+1)``) breaks ties toward the
    more time-pressured job.

    The density denominator is the work still to be paid for, with work
    already done discounted by ``DONE_WORK_WEIGHT`` — finishing a job that is
    nearly complete is cheaper than starting an equivalent fresh one, and
    partial work earns nothing if abandoned.
    """
    remaining = job['remaining_steps']
    done = job['work_steps'] - remaining
    slack = max(0, (job['deadline_step'] - k) - remaining)
    urgency = 1.0 / (slack + 1)
    density = job['value_usd'] / max(1.0, remaining + DONE_WORK_WEIGHT * done)
    if goal == 'priority':
        return job['priority'] ** 3 * urgency + density * 1e-3
    return density * urgency + job['priority'] * 1e-3


def earliest_deadline_score(job: dict, k: int, goal: str) -> float:
    """Baseline rule for comparison: earliest deadline wins, nothing else."""
    return -job['deadline_step']


class GreedyPlanner:
    name: str

    def __init__(self, goal: str, job_score_fn: JobScoreFn, name: str,
                 calibration_margin: int = 1, proactive_calibration: bool = False,
                 proactive_soc_margin_pct: float = 30.0):
        if goal not in ('priority', 'revenue'):
            raise ValueError(f'Unknown goal: {goal}')
        self.goal = goal
        self.job_score_fn = job_score_fn
        self.name = name
        self.calibration_margin = calibration_margin
        self.proactive_calibration = proactive_calibration
        self.proactive_soc_margin_pct = proactive_soc_margin_pct
        # Index of job id -> eligible satellites, built incrementally as new
        # jobs appear (initial scenario, then add_jobs events). Scenarios can
        # carry thousands of jobs (P04: 8120), so scanning every job for
        # every satellite on every step (O(satellites x jobs)) is wasteful;
        # this keeps per-step candidate generation proportional to the
        # number of (satellite, eligible job) pairs instead.
        self._jobs_by_satellite: dict[str, list[str]] = defaultdict(list)
        self._indexed_job_ids: set[str] = set()
        # Availability bitmasks and the per-job union of them; both are rebuilt
        # when an event may have changed contacts or outages.
        self._available_mask: dict[tuple[str, str], int] = {}
        self._job_reach_mask: dict[str, int] = {}
        self._availability_events = -1

    def _rebuild_availability(self, env: Any) -> None:
        """Per-satellite bitmask of steps the satellite could work on: the
        contact for that job kind is available and no known outage covers the
        step. Rebuilt whenever an event may have changed availability."""
        steps = env.s['time']['steps']
        outage_mask: dict[str, int] = {}
        for f in env.s['failures']:
            span = ((1 << (f['end_step'] - f['start_step'])) - 1) << f['start_step']
            outage_mask[f['satellite_id']] = outage_mask.get(f['satellite_id'], 0) | span
        self._available_mask = {}
        for sid in env.sats:
            row = env.s['environment'][sid]
            blocked = outage_mask.get(sid, 0)
            for kind in ('downlink', 'relay'):
                flags = row[kind + '_available']
                mask = 0
                for t in range(steps):
                    if flags[t]:
                        mask |= 1 << t
                self._available_mask[(sid, kind)] = mask & ~blocked
        self._job_reach_mask.clear()

    def _reachable_steps(self, env: Any, job: dict, k: int) -> int:
        """How many steps in ``[k, deadline)`` the job could still be worked
        on by *any* eligible satellite. Relay may change executor between
        steps, so the union across eligible satellites is the right count;
        each step is counted once, which also respects one-executor-per-step.

        Ignores energy, temperature and calibration, so it is an upper bound —
        a job failing this test is doomed beyond doubt."""
        mask = self._job_reach_mask.get(job['id'])
        if mask is None:
            mask = 0
            for sid in job['eligible_satellites']:
                mask |= self._available_mask.get((sid, job['kind']), 0)
            self._job_reach_mask[job['id']] = mask
        window = job['deadline_step'] - k
        if window <= 0:
            return 0
        return ((mask >> k) & ((1 << window) - 1)).bit_count()

    def _sync_job_index(self, env: Any) -> None:
        new_ids = env.jobs.keys() - self._indexed_job_ids
        for jid in new_ids:
            for sid in env.jobs[jid]['eligible_satellites']:
                self._jobs_by_satellite[sid].append(jid)
        self._indexed_job_ids |= new_ids

    def _proactive_calibration_ok(self, env: Any, sid: str) -> bool:
        """True if this satellite may usefully calibrate during an idle step:
        maintenance is at least half-due and charge is comfortably above the
        reserve, so we do not burn scarce power on the energy-deficit
        scenario."""
        m = env.s['model']
        age = env.state[sid]['calibration_age_steps']
        if age < m['calibration_valid_steps'] // 2:
            return False
        cap = env.sats[sid]['capacity_wh']
        soc_pct = 100 * env.state[sid]['energy_wh'] / cap
        return soc_pct >= m['reserve_soc_pct'] + self.proactive_soc_margin_pct

    def plan_step(self, session: Any) -> dict[str, dict]:
        env = session.env
        k, m = env.k, env.s['model']
        self._sync_job_index(env)
        # Any received event can add an outage or close downlink windows.
        if len(session.events) != self._availability_events:
            self._rebuild_availability(env)
            self._availability_events = len(session.events)
        candidates: list[tuple[int, float, str, dict, str | None, str]] = []
        for sid in env.sats:
            if not env.available(sid):
                continue
            has_feasible_job = False
            for jid in self._jobs_by_satellite.get(sid, ()):
                job = env.jobs[jid]
                if job['completed_step'] is not None or job['remaining_steps'] <= 0:
                    continue
                action = {'action': 'job', 'job_id': jid}
                ok, _, _ = env.can_execute(sid, action)
                if not ok:
                    continue
                # Checked after can_execute on purpose: the cheap check rejects
                # most jobs (no contact this step), so the reachability count
                # only runs for jobs that are actually workable right now.
                if self._reachable_steps(env, job, k) < job['remaining_steps']:
                    continue  # doomed: too few workable steps left to finish
                has_feasible_job = True
                score = self.job_score_fn(job, k, self.goal)
                candidates.append((TIER_JOB, score, sid, action, jid, job['kind']))

            age = env.state[sid]['calibration_age_steps']
            forced = age >= m['calibration_valid_steps'] - self.calibration_margin
            proactive = (self.proactive_calibration and not has_feasible_job
                         and self._proactive_calibration_ok(env, sid))
            if forced or proactive:
                action = {'action': 'calibrate'}
                ok, _, _ = env.can_execute(sid, action)
                if ok:
                    tier = TIER_FORCED_CALIBRATION if forced else TIER_PROACTIVE_CALIBRATION
                    candidates.append((tier, 0.0, sid, action, None, 'calibrate'))

        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        actions: dict[str, dict] = {}
        used_sats: set[str] = set()
        used_jobs: set[str] = set()
        downlinks = 0
        for _, _, sid, action, jid, kind in candidates:
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
    return GreedyPlanner(goal, priority_job_score, name='scoring-greedy',
                         proactive_calibration=True)


def make_baseline_planner(goal: str = 'priority') -> GreedyPlanner:
    return GreedyPlanner(goal, earliest_deadline_score, name='baseline-edf')
