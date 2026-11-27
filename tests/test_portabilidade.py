"""Grupo K -- portabilidade de fusos (zoneinfo + tzdata).

O fallback e testado em subprocesso isolado com ``PYTHONTZPATH`` vazio, sem
alterar a configuracao de fusos do processo de teste nem do sistema.
Testado somente em Linux; portabilidade em outros sistemas e prevista, nao
verificada.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

CONFIG = Path(__file__).resolve().parents[1] / "config"
FUSOS = ["America/Sao_Paulo", "UTC", "America/New_York", "Asia/Tokyo", "Europe/London"]


def _rodar(codigo: str, **env_extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONTZPATH", "TZ", "BRIEFING_CONFIG_DIR"}}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(codigo)],
        env=env, capture_output=True, text=True, timeout=60,
    )


class TestGrupoKPortabilidade:
    @pytest.mark.parametrize("fuso", FUSOS)
    def test_resolucao_de_fusos(self, fuso):
        assert ZoneInfo(fuso).key == fuso

    def test_offsets_conhecidos(self):
        ref = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
        assert ref.astimezone(ZoneInfo("America/Sao_Paulo")).utcoffset() == timedelta(hours=-3)
        assert ref.astimezone(ZoneInfo("Asia/Tokyo")).utcoffset() == timedelta(hours=9)
        assert ref.astimezone(ZoneInfo("America/New_York")).utcoffset() == timedelta(hours=-5)

    def test_tzdata_declarado_e_instalado(self):
        import tzdata  # noqa: F401

        texto = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        assert '"tzdata>=' in texto

    def test_fallback_tzdata_sem_base_do_sistema(self):
        r = _rodar(
            f"""
            import zoneinfo
            from datetime import date
            assert zoneinfo.TZPATH == (), zoneinfo.TZPATH  # base do sistema desativada
            for f in {FUSOS!r}:
                zoneinfo.ZoneInfo(f)
            from briefing.configuracao import carregar_regras
            from briefing.janela import calcular_janela
            r = carregar_regras({str(CONFIG / 'regras.yaml')!r}).janela
            j = calcular_janela(date(2026, 11, 30), r)
            print(j.start.isoformat(), j.end_exclusive.isoformat(), j.duracao)
            """,
            PYTHONTZPATH="",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.split() == ["2026-11-27T08:00:00-03:00", "2026-11-30T08:00:00-03:00", "3", "days,", "0:00:00"]

    def test_sem_tzdata_e_sem_base_do_sistema_falha(self):
        """Controle: prova que o sucesso acima vem do pacote tzdata."""
        r = _rodar(
            """
            import sys, zoneinfo
            sys.modules["tzdata"] = None  # simula ausencia do pacote
            try:
                zoneinfo.ZoneInfo("America/Sao_Paulo")
            except zoneinfo.ZoneInfoNotFoundError:
                print("NAO_ENCONTRADO")
            """,
            PYTHONTZPATH="",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "NAO_ENCONTRADO"

    def test_ambiente_do_processo_de_teste_nao_alterado(self):
        # o PYTHONTZPATH vazio so existe dentro dos subprocessos
        assert os.environ.get("PYTHONTZPATH") != ""
