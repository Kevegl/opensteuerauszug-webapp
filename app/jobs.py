"""Einfache Hintergrundjobs mit Live-Log (Polling)."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable


class Job:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.status = "running"  # running | done | error
        self.lines: list[str] = []
        self.results: list[dict] = []
        self.started = time.time()
        self.finished: float | None = None
        self._lock = threading.Lock()

    def log(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            return {
                "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "lines": self.lines[since:], "next": len(self.lines), "results": list(self.results),
            }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def running(self) -> Job | None:
        with self._lock:
            for j in self.jobs.values():
                if j.status == "running":
                    return j
        return None

    def start(self, kind: str, title: str, func: Callable[[Job], bool]) -> Job | None:
        """Startet func(job) in einem Thread. func gibt True bei Erfolg zurück. None, wenn schon ein Job läuft."""
        with self._lock:
            if any(j.status == "running" for j in self.jobs.values()):
                return None
            job = Job(kind, title)
            self.jobs[job.id] = job

        def runner() -> None:
            try:
                ok = func(job)
                job.status = "done" if ok else "error"
            except Exception as exc:  # noqa: BLE001
                job.log(f"FEHLER: {exc}")
                job.status = "error"
            finally:
                job.finished = time.time()
                job.log("Fertig." if job.status == "done" else "Beendet mit Fehlern.")

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)
