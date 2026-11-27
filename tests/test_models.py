"""Grupo E -- contratos de dados."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from briefing.models import (
    SUMMARY_MAX_CHARS,
    Article,
    CollectionResult,
    CollectionStatus,
    ErroValidacao,
    EvidenceType,
    PublicationEvidence,
    PublicationWindow,
    SourceConfig,
    gerar_article_id,
)

UTC = timezone.utc
SP = ZoneInfo("America/Sao_Paulo")
RETRIEVED = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)


def _artigo(**over):
    base = dict(
        source_id="reuters",
        title="Titulo",
        original_url="https://www.reuters.com/exemplo-2026-11-26/",
        language="en",
        retrieved_at=RETRIEVED,
        publication_precision="datetime",
        publication_evidence=PublicationEvidence(EvidenceType.HTML_META, "2026-11-26T13:00:00Z"),
        published_at=datetime(2026, 11, 26, 13, tzinfo=UTC),
        source_family="news_agency",
        themes=["economia"],
        geographic_scope="internacional",
    )
    base.update(over)
    return Article.criar(**base)


def _resultado(**over):
    base = dict(
        source_id="reuters",
        execution_date=date(2026, 11, 27),
        status="success_empty",
        articles_found=0,
        started_at=datetime(2026, 11, 27, 11, 3, tzinfo=UTC),
        finished_at=datetime(2026, 11, 27, 11, 4, tzinfo=UTC),
    )
    base.update(over)
    return CollectionResult(**base)


class TestGrupoEArticle:
    def test_campos_obrigatorios(self):
        with pytest.raises(ErroValidacao, match="campos obrigatorios ausentes"):
            Article.from_dict({"article_id": "x"})

    def test_id_estavel_e_reproduzivel(self):
        a, b = _artigo(), _artigo(title="Outro titulo")
        assert a.article_id == b.article_id == gerar_article_id("reuters", a.original_url)
        assert a.article_id.startswith("art_") and len(a.article_id) == 36
        assert _artigo(source_id="associated_press", source_family="news_agency").article_id != a.article_id

    def test_id_divergente_rejeitado(self):
        a = _artigo()
        d = a.to_dict()
        d["article_id"] = "art_" + "0" * 32
        with pytest.raises(ErroValidacao, match="article_id"):
            Article.from_dict(d)

    def test_serializacao_ida_e_volta(self):
        a = _artigo(updated_at=datetime(2026, 11, 26, 14, tzinfo=UTC), author="Fulano")
        d = a.to_dict()
        json.dumps(d)  # JSON puro
        assert Article.from_dict(json.loads(json.dumps(d))) == a

    def test_preserva_timestamp_original(self):
        original = datetime(2026, 11, 26, 22, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        a = _artigo(published_at=original)
        assert a.published_at is original and a.published_at.tzinfo == ZoneInfo("Asia/Tokyo")
        assert Article.from_dict(a.to_dict()).published_at.utcoffset() == timedelta(hours=9)

    def test_autor_ausente(self):
        assert _artigo().author is None

    def test_datetime_sem_fuso_rejeitado(self):
        with pytest.raises(ErroValidacao, match="sem fuso"):
            _artigo(published_at=datetime(2026, 11, 26, 13))

    def test_somente_data_sem_meia_noite_ficticia(self):
        a = _artigo(
            publication_precision="date",
            published_at=None,
            published_date=date(2026, 11, 26),
            publication_evidence=PublicationEvidence(EvidenceType.VISIBLE_DATE, "26 nov 2026"),
        )
        assert a.published_at is None and a.publication_timezone is None
        with pytest.raises(ErroValidacao, match="meia-noite"):
            _artigo(
                publication_precision="date",
                published_date=date(2026, 11, 26),
                published_at=datetime(2026, 11, 26, tzinfo=SP),
            )

    def test_data_incoerente_com_instante(self):
        with pytest.raises(ErroValidacao, match="incoerente"):
            _artigo(published_date=date(2026, 11, 27), publication_timezone="America/Sao_Paulo")
        # coerente: 13:00Z = 10:00 BRT do dia 26
        assert _artigo(published_date=date(2026, 11, 26), publication_timezone="America/Sao_Paulo")

    def test_atualizacao_anterior_a_publicacao(self):
        with pytest.raises(ErroValidacao, match="contraditorios"):
            _artigo(updated_at=datetime(2026, 11, 26, 12, tzinfo=UTC))

    def test_retrieved_nao_substitui_publicacao(self):
        a = _artigo(
            publication_precision="unknown",
            published_at=None,
            publication_evidence=PublicationEvidence(EvidenceType.UNKNOWN, None),
        )
        assert a.published_at is None and a.published_date is None

    def test_data_conhecida_exige_evidencia_identificada(self):
        with pytest.raises(ErroValidacao, match="evidencia"):
            _artigo(publication_evidence=PublicationEvidence(EvidenceType.UNKNOWN, None))

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "notaurl", "https://user:pw@x.com/a"])
    def test_url_invalida(self, url):
        with pytest.raises(ErroValidacao):
            _artigo(original_url=url)

    def test_enum_invalido(self):
        with pytest.raises(ErroValidacao, match="valor desconhecido"):
            _artigo(geographic_scope="regional")

    def test_campo_desconhecido_rejeitado(self):
        d = _artigo().to_dict()
        d["texto_integral"] = "..."
        with pytest.raises(ErroValidacao, match="campos desconhecidos"):
            Article.from_dict(d)

    def test_resumo_limitado(self):
        assert _artigo(summary="x" * SUMMARY_MAX_CHARS)
        with pytest.raises(ErroValidacao, match="texto integral"):
            _artigo(summary="x" * (SUMMARY_MAX_CHARS + 1))


class TestGrupoEEvidencia:
    def test_evidencia_identificada_exige_valor_bruto(self):
        with pytest.raises(ErroValidacao, match="raw_value"):
            PublicationEvidence(EvidenceType.RSS, None)

    def test_evidencia_ida_e_volta(self):
        e = PublicationEvidence(
            EvidenceType.JSON_LD_DATE_PUBLISHED,
            "2026-11-26T10:00:00-03:00",
            source_url="https://exemplo.com.br/materia",
            timezone="America/Sao_Paulo",
            retrieved_at=RETRIEVED,
        )
        assert PublicationEvidence.from_dict(json.loads(json.dumps(e.to_dict()))) == e

    def test_tipos_previstos(self):
        assert {t.value for t in EvidenceType} == {
            "rss", "json_ld_date_published", "html_meta", "visible_date", "api", "other", "unknown"
        }

    def test_fuso_invalido(self):
        with pytest.raises(ErroValidacao, match="fuso IANA"):
            PublicationEvidence(EvidenceType.OTHER, "x", timezone="GMT-3")


class TestGrupoEJanelaContrato:
    def test_janela_ida_e_volta(self, janela):
        j = janela(2026, 11, 30)
        assert PublicationWindow.from_dict(json.loads(json.dumps(j.to_dict()))) == j

    def test_inicio_apos_fim(self):
        with pytest.raises(ErroValidacao):
            PublicationWindow(
                date(2026, 11, 27), "America/Sao_Paulo",
                datetime(2026, 11, 27, 9, tzinfo=SP), datetime(2026, 11, 27, 8, tzinfo=SP),
            )


class TestGrupoECollectionResult:
    def test_vazio_confirma_ausencia(self):
        assert _resultado().confirma_ausencia

    def test_falha_tecnica_nao_confirma_ausencia(self):
        r = _resultado(status="technical_failure", error_code="timeout", error_message="tempo limite")
        assert not r.confirma_ausencia and r.articles_found == 0

    def test_parcial_distinto_de_vazio(self):
        r = _resultado(status="partial", error_code="pagina_2_falhou", articles=(_artigo(),), articles_found=1)
        assert not r.confirma_ausencia and r.status is CollectionStatus.PARTIAL

    def test_parcial_exige_erro(self):
        with pytest.raises(ErroValidacao, match="error_code"):
            _resultado(status="partial")

    def test_falha_exige_codigo(self):
        with pytest.raises(ErroValidacao, match="error_code"):
            _resultado(status="technical_failure")

    def test_sucesso_com_resultados_exige_artigo(self):
        with pytest.raises(ErroValidacao):
            _resultado(status="success_with_results")
        assert _resultado(status="success_with_results", articles=(_artigo(),), articles_found=1)

    def test_contagem_incoerente(self):
        with pytest.raises(ErroValidacao, match="articles_found"):
            _resultado(status="success_with_results", articles=(_artigo(),), articles_found=2)

    def test_vazio_nao_admite_erro(self):
        with pytest.raises(ErroValidacao, match="nao admite erro"):
            _resultado(error_code="x")

    def test_horarios_incoerentes(self):
        with pytest.raises(ErroValidacao, match="anterior"):
            _resultado(finished_at=datetime(2026, 11, 27, 11, 2, tzinfo=UTC))

    def test_nao_consultada_sem_horarios(self):
        r = _resultado(status="source_not_queried", started_at=None, finished_at=None)
        assert not r.confirma_ausencia
        with pytest.raises(ErroValidacao):
            _resultado(status="source_not_configured")  # tem horarios

    def test_acesso_restrito(self):
        r = _resultado(status="access_restricted", error_code="http_403")
        assert not r.confirma_ausencia

    def test_artigo_de_outra_fonte(self):
        with pytest.raises(ErroValidacao, match="outras fontes"):
            _resultado(
                source_id="associated_press",
                status="success_with_results",
                articles=(_artigo(),),
                articles_found=1,
            )

    def test_ida_e_volta(self):
        r = _resultado(status="success_with_results", articles=(_artigo(),), articles_found=1)
        assert CollectionResult.from_dict(json.loads(json.dumps(r.to_dict()))) == r

    def test_status_invalido(self):
        with pytest.raises(ErroValidacao, match="valor desconhecido"):
            _resultado(status="ok")


class TestGrupoESourceConfig:
    def _fonte(self, **over):
        base = dict(
            source_id="exemplo", name="Exemplo", homepage_url="https://exemplo.com.br/",
            url_provenance="public_knowledge_untested", country="BR", language="pt",
            family="general_press", themes=["politica"], enabled=False, mandatory=False,
            mandatory_themes=[], access_status="unverified", collection_methods=[],
            terms_status="unverified", notes="teste",
        )
        base.update(over)
        return SourceConfig(**base)

    def test_habilitada_exige_acesso_verificado(self):
        with pytest.raises(ErroValidacao, match="metodo de coleta"):
            self._fonte(enabled=True)
        with pytest.raises(ErroValidacao, match="acesso verificado"):
            self._fonte(enabled=True, collection_methods=["rss"], rss_url="https://exemplo.com.br/feed")
        assert self._fonte(
            enabled=True, collection_methods=["rss"], rss_url="https://exemplo.com.br/feed",
            access_status="accessible",
        ).enabled

    def test_metodo_sem_url(self):
        with pytest.raises(ErroValidacao, match="rss_url"):
            self._fonte(collection_methods=["rss"])

    def test_obrigatoria_sem_temas(self):
        with pytest.raises(ErroValidacao, match="mandatory_themes"):
            self._fonte(mandatory=True)
        with pytest.raises(ErroValidacao, match="mandatory=true"):
            self._fonte(mandatory_themes=["politica"])

    def test_pendente_sem_url(self):
        assert self._fonte(homepage_url=None, url_provenance=None, registration_status="pending")
        with pytest.raises(ErroValidacao, match="homepage_url"):
            self._fonte(homepage_url=None, url_provenance=None)

    def test_http_inseguro_rejeitado_em_config(self):
        with pytest.raises(ErroValidacao, match="esquema"):
            self._fonte(homepage_url="http://exemplo.com.br/")
