"""Grupo L -- resolucao do diretorio de configuracao."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from briefing import configuracao as cfgmod
from briefing.configuracao import (
    ENV_CONFIG_DIR,
    ErroConfiguracao,
    carregar_configuracao,
    diretorio_config_padrao,
    resolver_diretorio_config,
)

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = RAIZ / "config"
SRC = RAIZ / "src" / "briefing"


@pytest.fixture
def externo(tmp_path: Path) -> Path:
    destino = tmp_path / "cfg_externa"
    shutil.copytree(CONFIG, destino)
    return destino


def _env_limpo(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != ENV_CONFIG_DIR}
    env.update(extra)
    return env


class TestGrupoLDiretorioConfig:
    def test_argumento_explicito_valido(self, externo):
        caminho, origem = resolver_diretorio_config(externo, ambiente={})
        assert caminho == externo and origem == "argumento explicito"

    def test_argumento_explicito_invalido_sem_fallback(self, tmp_path):
        with pytest.raises(ErroConfiguracao) as exc:
            resolver_diretorio_config(tmp_path / "nao_existe", ambiente={ENV_CONFIG_DIR: str(CONFIG)})
        assert exc.value.campo == "argumento explicito" and "inexistente" in exc.value.motivo

    def test_variavel_de_ambiente_valida(self, externo):
        caminho, origem = resolver_diretorio_config(ambiente={ENV_CONFIG_DIR: str(externo)})
        assert caminho == externo and ENV_CONFIG_DIR in origem

    @pytest.mark.parametrize("valor", ["", "   ", "/caminho/inexistente/briefing"])
    def test_variavel_de_ambiente_invalida_sem_fallback(self, valor):
        with pytest.raises(ErroConfiguracao) as exc:
            resolver_diretorio_config(ambiente={ENV_CONFIG_DIR: valor})
        assert ENV_CONFIG_DIR in exc.value.campo

    def test_precedencia_do_argumento(self, externo, tmp_path):
        outro = tmp_path / "outro"
        shutil.copytree(CONFIG, outro)
        caminho, origem = resolver_diretorio_config(externo, ambiente={ENV_CONFIG_DIR: str(outro)})
        assert caminho == externo and origem == "argumento explicito"

    def test_precedencia_da_variavel_sobre_padrao(self, externo):
        caminho, _ = resolver_diretorio_config(ambiente={ENV_CONFIG_DIR: str(externo)})
        assert caminho == externo != CONFIG

    def test_diretorio_padrao_do_checkout(self):
        assert diretorio_config_padrao() == CONFIG
        caminho, origem = resolver_diretorio_config(ambiente={})
        assert caminho == CONFIG and origem == "checkout do repositorio"

    @pytest.mark.parametrize("nome", ["temas.yaml", "fontes.yaml", "regras.yaml"])
    def test_arquivo_obrigatorio_ausente(self, externo, nome):
        (externo / nome).unlink()
        with pytest.raises(ErroConfiguracao, match=f"ausente: {nome}"):
            resolver_diretorio_config(externo, ambiente={})

    def test_arquivo_obrigatorio_e_diretorio(self, externo):
        (externo / "temas.yaml").unlink()
        (externo / "temas.yaml").mkdir()
        with pytest.raises(ErroConfiguracao, match="nao e um arquivo regular"):
            resolver_diretorio_config(externo, ambiente={})

    def test_caminho_aponta_para_arquivo(self, externo):
        with pytest.raises(ErroConfiguracao, match="nao e um diretorio"):
            resolver_diretorio_config(externo / "temas.yaml", ambiente={})

    def test_erro_explicito_sem_configuracao(self, monkeypatch):
        monkeypatch.setattr(cfgmod, "diretorio_config_padrao", lambda: None)
        with pytest.raises(ErroConfiguracao, match="nenhuma configuracao disponivel"):
            resolver_diretorio_config(ambiente={})

    def test_carregar_configuracao_registra_origem(self, externo):
        cfg = carregar_configuracao(ambiente={ENV_CONFIG_DIR: str(externo)})
        assert cfg.diretorio == externo and ENV_CONFIG_DIR in cfg.origem and len(cfg.fontes) == 25

    def test_execucao_a_partir_de_outro_diretorio(self, tmp_path):
        r = subprocess.run(
            [sys.executable, "-c",
             "from briefing.configuracao import carregar_configuracao as c; x=c(); print(x.origem, '|', x.diretorio)"],
            cwd=tmp_path, env=_env_limpo(), capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == f"checkout do repositorio | {CONFIG}"

    def test_instalacao_convencional_simulada(self, tmp_path, externo):
        """Pacote copiado para um diretorio fora do checkout (layout de site-packages).

        Sem rede: simula a instalacao convencional. A instalacao real via
        ``pip install .`` foi verificada manualmente (ver relatorio da PR).
        """
        site = tmp_path / "site-packages"
        shutil.copytree(SRC, site / "briefing", ignore=shutil.ignore_patterns("__pycache__"))
        trabalho = tmp_path / "trabalho"
        trabalho.mkdir()
        codigo = textwrap.dedent(
            """
            import briefing, sys
            from briefing.configuracao import carregar_configuracao, ErroConfiguracao
            print(briefing.__file__)
            try:
                carregar_configuracao()
                print("SEM_ERRO")
            except ErroConfiguracao as e:
                print("ERRO:", e.motivo.split(":")[0])
            """
        )
        # 1) sem configuracao externa: erro explicito (nao acha o checkout)
        r = subprocess.run([sys.executable, "-c", codigo], cwd=trabalho,
                           env=_env_limpo(PYTHONPATH=str(site)), capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        linhas = r.stdout.strip().splitlines()
        assert linhas[0].startswith(str(site)), "pacote importado nao e a copia externa"
        assert linhas[1] == "ERRO: nenhuma configuracao disponivel"
        # 2) com BRIEFING_CONFIG_DIR: funciona
        r = subprocess.run([sys.executable, "-c", codigo], cwd=trabalho,
                           env=_env_limpo(PYTHONPATH=str(site), BRIEFING_CONFIG_DIR=str(externo)),
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip().splitlines()[1] == "SEM_ERRO"

    def test_nao_procura_em_ancestrais_nem_cwd(self, tmp_path, monkeypatch):
        # um config/ no diretorio de trabalho nao deve ser usado implicitamente
        shutil.copytree(CONFIG, tmp_path / "config")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cfgmod, "diretorio_config_padrao", lambda: None)
        with pytest.raises(ErroConfiguracao, match="nenhuma configuracao disponivel"):
            resolver_diretorio_config(ambiente={})
