import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from lib.runner.supervisor import SessionSupervisor


def _client(*, backend="claude", control=False, cwd="/repo", resume_id=None):
    return SimpleNamespace(
        backend=backend,
        cwd=cwd,
        resume_id=resume_id,
        backend_profile={
            "capabilities": {"background_continuation_control": control},
        },
    )


def test_persiste_job_e_recupera_running_como_orfao(tmp_path):
    path = tmp_path / "jobs.json"
    supervisor = SessionSupervisor(_client(), path=path)
    job, created = supervisor.register_job({
        "id": "bg1", "status": "running", "session_id": "sess-1",
    })

    assert created is True
    assert job["cwd"] == "/repo"
    restored = SessionSupervisor(_client(resume_id="sess-1"), path=path)
    assert restored.jobs["bg1"]["status"] == "orphaned"
    assert "reiniciou" in restored.jobs["bg1"]["summary"]


def test_preserva_jobs_de_outro_backend_ou_diretorio(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"version": 1, "jobs": [{
        "id": "foreign", "backend": "codex", "cwd": "/other", "status": "completed",
    }]}))

    supervisor = SessionSupervisor(_client(), path=path)
    supervisor.register_job({"id": "local", "status": "running"})
    persisted = json.loads(path.read_text())["jobs"]

    assert {job["id"] for job in persisted} == {"foreign", "local"}


def test_nao_adota_job_de_outra_instancia_sem_resume(tmp_path):
    path = tmp_path / "jobs.json"
    first = SessionSupervisor(_client(), path=path)
    first.register_job({"id": "bg1", "status": "running", "session_id": "sess-1"})

    second = SessionSupervisor(_client(), path=path)

    assert second.jobs == {}
    assert json.loads(path.read_text())["jobs"][0]["status"] == "running"


@pytest.mark.parametrize("policy", ["notify", "prompt", "auto"])
def test_conclusao_controlavel_enfileira_politica(policy, tmp_path):
    supervisor = SessionSupervisor(_client(backend="codex", control=True), path=tmp_path / "jobs.json", policy=policy)
    supervisor.register_job({"id": "bg1", "status": "running"})
    job, changed = supervisor.finish_job({"id": "bg1", "status": "completed"})

    assert changed is True
    assert job["status"] == "completed"
    pending = supervisor.next_continuation()
    assert pending["policy"] == policy
    assert pending["job"]["id"] == "bg1"


def test_backend_sem_controle_deixa_wakeup_com_o_provedor(tmp_path):
    supervisor = SessionSupervisor(_client(control=False), path=tmp_path / "jobs.json", policy="prompt")
    supervisor.register_job({"id": "bg1", "status": "running"})
    supervisor.finish_job({"id": "bg1", "status": "completed"})

    assert supervisor.effective_policy == "native"
    assert supervisor.next_continuation() is None


def test_rejeita_politica_desconhecida(tmp_path):
    supervisor = SessionSupervisor(_client(), path=tmp_path / "jobs.json")
    with pytest.raises(ValueError, match="política"):
        supervisor.set_policy("talvez")
