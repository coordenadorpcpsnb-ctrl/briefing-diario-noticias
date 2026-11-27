"""Higiene do repositorio: sem caches/temporarios versionados nem credenciais reais.

Valores ficticios usados nos testes de redacao de credenciais (ex.: TOKEN123,
SENHA_SECRETA) nao correspondem aos formatos de credenciais reais verificados.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

PADROES_CREDENCIAIS = {
    "token GitHub": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "chave AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "chave privada": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    "token Slack": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "chave de API (sk-)": re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{32,}\b"),
}
PROIBIDOS = re.compile(
    r"(^|/)(__pycache__|\.pytest_cache|\.venv[^/]*|venv|build|dist|[^/]+\.egg-info)(/|$)|\.py[cod]$|(^|/)\.env(\.|$)"
)


@pytest.fixture(scope="module")
def versionados() -> list[str]:
    if shutil.which("git") is None or not (RAIZ / ".git").exists():
        pytest.fail("git e checkout do repositorio sao necessarios para este teste")
    r = subprocess.run(["git", "ls-files", "-z"], cwd=RAIZ, capture_output=True, check=True)
    return [p for p in r.stdout.decode("utf-8").split("\0") if p]


def test_sem_caches_ou_temporarios_versionados(versionados):
    assert [p for p in versionados if PROIBIDOS.search(p)] == []


def test_sem_credenciais_reais(versionados):
    achados = []
    for caminho in versionados:
        arq = RAIZ / caminho
        if not arq.is_file():
            continue
        texto = arq.read_bytes().decode("utf-8", errors="replace")
        for nome, padrao in PADROES_CREDENCIAIS.items():
            if padrao.search(texto):
                achados.append(f"{caminho}: {nome}")
    assert achados == []


def test_padroes_detectam_exemplos_sinteticos():
    # Controle: os padroes reconhecem formatos reais (montados aqui, nao versionados).
    assert PADROES_CREDENCIAIS["token GitHub"].search("ghp_" + "a" * 36)
    assert PADROES_CREDENCIAIS["chave AWS"].search("AKIA" + "A" * 16)
    assert not PADROES_CREDENCIAIS["token GitHub"].search("TOKEN123")
