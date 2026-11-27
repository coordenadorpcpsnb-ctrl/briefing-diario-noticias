"""Grupo J -- normalizacao de trechos da fonte (source_excerpt)."""

from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from briefing.configuracao import ErroConfiguracao, carregar_configuracao, carregar_regras
from briefing.janela import avaliar_artigo, calcular_janela
from briefing.models import (
    Article,
    ErroValidacao,
    EvidenceType,
    PublicationEvidence,
    gerar_article_id,
    normalizar_trecho,
    validar_limite_trecho,
    verificar_limite_trecho,
)

UTC = timezone.utc
CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture(scope="module")
def limite() -> int:
    return carregar_regras(CONFIG / "regras.yaml").source_excerpt_max_chars


def _campos(**over):
    base = dict(
        source_id="agencia_brasil",
        title="Dolar fecha em queda",
        original_url="https://agenciabrasil.ebc.com.br/economia/noticia/exemplo",
        language="pt",
        retrieved_at=datetime(2026, 11, 27, 9, tzinfo=UTC),
        publication_precision="datetime",
        publication_evidence=PublicationEvidence(EvidenceType.RSS, "Thu, 26 Nov 2026 19:08:00 -0300"),
        published_at=datetime(2026, 11, 26, 22, 8, tzinfo=UTC),
        author="Reporter",
        source_family="news_agency",
        themes=["economia"],
        geographic_scope="nacional",
    )
    base.update(over)
    return base


class TestGrupoJNormalizacao:
    def test_limite_configurado_e_600(self, limite):
        assert limite == 600

    def test_none(self, limite):
        assert normalizar_trecho(None, limite) == (None, False)

    @pytest.mark.parametrize("vazio", ["", "   ", "\n\t "])
    def test_vazio_tratado_como_ausente(self, limite, vazio):
        assert normalizar_trecho(vazio, limite) == (None, False)

    @pytest.mark.parametrize("n,truncado", [(599, False), (600, False), (601, True), (5000, True)])
    def test_tamanhos(self, limite, n, truncado):
        texto = "a" * n
        trecho, t = normalizar_trecho(texto, limite)
        assert t is truncado
        assert len(trecho) == min(n, limite)
        assert texto.startswith(trecho)

    def test_sem_reticencias(self, limite):
        trecho, t = normalizar_trecho("palavra " * 200, limite)
        assert t and len(trecho) <= limite and not trecho.endswith(("...", "…"))

    def test_espacos_normalizados(self):
        assert normalizar_trecho("  linha 1\n\n linha\t2  ", 50) == ("linha 1 linha 2", False)

    def test_unicode_por_caractere_nao_por_byte(self):
        texto = "ç" * 10 + "🌲" * 10  # caracteres multibyte em UTF-8
        trecho, t = normalizar_trecho(texto, 15)
        assert t and trecho == "ç" * 10 + "🌲" * 5
        trecho.encode("utf-8")  # nunca corrompe

    def test_nfc_aplicado(self):
        decomposto = "é" * 3  # "é" decomposto
        trecho, t = normalizar_trecho(decomposto, 10)
        assert trecho == "é" * 3 and not t

    def test_nao_separa_marca_combinante(self):
        # q + acento combinante nao tem forma composta em NFC
        texto = "ab" + "q́" + "cd"
        trecho, t = normalizar_trecho(texto, 3)  # corte cairia entre 'q' e a marca
        assert t and trecho == "ab"
        assert not unicodedata.combining(trecho[-1])

    def test_limite_diferente(self):
        assert normalizar_trecho("abcdef", 3) == ("abc", True)
        assert normalizar_trecho("abc", 3) == ("abc", False)

    @pytest.mark.parametrize("invalido", [0, -1, 1.5, 600.0, True, False, "600", None])
    def test_limite_invalido(self, invalido):
        with pytest.raises(ErroValidacao):
            normalizar_trecho("x", invalido)
        with pytest.raises(ErroValidacao):
            validar_limite_trecho(invalido)

    def test_texto_nao_textual(self):
        with pytest.raises(ErroValidacao):
            normalizar_trecho(123, 10)  # type: ignore[arg-type]


class TestGrupoJArticle:
    def test_descricao_extensa_nao_perde_artigo(self, limite):
        bruto = "Descricao longa vinda do feed. " * 100
        a = Article.criar_a_partir_da_fonte(trecho_bruto=bruto, limite_trecho=limite, **_campos())
        assert a.excerpt_truncated and len(a.source_excerpt) <= limite

    def test_preserva_metadados_e_id(self, limite):
        sem = Article.criar(**_campos())
        com = Article.criar_a_partir_da_fonte(trecho_bruto="x " * 2000, limite_trecho=limite, **_campos())
        for campo in ("article_id", "title", "original_url", "author", "published_at", "published_date",
                      "updated_at", "publication_evidence", "publication_precision", "retrieved_at"):
            assert getattr(com, campo) == getattr(sem, campo), campo
        assert com.article_id == gerar_article_id("agencia_brasil", _campos()["original_url"])

    def test_preserva_elegibilidade_temporal(self, limite, regras):
        j = calcular_janela(date(2026, 11, 27), regras)
        sem = Article.criar(**_campos())
        com = Article.criar_a_partir_da_fonte(trecho_bruto="y" * 9000, limite_trecho=limite, **_campos())
        assert avaliar_artigo(com, j) == avaliar_artigo(sem, j)

    def test_descricao_curta(self, limite):
        a = Article.criar_a_partir_da_fonte(trecho_bruto=" Curta. ", limite_trecho=limite, **_campos())
        assert a.source_excerpt == "Curta." and a.excerpt_truncated is False

    def test_descricao_ausente(self, limite):
        a = Article.criar_a_partir_da_fonte(trecho_bruto=None, limite_trecho=limite, **_campos())
        assert a.source_excerpt is None and a.excerpt_truncated is False

    def test_estado_contraditorio_rejeitado(self):
        with pytest.raises(ErroValidacao, match="excerpt_truncated"):
            Article.criar(excerpt_truncated=True, **_campos())
        with pytest.raises(ErroValidacao, match="excerpt_truncated"):
            Article.criar(source_excerpt="x", excerpt_truncated="sim", **_campos())

    def test_campos_de_trecho_nao_podem_ser_forcados(self, limite):
        with pytest.raises(ErroValidacao):
            Article.criar_a_partir_da_fonte(trecho_bruto="x", limite_trecho=limite,
                                            source_excerpt="y", **_campos())

    def test_serializacao(self, limite):
        a = Article.criar_a_partir_da_fonte(trecho_bruto="é" * 700, limite_trecho=limite, **_campos())
        d = json.loads(json.dumps(a.to_dict(), ensure_ascii=False))
        assert d["source_excerpt"] == "é" * 600 and d["excerpt_truncated"] is True
        assert "summary" not in d
        assert Article.from_dict(d) == a

    def test_campo_summary_antigo_rejeitado(self):
        d = Article.criar(**_campos()).to_dict()
        d["summary"] = "x"
        with pytest.raises(ErroValidacao, match="campos desconhecidos"):
            Article.from_dict(d)

    def test_verificar_limite(self):
        a = Article.criar(source_excerpt="abcdef", **_campos())
        verificar_limite_trecho(a, 6)
        with pytest.raises(ErroValidacao, match="limite"):
            verificar_limite_trecho(a, 5)


class TestGrupoJIntegracaoConfiguracao:
    def test_configuracao_normalizacao_article(self, tmp_path):
        """Fluxo completo: YAML -> limite validado -> normalizacao -> Article."""
        import shutil

        destino = tmp_path / "config"
        shutil.copytree(CONFIG, destino)
        arq = destino / "regras.yaml"
        arq.write_text(arq.read_text(encoding="utf-8").replace(
            "source_excerpt_max_chars: 600", "source_excerpt_max_chars: 40"), encoding="utf-8")
        cfg = carregar_configuracao(destino)
        limite = cfg.regras.source_excerpt_max_chars
        assert limite == 40
        a = Article.criar_a_partir_da_fonte(trecho_bruto="z" * 100, limite_trecho=limite, **_campos())
        assert a.source_excerpt == "z" * 40 and a.excerpt_truncated
        verificar_limite_trecho(a, limite)

    @pytest.mark.parametrize("valor", ["0", "-5", "1.5", "true", '"600"', "null"])
    def test_limite_invalido_na_configuracao(self, tmp_path, valor):
        import shutil

        destino = tmp_path / "config"
        shutil.copytree(CONFIG, destino)
        arq = destino / "regras.yaml"
        arq.write_text(arq.read_text(encoding="utf-8").replace(
            "source_excerpt_max_chars: 600", f"source_excerpt_max_chars: {valor}"), encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="source_excerpt_max_chars"):
            carregar_configuracao(destino)

    def test_limite_ausente_na_configuracao(self, tmp_path):
        import shutil

        destino = tmp_path / "config"
        shutil.copytree(CONFIG, destino)
        arq = destino / "regras.yaml"
        arq.write_text(arq.read_text(encoding="utf-8").replace(
            "  source_excerpt_max_chars: 600\n", "  outro_limite: 1\n"), encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="limites"):
            carregar_configuracao(destino)

    def test_fonte_unica_do_limite(self):
        """Nenhuma constante 600 independente no codigo-fonte."""
        src = Path(__file__).resolve().parents[1] / "src" / "briefing"
        for arq in src.glob("*.py"):
            assert "600" not in arq.read_text(encoding="utf-8"), arq.name
