"""In-memory registry of running sessions.

Each browser run gets its own session id; forked comparison branches keep a
``root_id`` pointing at the session they diverged from, so the compare
endpoint can refuse to compare unrelated runs (O4 requires identical
conditions up to the branch point).

Two concerns this module owns:

* **Concurrency.** A session's ``Session``/``Environment`` is mutable state
  stepped forward in place. Two requests touching the same session at once
  (an impatient double-click on "advance", or two people on one link) would
  interleave inside ``env.step`` and corrupt the journal — duplicated
  (step, satellite) rows and a command log that no longer replays. Each
  record therefore carries its own lock, and every mutating endpoint holds it.
  The registry lock only guards the dictionary, never the stepping.

* **Memory.** A finished 288-step, 48-satellite run keeps a full trace and
  costs tens of megabytes, and ``fork`` deep-copies all of it. Sessions are
  capped and the least recently used ones are dropped, so a long evaluation
  session cannot exhaust the host.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from model.operations import Session
from planner.greedy import GreedyPlanner, make_baseline_planner, make_scoring_planner

PLANNER_FACTORIES = {'scoring': make_scoring_planner, 'baseline': make_baseline_planner}

# A completed P04 run (8120 jobs, 288 steps, 48 satellites) costs ~46 MB, so the
# default leaves room on a small host. Override with VYVYZELA_MAX_SESSIONS.
MAX_SESSIONS = max(2, int(os.environ.get('VYVYZELA_MAX_SESSIONS', '20')))


@dataclass
class Record:
    id: str
    session: Session
    planner: GreedyPlanner
    algorithm: str
    scenario_name: str
    root_id: str
    fork_step: int
    label: str = ''
    goal_switches: list[dict[str, Any]] = field(default_factory=list)
    # Held for the whole of any operation that steps or mutates the session.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    last_used: float = field(default_factory=time.monotonic, repr=False)


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, Record] = {}

    def create(self, scenario: dict, scenario_name: str, goal: str,
               algorithm: str, label: str = '') -> Record:
        if algorithm not in PLANNER_FACTORIES:
            raise ValueError(f'Unknown algorithm: {algorithm}')
        sid = str(uuid.uuid4())
        planner = PLANNER_FACTORIES[algorithm](goal)
        session = Session(scenario, run_metadata={
            'goal': goal,
            # Short key the API selects by, plus the planner's precise name, so
            # a saved run names the exact algorithm the docs describe.
            'algorithm': algorithm,
            'algorithm_name': planner.name,
            'version': '1.0', 'parameters': {},
        })
        record = Record(id=sid, session=session, planner=planner, algorithm=algorithm,
                         scenario_name=scenario_name, root_id=sid, fork_step=0, label=label)
        with self._lock:
            self._records[sid] = record
            self._evict_if_needed()
        return record

    def get(self, session_id: str) -> Record:
        with self._lock:
            record = self._records.get(session_id)
            if record is not None:
                record.last_used = time.monotonic()
        if record is None:
            raise KeyError(f'Unknown session: {session_id}')
        return record

    def _evict_if_needed(self) -> None:
        """Drop least recently used sessions past the cap. Caller holds the
        registry lock. A session someone is actively using keeps refreshing
        ``last_used``, so only abandoned ones are collected."""
        while len(self._records) > MAX_SESSIONS:
            oldest = min(self._records.values(), key=lambda r: r.last_used)
            del self._records[oldest.id]

    def fork(self, session_id: str, goal: str | None, algorithm: str | None,
              label: str = '') -> Record:
        parent = self.get(session_id)
        new_id = str(uuid.uuid4())
        new_goal = goal or parent.planner.goal
        new_algorithm = algorithm or parent.algorithm
        if new_algorithm not in PLANNER_FACTORIES:
            raise ValueError(f'Unknown algorithm: {new_algorithm}')
        planner = PLANNER_FACTORIES[new_algorithm](new_goal)
        # Copy the parent while it cannot be stepped, so the branch starts from
        # a whole state rather than one caught mid-step.
        with parent.lock:
            session = parent.session.fork()
            fork_step = parent.session.env.k
        session.run_metadata = dict(session.run_metadata, goal=new_goal,
                                     algorithm=new_algorithm,
                                     algorithm_name=planner.name,
                                     forked_from=parent.root_id, fork_step=fork_step)
        record = Record(id=new_id, session=session, planner=planner, algorithm=new_algorithm,
                         scenario_name=parent.scenario_name, root_id=parent.root_id,
                         fork_step=fork_step, label=label,
                         goal_switches=list(parent.goal_switches))
        with self._lock:
            self._records[new_id] = record
            self._evict_if_needed()
        return record

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._records.pop(session_id, None)

    def list(self) -> list[Record]:
        with self._lock:
            return list(self._records.values())


store = Store()
