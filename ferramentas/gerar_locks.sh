#!/usr/bin/env bash
# Regenera os arquivos de lock (versoes exatas + hashes SHA-256) a partir de
# pyproject.toml e requirements/build.in.
#
# Ferramenta auxiliar de GERACAO: uv (versao fixada abaixo). A instalacao
# operacional usa somente pip (--require-hashes); uv nao e necessario no CI.
#
# Por que uv e nao pip-tools: pip-compile resolve apenas para a plataforma em
# que roda, e o pytest exige colorama somente no Windows. "uv pip compile
# --universal" gera um unico lock com marcadores de ambiente valido para Linux
# e Windows, evitando listas duplicadas por plataforma.
#
# Uso (a partir da raiz do repositorio):
#   pip install "uv==0.11.32"
#   bash ferramentas/gerar_locks.sh            # mantem as versoes atuais
#   bash ferramentas/gerar_locks.sh --upgrade  # busca versoes novas (revisar o diff!)
set -euo pipefail

UV_VERSAO_ESPERADA="0.11.32"
versao="$(uv --version | awk '{print $2}')"
if [[ "$versao" != "$UV_VERSAO_ESPERADA" ]]; then
  echo "uv $UV_VERSAO_ESPERADA esperado, encontrado $versao" >&2
  exit 1
fi

export UV_NO_CONFIG=1
comum=(--universal --python-version 3.12 --generate-hashes --quiet "$@")

uv pip compile pyproject.toml "${comum[@]}" \
  --custom-compile-command "bash ferramentas/gerar_locks.sh" -o requirements/runtime.lock
uv pip compile pyproject.toml --extra dev "${comum[@]}" \
  --custom-compile-command "bash ferramentas/gerar_locks.sh" -o requirements/dev.lock
uv pip compile requirements/build.in "${comum[@]}" \
  --custom-compile-command "bash ferramentas/gerar_locks.sh" -o requirements/build.lock

echo "Locks regenerados. Revise com: git diff requirements/"
