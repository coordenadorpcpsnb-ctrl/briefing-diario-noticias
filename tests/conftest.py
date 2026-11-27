"""Fixtures compartilhadas. Nenhum teste usa rede, APIs ou relogio do sistema."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from briefing.configuracao import carregar_configuracao, carregar_regras
from briefing.janela import RegrasJanela, calcular_janela

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config"


@pytest.fixture(scope="session")
def configuracao():
    return carregar_configuracao(CONFIG)


@pytest.fixture(scope="session")
def regras() -> RegrasJanela:
    """Regras reais de config/regras.yaml (fonte unica de verdade)."""
    return carregar_regras(CONFIG / "regras.yaml").janela


@pytest.fixture(scope="session")
def janela(regras):
    def _j(ano: int, mes: int, dia: int):
        return calcular_janela(date(ano, mes, dia), regras)

    return _j
