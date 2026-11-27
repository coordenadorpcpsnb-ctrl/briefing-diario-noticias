"""Consistencia dos arquivos de lock (versoes exatas + hashes).

Mecanismo de verificacao que evita divergencia entre pyproject.toml,
requirements/*.lock e o ambiente efetivamente instalado.
"""

from __future__ import annotations

import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

RAIZ = Path(__file__).resolve().parents[1]
REQ = RAIZ / "requirements"
PYPROJECT = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
_LINHA = re.compile(r"^(?P<nome>[A-Za-z0-9._-]+)==(?P<versao>[^\s;\\]+)\s*(?:;\s*(?P<marcador>[^\\]+?))?\s*\\?$")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})\b")


def ler_lock(nome: str) -> dict[str, dict]:
    pacotes: dict[str, dict] = {}
    atual = None
    for linha in (REQ / nome).read_text(encoding="utf-8").splitlines():
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        if not linha.startswith(" "):
            m = _LINHA.match(linha.strip())
            assert m, f"{nome}: linha fora do formato 'nome==versao': {linha!r}"
            atual = canonicalize_name(m["nome"])
            assert atual not in pacotes, f"{nome}: {atual} duplicado"
            pacotes[atual] = {"versao": m["versao"], "marcador": m["marcador"], "hashes": []}
        else:
            hashes = _HASH.findall(linha)
            if hashes:
                pacotes[atual]["hashes"].extend(hashes)
            else:
                assert linha.strip().startswith("# via"), f"{nome}: linha inesperada {linha!r}"
    return pacotes


LOCKS = ("runtime.lock", "dev.lock", "build.lock")


@pytest.mark.parametrize("nome", LOCKS)
def test_lock_fixado_com_hashes(nome):
    pacotes = ler_lock(nome)
    assert pacotes
    for pkg, info in pacotes.items():
        assert info["hashes"], f"{nome}: {pkg} sem hash"
        assert len(set(info["hashes"])) == len(info["hashes"]), f"{nome}: {pkg} hash repetido"


@pytest.mark.parametrize("nome", LOCKS)
def test_lock_gerado_pelo_script_documentado(nome):
    texto = (REQ / nome).read_text(encoding="utf-8")
    assert "#    bash ferramentas/gerar_locks.sh" in texto.splitlines()[1]


def _satisfaz(lock: dict, requisitos: list[str]) -> None:
    for bruto in requisitos:
        req = Requirement(bruto)
        nome = canonicalize_name(req.name)
        assert nome in lock, f"{req.name} ausente do lock"
        assert req.specifier.contains(lock[nome]["versao"], prereleases=False), (
            f"{req.name}=={lock[nome]['versao']} nao satisfaz {req.specifier}"
        )


def test_runtime_lock_cobre_dependencias_do_pyproject():
    _satisfaz(ler_lock("runtime.lock"), PYPROJECT["project"]["dependencies"])


def test_dev_lock_cobre_runtime_e_dev():
    dev = ler_lock("dev.lock")
    _satisfaz(dev, PYPROJECT["project"]["dependencies"] + PYPROJECT["project"]["optional-dependencies"]["dev"])


def test_runtime_e_dev_identicos_nos_pacotes_comuns():
    runtime, dev = ler_lock("runtime.lock"), ler_lock("dev.lock")
    for pkg, info in runtime.items():
        assert dev[pkg]["versao"] == info["versao"], pkg
        assert sorted(dev[pkg]["hashes"]) == sorted(info["hashes"]), pkg


def test_build_lock_cobre_build_system():
    build = ler_lock("build.lock")
    _satisfaz(build, PYPROJECT["build-system"]["requires"])
    entrada = [l.strip() for l in (REQ / "build.in").read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")]
    assert sorted(canonicalize_name(Requirement(r).name) for r in entrada) == sorted(
        canonicalize_name(Requirement(r).name) for r in PYPROJECT["build-system"]["requires"]
    ), "requirements/build.in difere de [build-system].requires"


def test_build_e_dev_concordam_em_pacotes_comuns():
    build, dev = ler_lock("build.lock"), ler_lock("dev.lock")
    for pkg in set(build) & set(dev):
        assert build[pkg]["versao"] == dev[pkg]["versao"], pkg


def test_lock_universal_contem_dependencia_exclusiva_do_windows():
    # pytest exige colorama apenas em win32: o lock deve ser valido para ambas as plataformas.
    dev = ler_lock("dev.lock")
    assert dev["colorama"]["marcador"] == "sys_platform == 'win32'"


def test_constraints_simples_removido():
    assert not (RAIZ / "constraints-dev.txt").exists(), "substituido pelos locks com hashes"


def test_ambiente_instalado_corresponde_ao_lock():
    """Executado no ambiente de testes: versoes instaladas == lock (pacotes aplicaveis)."""
    dev = ler_lock("dev.lock")
    divergencias = []
    for pkg, info in dev.items():
        if info["marcador"] and not Marker(info["marcador"]).evaluate():
            continue
        try:
            instalada = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            divergencias.append(f"{pkg}: nao instalado (lock {info['versao']})")
            continue
        if canonicalize_name(instalada) != canonicalize_name(info["versao"]):
            divergencias.append(f"{pkg}: instalado {instalada}, lock {info['versao']}")
    assert not divergencias, (
        "ambiente difere de requirements/dev.lock (instale com --require-hashes): " + "; ".join(divergencias)
    )


def test_python_de_referencia():
    assert sys.version_info[:2] == (3, 12), "locks gerados e validados para Python 3.12"
