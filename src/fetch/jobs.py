from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class Job:
    id: str
    state: str = "queued"
    stage: str = "Preparing"
    current: int = 0
    total: int = 0
    filename: str = ""
    result: str = ""
    error: str = ""


class JobStore:
    """Small thread-safe store for jobs owned by this app process."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()

    def create(self) -> Job:
        job = Job(id=str(uuid4()))
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: object) -> Job:
        with self._lock:
            job = replace(self._jobs[job_id], **changes)
            self._jobs[job_id] = job
            return job
