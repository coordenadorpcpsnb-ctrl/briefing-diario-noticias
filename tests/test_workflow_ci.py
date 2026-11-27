"""Seguranca estrutural de .github/workflows/ci.yml."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOWS = RAIZ / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$")


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _gatilhos(wf: dict) -> dict:
    # PyYAML (YAML 1.1) interpreta a chave "on" como booleano True.
    return wf.get("on", wf.get(True))


def test_unico_workflow_do_projeto():
    assert sorted(p.name for p in WORKFLOWS.iterdir()) == ["ci.yml"]


def test_gatilhos_permitidos(wf):
    g = _gatilhos(wf)
    assert set(g) == {"pull_request", "push", "workflow_dispatch"}
    assert g["pull_request"]["branches"] == ["main"]
    assert g["push"]["branches"] == ["main"]
    for perigoso in ("pull_request_target", "workflow_run", "schedule", "issue_comment"):
        assert perigoso not in g


def test_sem_filtro_de_caminhos(wf):
    g = _gatilhos(wf)
    for ev in ("pull_request", "push"):
        assert not {"paths", "paths-ignore"} & set(g[ev] or {})


def test_permissoes_minimas(wf):
    assert wf["permissions"] == {"contents": "read"}
    for nome, job in wf["jobs"].items():
        assert "permissions" not in job, f"job {nome} redefine permissoes"


def test_jobs_com_timeout_runner_explicito_e_sem_continue_on_error(wf):
    assert set(wf["jobs"]) == {"qualidade", "portabilidade", "integridade"}
    for nome, job in wf["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] <= 30, nome
        assert "latest" not in str(job["runs-on"]), nome
        assert "continue-on-error" not in job, nome
        for passo in job["steps"]:
            assert "continue-on-error" not in passo, f"{nome}: {passo.get('name')}"


def test_matriz_linux_e_windows(wf):
    m = wf["jobs"]["portabilidade"]["strategy"]
    assert m["matrix"]["os"] == ["ubuntu-24.04", "windows-2025"]
    assert m["fail-fast"] is False


def test_concorrencia(wf):
    c = wf["concurrency"]
    assert "github.event.pull_request.number" in c["group"] and "github.ref" in c["group"]
    assert "pull_request" in str(c["cancel-in-progress"])


def test_actions_fixadas_por_sha_com_versao():
    usos = [m for linha in CI.read_text(encoding="utf-8").splitlines() if (m := _USES.match(linha))]
    assert usos
    for m in usos:
        alvo, resto = m.group(1), m.group(2)
        acao, _, ref = alvo.partition("@")
        assert acao in {"actions/checkout", "actions/setup-python"}, acao
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{alvo}: referencia nao e SHA completo"
        assert re.search(r"#\s*v\d+\.\d+\.\d+", resto), f"{alvo}: falta comentario de versao"


def test_checkout_sem_persistir_credenciais(wf):
    for nome, job in wf["jobs"].items():
        for passo in job["steps"]:
            if str(passo.get("uses", "")).startswith("actions/checkout@"):
                assert passo["with"]["persist-credentials"] is False, nome


def test_sem_interpolacao_de_eventos_em_shell(wf):
    for nome, job in wf["jobs"].items():
        for passo in job["steps"]:
            run = passo.get("run", "")
            assert "${{" not in run, f"{nome}: expressao interpolada em run ({passo.get('name')})"


def test_sem_download_executado_e_sem_secrets():
    # considera apenas conteudo efetivo (comentarios YAML removidos)
    linhas = [l.split(" #")[0] for l in CI.read_text(encoding="utf-8").splitlines()
              if not l.lstrip().startswith("#")]
    texto = "\n".join(linhas)
    assert not re.search(r"\b(curl|wget)\b", texto)
    assert not re.search(r"\bsecrets\.", texto)
    assert "GITHUB_TOKEN" not in texto


def test_instalacoes_usam_hashes(wf):
    for nome, job in wf["jobs"].items():
        for passo in job["steps"]:
            for linha in passo.get("run", "").splitlines():
                if "pip install" in linha and "-r requirements/" in linha:
                    assert "--require-hashes" in linha, f"{nome}: {linha.strip()}"
                elif "pip install" in linha:
                    assert "--no-deps" in linha, f"{nome}: instalacao sem --no-deps: {linha.strip()}"
