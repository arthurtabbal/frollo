"""Estado reativo comum: jobs, persistência e política de continuação."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from .. import errors


POLICIES = ("notify", "prompt", "auto")
JOBS_PATH = Path(os.environ.get(
    "FROLLO_JOBS",
    str(Path.home() / ".config" / "frollo" / "jobs.json"),
))


class SessionSupervisor:
    def __init__(self, client, *, path=None, policy="notify"):
        self.client = client
        self.path = Path(path) if path is not None else JOBS_PATH
        self.policy = policy if policy in POLICIES else "notify"
        self.owner_id = uuid.uuid4().hex
        self.jobs = {}
        self._continuations = []
        self._load()

    @property
    def can_control_continuation(self):
        profile = getattr(self.client, "backend_profile", {}) or {}
        capabilities = profile.get("capabilities") or {}
        return bool(capabilities.get("background_continuation_control"))

    @property
    def effective_policy(self):
        return self.policy if self.can_control_continuation else "native"

    def set_policy(self, policy):
        if policy not in POLICIES:
            raise ValueError(f"política de jobs inválida: {policy}")
        self.policy = policy

    def _belongs_here(self, job):
        if (
            job.get("backend") != getattr(self.client, "backend", "claude")
            or job.get("cwd") != getattr(self.client, "cwd", None)
        ):
            return False
        if job.get("owner_id") == self.owner_id:
            return True
        resume_id = getattr(self.client, "resume_id", None)
        if not resume_id or job.get("session_id") != resume_id:
            return False
        owner_pid = job.get("owner_pid")
        if owner_pid == os.getpid():
            return True  # /refresh usa exec e preserva o PID.
        if not isinstance(owner_pid, int) or owner_pid <= 0:
            return True
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            items = data.get("jobs", []) if isinstance(data, dict) else []
            for job in items:
                if isinstance(job, dict) and job.get("id") and self._belongs_here(job):
                    restored = dict(job)
                    if restored.get("status") == "running":
                        restored["status"] = "orphaned"
                        restored["summary"] = "o Frollo reiniciou antes de observar a conclusão"
                    self.jobs[restored["id"]] = restored
            if any(job.get("status") == "orphaned" for job in self.jobs.values()):
                self._save()
        except (OSError, ValueError, TypeError) as exc:
            errors.report_exception(
                "supervisor", exc, severity="warning", code="jobs_load_failed",
                chat=False, tools=False,
            )

    def _all_persisted_jobs(self):
        existing = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                existing = data.get("jobs", []) if isinstance(data, dict) else []
            except (OSError, ValueError, TypeError):
                existing = []
        foreign = [job for job in existing if isinstance(job, dict) and not self._belongs_here(job)]
        return foreign + list(self.jobs.values())

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "jobs": self._all_persisted_jobs()}
            fd, temp_path = tempfile.mkstemp(prefix="jobs.", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                os.replace(temp_path, self.path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except OSError as exc:
            errors.report_exception(
                "supervisor", exc, severity="warning", code="jobs_save_failed",
                chat=False, tools=False,
            )

    def register_job(self, job):
        job_id = job.get("id")
        if not job_id:
            raise ValueError("job sem id")
        if job_id in self.jobs:
            return self.jobs[job_id], False
        stored = {
            **job,
            "backend": getattr(self.client, "backend", "claude"),
            "cwd": getattr(self.client, "cwd", None),
            "owner_id": self.owner_id,
            "owner_pid": os.getpid(),
        }
        self.jobs[job_id] = stored
        self._save()
        return stored, True

    def finish_job(self, update):
        job_id = update.get("id")
        job = self.jobs.setdefault(job_id, {
            "id": job_id,
            "backend": getattr(self.client, "backend", "claude"),
            "cwd": getattr(self.client, "cwd", None),
        })
        changed = job.get("status") != update.get("status")
        job.update({key: value for key, value in update.items() if value is not None})
        if changed and self.can_control_continuation and job.get("status") == "completed":
            self._continuations.append(job_id)
        self._save()
        return job, changed

    def has_running_jobs(self):
        return any(job.get("status") == "running" for job in self.jobs.values())

    def attach_session(self, session_id):
        changed = False
        for job in self.jobs.values():
            if not job.get("session_id"):
                job["session_id"] = session_id
                changed = True
        if changed:
            self._save()

    def orphaned_jobs(self):
        return [job for job in self.jobs.values() if job.get("status") == "orphaned"]

    def next_continuation(self):
        if not self._continuations:
            return None
        job_id = self._continuations.pop(0)
        job = self.jobs.get(job_id, {"id": job_id})
        return {
            "job": job,
            "policy": self.policy,
            "instruction": f"O job {job_id} terminou. Leia o resultado e continue o trabalho.",
        }
