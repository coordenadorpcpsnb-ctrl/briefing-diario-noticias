"""Grupo I -- semantica de coleta vazia (CollectionResult.coleta_vazia_confirmada)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import briefing.models as models
from briefing.models import (
    Article,
    CollectionResult,
    CollectionStatus as C,
    ErroValidacao,
    EvidenceType,
    PublicationEvidence,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 27, 11, 3, tzinfo=UTC)
T1 = datetime(2026, 11, 27, 11, 4, tzinfo=UTC)


def _artigo():
    return Article.criar(
        source_id="reuters", title="t", original_url="https://www.reuters.com/x", language="en",
        retrieved_at=T1, publication_precision="unknown",
        publication_evidence=PublicationEvidence(EvidenceType.UNKNOWN, None),
        source_family="news_agency", themes=["economia"], geographic_scope="internacional",
    )


CASOS = {
    C.SUCCESS_EMPTY: dict(articles_found=0, started_at=T0, finished_at=T1),
    C.SUCCESS_WITH_RESULTS: dict(articles=(_artigo(),), articles_found=1, started_at=T0, finished_at=T1),
    C.TECHNICAL_FAILURE: dict(articles_found=0, started_at=T0, finished_at=T1, error_code="timeout"),
    C.SOURCE_NOT_CONFIGURED: dict(articles_found=0),
    C.SOURCE_NOT_QUERIED: dict(articles_found=0),
    C.ACCESS_RESTRICTED: dict(articles_found=0, started_at=T0, finished_at=T1, error_code="http_403"),
    C.PARTIAL: dict(articles=(_artigo(),), articles_found=1, started_at=T0, finished_at=T1, error_code="pagina_2"),
}


class TestGrupoIColetaVazia:
    def test_todos_os_estados_cobertos(self):
        assert set(CASOS) == set(C)

    @pytest.mark.parametrize("status", list(C))
    def test_somente_success_empty_e_coleta_vazia(self, status):
        r = CollectionResult(source_id="reuters", execution_date=date(2026, 11, 27), status=status, **CASOS[status])
        assert r.coleta_vazia_confirmada is (status is C.SUCCESS_EMPTY)

    def test_propriedade_antiga_removida(self):
        r = CollectionResult(source_id="reuters", execution_date=date(2026, 11, 27),
                             status=C.SUCCESS_EMPTY, **CASOS[C.SUCCESS_EMPTY])
        assert not hasattr(r, "confirma_ausencia")
        assert not hasattr(CollectionResult, "confirma_ausencia")
        assert "confirma_ausencia" not in dir(models)

    def test_docstring_delimita_semantica(self):
        doc = CollectionResult.coleta_vazia_confirmada.__doc__
        assert "tema" in doc and "Fase 2" in doc

    @pytest.mark.parametrize(
        "status,extra",
        [
            (C.SUCCESS_EMPTY, dict(articles=(_artigo(),), articles_found=1)),
            (C.SUCCESS_WITH_RESULTS, dict(articles=(), articles_found=0)),
            (C.TECHNICAL_FAILURE, dict(articles=(_artigo(),), articles_found=1, error_code="x")),
            (C.SUCCESS_EMPTY, dict(articles=(), articles_found=3)),
        ],
    )
    def test_coerencia_status_contagem(self, status, extra):
        with pytest.raises(ErroValidacao):
            CollectionResult(source_id="reuters", execution_date=date(2026, 11, 27), status=status,
                             started_at=T0, finished_at=T1, **extra)
