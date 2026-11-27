"""Grupo G -- integridade do portal e das edicoes historicas.

Testes gerais e permanentes: NAO fixam a quantidade de edicoes, para permitir
novas publicacoes legitimas. A comparacao de hashes da baseline e feita na
auditoria da Fase 1A (fora da suite).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
EDICOES = RAIZ / "docs" / "edicoes"
MANIFESTO = EDICOES / "manifest.json"
_ARQ_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")


def _manifesto() -> dict:
    return json.loads(MANIFESTO.read_text(encoding="utf-8"))


def test_portal_existe():
    assert (RAIZ / "docs" / "index.html").is_file()


def test_manifesto_valido():
    m = _manifesto()
    assert set(m) == {"updated_at", "editions"}
    date.fromisoformat(m["updated_at"])
    assert isinstance(m["editions"], list) and m["editions"]
    for e in m["editions"]:
        assert set(e) == {"date"}
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"])
        date.fromisoformat(e["date"])


def test_sem_datas_duplicadas():
    datas = [e["date"] for e in _manifesto()["editions"]]
    assert len(datas) == len(set(datas))


def test_toda_data_do_manifesto_tem_arquivo():
    for e in _manifesto()["editions"]:
        assert (EDICOES / f"{e['date']}.html").is_file(), e["date"]


def test_sem_edicoes_orfas():
    datas = {e["date"] for e in _manifesto()["editions"]}
    arquivos = {m.group(1) for p in EDICOES.iterdir() if (m := _ARQ_RE.match(p.name))}
    assert arquivos - datas == set(), "arquivo de edicao fora do manifesto"


def test_updated_at_nao_anterior_a_ultima_edicao():
    m = _manifesto()
    assert m["updated_at"] >= max(e["date"] for e in m["editions"])
