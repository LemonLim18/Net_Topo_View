from __future__ import annotations

import asyncio
import inspect
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RefreshOperationStatus:
    name: str
    status: str = "idle"
    last_started_at: Optional[str] = None
    last_completed_at: Optional[str] = None
    last_error: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    last_trigger: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "last_started_at": self.last_started_at,
            "last_completed_at": self.last_completed_at,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "last_trigger": self.last_trigger,
        }


@dataclass
class RefreshJobStatus:
    job_id: str
    target: str
    status: str = "queued"
    created_at: str = field(default_factory=_utc_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    requested_by: str = "tool"
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "target": self.target,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "requested_by": self.requested_by,
            "error": self.error,
            "result": self.result,
        }


class RefreshStateManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: Dict[str, Callable[[], Any]] = {}
        self._jobs: Dict[str, RefreshJobStatus] = {}
        self._operations: Dict[str, RefreshOperationStatus] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._jobs.clear()
            self._operations = {
                "live_topology": RefreshOperationStatus(name="live_topology"),
                "inventory": RefreshOperationStatus(name="inventory"),
                "planned_topology": RefreshOperationStatus(name="planned_topology"),
            }

    def register_handler(self, target: str, handler: Callable[[], Any]) -> None:
        with self._lock:
            self._handlers[target] = handler

    def record_start(self, operation: str, trigger: str = "system") -> None:
        with self._lock:
            status = self._operations.setdefault(operation, RefreshOperationStatus(name=operation))
            status.status = "running"
            status.last_started_at = _utc_now()
            status.last_error = None
            status.last_trigger = trigger

    def record_success(self, operation: str, result: Optional[Dict[str, Any]] = None, trigger: str = "system") -> None:
        with self._lock:
            status = self._operations.setdefault(operation, RefreshOperationStatus(name=operation))
            status.status = "success"
            status.last_completed_at = _utc_now()
            status.last_error = None
            status.last_result = result
            status.last_trigger = trigger

    def record_skipped(self, operation: str, result: Optional[Dict[str, Any]] = None, trigger: str = "system") -> None:
        with self._lock:
            status = self._operations.setdefault(operation, RefreshOperationStatus(name=operation))
            status.status = "skipped"
            status.last_completed_at = _utc_now()
            status.last_error = None
            status.last_result = result
            status.last_trigger = trigger

    def record_failure(self, operation: str, error: str, result: Optional[Dict[str, Any]] = None, trigger: str = "system") -> None:
        with self._lock:
            status = self._operations.setdefault(operation, RefreshOperationStatus(name=operation))
            status.status = "failed"
            status.last_completed_at = _utc_now()
            status.last_error = error
            status.last_result = result
            status.last_trigger = trigger

    def get_operation_statuses(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {name: operation.to_dict() for name, operation in self._operations.items()}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def _run_handler(self, handler: Callable[[], Any]) -> Any:
        result = handler()
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    def run_refresh(self, target: str, requested_by: str = "tool") -> Dict[str, Any]:
        if target == "all":
            results: Dict[str, Any] = {}
            overall_status = "completed"
            for subtarget in ("live_topology", "inventory", "planned_topology"):
                subresult = self.run_refresh(subtarget, requested_by=requested_by)
                results[subtarget] = subresult
                if subresult.get("status") != "completed":
                    overall_status = "partial"
            return {"status": overall_status, "target": "all", "results": results}

        with self._lock:
            handler = self._handlers.get(target)

        if handler is None:
            return {
                "status": "unavailable",
                "target": target,
                "requested_by": requested_by,
                "error": f"No refresh handler registered for target '{target}'",
            }

        try:
            result = self._run_handler(handler)
            return {
                "status": "completed",
                "target": target,
                "requested_by": requested_by,
                "result": result,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "target": target,
                "requested_by": requested_by,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=3),
            }

    def start_refresh_job(self, target: str, requested_by: str = "tool") -> Dict[str, Any]:
        job = RefreshJobStatus(job_id=f"refresh-{uuid.uuid4().hex[:10]}", target=target, requested_by=requested_by)
        with self._lock:
            self._jobs[job.job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job.job_id,),
            name=f"refresh-job-{job.job_id}",
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _utc_now()

        result = self.run_refresh(job.target, requested_by=job.requested_by)

        with self._lock:
            job = self._jobs[job_id]
            job.completed_at = _utc_now()
            job.result = result
            if result.get("status") in {"completed", "partial"}:
                job.status = "completed"
            else:
                job.status = "failed"
                job.error = result.get("error")


_REFRESH_MANAGER = RefreshStateManager()


def get_refresh_state_manager() -> RefreshStateManager:
    return _REFRESH_MANAGER