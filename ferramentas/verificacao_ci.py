"""Verificacoes usadas pelo CI (e reproduziveis localmente).

Subcomandos:

  configuracao
      Carrega a configuracao pela resolucao normal (argumento/variavel/checkout),
      confere 7 temas, 25 fontes, 33 regras e as janelas temporais aprovadas,
      inclusive a continuidade semanal.

  instalacao --checkout CAMINHO
      Para instalacao CONVENCIONAL (wheel em ambiente limpo), executada fora do
      checkout com BRIEFING_CONFIG_DIR definido. Confere que o pacote NAO foi
      importado do checkout, roda as verificacoes de configuracao, confirma o
      erro explicito sem configuracao e o fallback tzdata (PYTHONTZPATH vazio).

Saida diferente de zero indica falha. Somente biblioteca padrao + briefing.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def _falhar(msg: str) -> None:
    print(f"FALHA: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def verificar_configuracao() -> None:
    from zoneinfo import ZoneInfo

    from briefing.configuracao import carregar_configuracao
    from briefing.janela import calcular_janela

    cfg = carregar_configuracao()
    _ok(f"configuracao carregada de {cfg.diretorio} (origem: {cfg.origem})")
    contagens = (len(cfg.temas), len(cfg.fontes), len(cfg.regras.regras))
    if contagens != (7, 25, 33):
        _falhar(f"esperado 7 temas, 25 fontes, 33 regras; obtido {contagens}")
    _ok("7 temas, 25 fontes, 33 regras")

    sp = ZoneInfo("America/Sao_Paulo")
    casos = [
        # (edicao, inicio, fim exclusivo, duracao)
        (date(2026, 11, 30), datetime(2026, 11, 27, 8, tzinfo=sp), datetime(2026, 11, 30, 8, tzinfo=sp), 72),
        (date(2026, 12, 1), datetime(2026, 11, 30, 0, tzinfo=sp), datetime(2026, 12, 1, 8, tzinfo=sp), 32),
        (date(2026, 11, 27), datetime(2026, 11, 26, 0, tzinfo=sp), datetime(2026, 11, 27, 8, tzinfo=sp), 32),
    ]
    regras = cfg.regras.janela
    for edicao, ini, fim, horas in casos:
        j = calcular_janela(edicao, regras)
        if (j.start, j.end_exclusive, j.duracao) != (ini, fim, timedelta(hours=horas)):
            _falhar(f"janela de {edicao}: {j.start} -> {j.end_exclusive} ({j.duracao})")
        _ok(f"janela {edicao:%a %d/%m/%Y}: {j.descrever()} [{horas}h]")

    # Continuidade: 8 semanas de edicoes sem lacunas; sexta->segunda exata.
    janelas, d = [], date(2026, 11, 2)
    while len(janelas) < 40:
        if d.weekday() < 5:
            janelas.append(calcular_janela(d, regras))
        d += timedelta(days=1)
    for a, b in zip(janelas, janelas[1:]):
        if b.start > a.end_exclusive:
            _falhar(f"lacuna entre {a.execution_date} e {b.execution_date}")
        if a.execution_date.weekday() == 4 and b.start != a.end_exclusive:
            _falhar(f"sexta {a.execution_date} -> segunda nao e continua")
    _ok("continuidade semanal sem lacunas (40 edicoes)")


def verificar_instalacao(checkout: Path) -> None:
    import briefing

    origem_pacote = Path(briefing.__file__).resolve()
    checkout = checkout.resolve()
    if origem_pacote.is_relative_to(checkout):
        _falhar(f"pacote importado do checkout: {origem_pacote}")
    if "site-packages" not in origem_pacote.parts:
        _falhar(f"pacote nao esta em site-packages: {origem_pacote}")
    _ok(f"pacote importado da instalacao convencional: {origem_pacote}")
    if Path.cwd().resolve().is_relative_to(checkout):
        _falhar(f"diretorio de trabalho dentro do checkout: {Path.cwd()}")
    if not os.environ.get("BRIEFING_CONFIG_DIR"):
        _falhar("BRIEFING_CONFIG_DIR deve estar definido para este subcomando")

    verificar_configuracao()

    env = {k: v for k, v in os.environ.items() if k != "BRIEFING_CONFIG_DIR"}
    r = subprocess.run(
        [sys.executable, "-c",
         "from briefing.configuracao import carregar_configuracao, ErroConfiguracao\n"
         "try:\n carregar_configuracao(); print('SEM_ERRO')\n"
         "except ErroConfiguracao as e:\n print('ERRO:', e.motivo.split(':')[0])"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0 or r.stdout.strip() != "ERRO: nenhuma configuracao disponivel":
        _falhar(f"sem configuracao, esperado erro explicito; obtido {r.stdout!r} {r.stderr!r}")
    _ok("erro explicito quando nenhuma configuracao esta disponivel")

    env = dict(os.environ, PYTHONTZPATH="")
    r = subprocess.run(
        [sys.executable, "-c",
         "import zoneinfo\nfrom datetime import date\n"
         "assert zoneinfo.TZPATH == (), zoneinfo.TZPATH\n"
         "from briefing.configuracao import carregar_configuracao\n"
         "from briefing.janela import calcular_janela\n"
         "print(calcular_janela(date(2026, 11, 30), carregar_configuracao().regras.janela).start.isoformat())"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0 or r.stdout.strip() != "2026-11-27T08:00:00-03:00":
        _falhar(f"fallback tzdata: {r.stdout!r} {r.stderr!r}")
    _ok("fallback tzdata com PYTHONTZPATH vazio")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("configuracao")
    inst = sub.add_parser("instalacao")
    inst.add_argument("--checkout", type=Path, required=True)
    a = p.parse_args()
    print(f"Python {sys.version.split()[0]} em {sys.platform} ({sys.executable})")
    if a.cmd == "configuracao":
        verificar_configuracao()
    else:
        verificar_instalacao(a.checkout)


if __name__ == "__main__":
    main()
