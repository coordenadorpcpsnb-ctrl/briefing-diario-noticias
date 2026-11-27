"""Integridade de docs/ contra a baseline 9d2f2d9 (politica da Fase 1A.2).

Politica DESTA fase: nenhuma alteracao em docs/ (inclusao, modificacao ou
remocao). Na Fase 6 a politica devera evoluir para permitir, de forma
controlada, novas edicoes, atualizacao autorizada do manifesto e do portal e
revisoes historicas aprovadas -- nao implementado aqui.

Verifica:
1. O arquivo de hashes registrado corresponde aos objetos Git da baseline
   (hashes SHA-256 completos, recalculados de ``git show``).
2. Os arquivos atuais tem exatamente esses hashes.
3. ``git diff`` entre a baseline e HEAD em docs/ esta vazio.

Requer historico com o commit da baseline (checkout com fetch-depth: 0).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

BASELINE = "9d2f2d9be264f53856e4501a3d64aec0d5cd2407"
RAIZ = Path(__file__).resolve().parents[1]
REGISTRO = RAIZ / "auditoria" / "docs-baseline-9d2f2d9.sha256"


def _git(*args: str, binario: bool = False):
    r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, check=False)
    if r.returncode != 0:
        print(f"FALHA: git {' '.join(args)}: {r.stderr.decode(errors='replace')}", file=sys.stderr)
        raise SystemExit(1)
    return r.stdout if binario else r.stdout.decode()


def main() -> None:
    falhas = []
    registro = {}
    for linha in REGISTRO.read_text(encoding="utf-8").splitlines():
        h, caminho = linha.split(maxsplit=1)
        if len(h) != 64:
            falhas.append(f"hash abreviado no registro: {caminho}")
        registro[caminho] = h

    arquivos_baseline = set(_git("ls-tree", "-r", "--name-only", BASELINE, "--", "docs").split())
    if set(registro) != arquivos_baseline:
        falhas.append(f"registro difere dos arquivos da baseline: {sorted(set(registro) ^ arquivos_baseline)}")

    for caminho, esperado in sorted(registro.items()):
        do_git = hashlib.sha256(_git("show", f"{BASELINE}:{caminho}", binario=True)).hexdigest()
        if do_git != esperado:
            falhas.append(f"{caminho}: registro {esperado} != baseline git {do_git}")
        atual = RAIZ / caminho
        if not atual.is_file():
            falhas.append(f"{caminho}: ausente")
            continue
        h = hashlib.sha256(atual.read_bytes()).hexdigest()
        if h != esperado:
            falhas.append(f"{caminho}: atual {h} != baseline {esperado}")
        else:
            print(f"OK  {esperado}  {caminho}")

    diff = _git("diff", "--name-status", BASELINE, "HEAD", "--", "docs").strip()
    if diff:
        falhas.append(f"alteracoes em docs/ desde a baseline:\n{diff}")
    else:
        print(f"OK  git diff {BASELINE[:12]}..HEAD -- docs: vazio")

    if falhas:
        for f in falhas:
            print(f"FALHA: {f}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
