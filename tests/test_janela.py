"""Grupos A (janelas), B (limites), C (sem horario) e D (continuidade)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from briefing.janela import (
    ErroDataExecucao,
    InicioJanela,
    RegrasJanela,
    avaliar_artigo,
    avaliar_data_civil,
    avaliar_instante,
    avaliar_metadados,
    calcular_janela,
)
from briefing.models import (
    Article,
    ErroValidacao,
    EvidenceType,
    PublicationEvidence,
    RawPublicationMetadata,
    ReasonCode,
    TemporalStatus as S,
)

SP = ZoneInfo("America/Sao_Paulo")
UTC = timezone.utc


def sp(a, m, d, h=0, mi=0, s=0, us=0):
    return datetime(a, m, d, h, mi, s, us, tzinfo=SP)


# ============================================================ Grupo A
class TestGrupoAJanelas:
    def test_exemplo_sexta_27_11_2026(self, janela):
        j = janela(2026, 11, 27)
        assert j.start == sp(2026, 11, 26) and j.end_exclusive == sp(2026, 11, 27, 8)

    def test_exemplo_segunda_30_11_2026(self, janela):
        j = janela(2026, 11, 30)
        assert j.start == sp(2026, 11, 27, 8) and j.end_exclusive == sp(2026, 11, 30, 8)

    def test_exemplo_terca_01_12_2026(self, janela):
        j = janela(2026, 12, 1)
        assert j.start == sp(2026, 11, 30) and j.end_exclusive == sp(2026, 12, 1, 8)

    @pytest.mark.parametrize("dia,inicio", [(1, 30), (2, 1), (3, 2), (4, 3)])
    def test_terca_a_sexta(self, janela, dia, inicio):
        # 01/12/2026 terca ... 04/12/2026 sexta
        mes_ini = 11 if inicio == 30 else 12
        j = janela(2026, 12, dia)
        assert j.start == sp(2026, mes_ini, inicio)
        assert j.end_exclusive == sp(2026, 12, dia, 8)

    def test_segunda_inicia_sexta_0800(self, janela):
        j = janela(2026, 12, 7)
        assert j.start.weekday() == 4
        assert j.start == sp(2026, 12, 4, 8)

    def test_duracao_segunda_72h(self, janela):
        assert janela(2026, 11, 30).duracao == timedelta(hours=72)

    @pytest.mark.parametrize("dia", [1, 2, 3, 4])
    def test_duracao_terca_a_sexta_32h(self, janela, dia):
        assert janela(2026, 12, dia).duracao == timedelta(hours=32)

    @pytest.mark.parametrize("d", [date(2026, 11, 28), date(2026, 11, 29)])
    def test_fim_de_semana_rejeitado(self, regras, d):
        with pytest.raises(ErroDataExecucao, match="nao e dia de execucao"):
            calcular_janela(d, regras)

    def test_datetime_no_lugar_de_date_rejeitado(self, regras):
        with pytest.raises(TypeError):
            calcular_janela(datetime(2026, 11, 27, 8), regras)  # type: ignore[arg-type]

    def test_virada_de_mes(self, janela):
        j = janela(2026, 12, 1)
        assert j.start == sp(2026, 11, 30)

    def test_virada_de_ano_segunda(self, janela):
        # 04/01/2027 e segunda; inicio sexta 01/01/2027 08:00
        j = janela(2027, 1, 4)
        assert j.start == sp(2027, 1, 1, 8) and j.duracao == timedelta(hours=72)

    def test_virada_de_ano_sexta(self, janela):
        # 01/01/2027 sexta (feriado): inicio 31/12/2026 00:00
        j = janela(2027, 1, 1)
        assert j.start == sp(2026, 12, 31) and j.end_exclusive == sp(2027, 1, 1, 8)

    def test_ano_bissexto(self, janela):
        # 01/03/2028 quarta; dia anterior 29/02/2028
        j = janela(2028, 3, 1)
        assert j.start == sp(2028, 2, 29)

    def test_ano_bissexto_segunda(self, janela):
        # 03/03/2024 domingo -> usar segunda 04/03/2024; sexta 01/03/2024
        j = janela(2024, 3, 4)
        assert j.start == sp(2024, 3, 1, 8)

    def test_feriado_em_dia_util_segue_regra_normal(self, janela):
        # 25/12/2026 sexta (Natal) e 02/11/2026 segunda (Finados)
        assert janela(2026, 12, 25).start == sp(2026, 12, 24)
        assert janela(2026, 11, 2).start == sp(2026, 10, 30, 8)

    def test_usa_zoneinfo_e_nao_offset_fixo(self, janela):
        j = janela(2026, 11, 27)
        assert j.timezone == "America/Sao_Paulo"
        assert isinstance(j.start.tzinfo, ZoneInfo)

    def test_duracao_real_respeita_horario_de_verao_historico(self, janela):
        # Horario de verao de 2018 iniciou em 04/11/2018: a janela de
        # segunda 05/11/2018 tem 71h reais (72h nominais de parede).
        j = janela(2018, 11, 5)
        assert j.duracao == timedelta(hours=71)

    def test_descricao_apresenta_0759(self, janela):
        assert janela(2026, 11, 27).descrever() == (
            "26/11/2026 00h00 a 27/11/2026 07h59 (America/Sao_Paulo)"
        )

    def test_regras_rejeitam_inicio_apos_fim(self):
        with pytest.raises(ErroValidacao):
            RegrasJanela("America/Sao_Paulo", time(8), {1: InicioJanela(0, time(9))})

    def test_regras_rejeitam_fuso_invalido(self):
        with pytest.raises(ErroValidacao, match="fuso IANA invalido"):
            RegrasJanela("America/Brasilia", time(8), {1: InicioJanela(1, time(0))})


# ============================================================ Grupo B
class TestGrupoBLimites:
    @pytest.fixture
    def j(self, janela):
        return janela(2026, 11, 27)  # [26/11 00:00, 27/11 08:00)

    def test_exatamente_no_inicio(self, j):
        assert avaliar_instante(sp(2026, 11, 26), j).status is S.ELIGIBLE_CONFIRMED

    def test_um_microssegundo_antes_do_inicio(self, j):
        d = avaliar_instante(sp(2026, 11, 26) - timedelta(microseconds=1), j)
        assert d.status is S.REJECTED_OUTSIDE_WINDOW and d.reason_code is ReasonCode.BEFORE_WINDOW

    def test_0759_59(self, j):
        assert avaliar_instante(sp(2026, 11, 27, 7, 59, 59), j).eligible

    def test_0759_59_999999(self, j):
        assert avaliar_instante(sp(2026, 11, 27, 7, 59, 59, 999999), j).eligible

    def test_exatamente_0800(self, j):
        d = avaliar_instante(sp(2026, 11, 27, 8), j)
        assert d.status is S.REJECTED_OUTSIDE_WINDOW
        assert d.reason_code is ReasonCode.AT_OR_AFTER_WINDOW_END

    def test_um_microssegundo_apos_fim(self, j):
        assert not avaliar_instante(sp(2026, 11, 27, 8, 0, 0, 1), j).eligible

    def test_conversao_utc(self, j):
        # 10:59:59Z = 07:59:59 BRT -> dentro; 11:00:00Z = 08:00 BRT -> fora
        assert avaliar_instante(datetime(2026, 11, 27, 10, 59, 59, tzinfo=UTC), j).eligible
        assert not avaliar_instante(datetime(2026, 11, 27, 11, tzinfo=UTC), j).eligible

    def test_conversao_outros_fusos(self, j):
        ny = datetime(2026, 11, 27, 5, 59, tzinfo=ZoneInfo("America/New_York"))  # 07:59 BRT
        tokyo = datetime(2026, 11, 26, 11, 59, tzinfo=ZoneInfo("Asia/Tokyo"))  # 23:59 BRT do dia 25
        assert avaliar_instante(ny, j).eligible
        assert avaliar_instante(tokyo, j).status is S.REJECTED_OUTSIDE_WINDOW

    def test_data_muda_apos_conversao(self, j):
        # 26/11 01:30 em Londres = 25/11 22:30 BRT: fora, embora a data local seja 26
        londres = datetime(2026, 11, 26, 1, 30, tzinfo=ZoneInfo("Europe/London"))
        assert not avaliar_instante(londres, j).eligible
        # 27/11 02:00 em Tokyo = 26/11 14:00 BRT: dentro
        assert avaliar_instante(datetime(2026, 11, 27, 2, tzinfo=ZoneInfo("Asia/Tokyo")), j).eligible

    def test_timestamp_sem_fuso(self, j):
        d = avaliar_instante(datetime(2026, 11, 26, 12), j)
        assert d.status is S.PENDING_TIMEZONE_CONFIRMATION and not d.eligible

    def test_timestamp_invalido(self, j):
        d = avaliar_metadados(RawPublicationMetadata(published_raw="2026-11-26T25:00:00-03:00"), j)
        assert d.status is S.REJECTED_INVALID_METADATA
        d = avaliar_metadados(RawPublicationMetadata(published_raw="ontem a noite"), j)
        assert d.status is S.REJECTED_INVALID_METADATA

    def test_fuso_iana_invalido(self, j):
        d = avaliar_metadados(
            RawPublicationMetadata(published_raw="2026-11-26T10:00:00", timezone_hint="Brasil/Brasilia"), j
        )
        assert d.status is S.REJECTED_INVALID_METADATA and d.reason_code is ReasonCode.INVALID_TIMEZONE

    def test_metadado_com_offset_dentro(self, j):
        d = avaliar_metadados(RawPublicationMetadata(published_raw="2026-11-27T07:59:59.999999-03:00"), j)
        assert d.status is S.ELIGIBLE_CONFIRMED

    def test_metadado_zulu(self, j):
        d = avaliar_metadados(RawPublicationMetadata(published_raw="2026-11-27T11:00:00Z"), j)
        assert d.status is S.REJECTED_OUTSIDE_WINDOW

    def test_metadado_sem_fuso_com_fuso_comprovado(self, j):
        d = avaliar_metadados(
            RawPublicationMetadata(published_raw="2026-11-26T09:00:00", timezone_hint="America/Sao_Paulo"), j
        )
        assert d.status is S.ELIGIBLE_CONFIRMED

    def test_metadado_sem_fuso_sem_evidencia(self, j):
        d = avaliar_metadados(RawPublicationMetadata(published_raw="2026-11-26T09:00:00"), j)
        assert d.status is S.PENDING_TIMEZONE_CONFIRMATION


# ============================================================ Grupo C
class TestGrupoCSemHorario:
    @pytest.mark.parametrize(
        "pub,edicao,esperado",
        [
            (date(2026, 11, 26), date(2026, 11, 27), S.ELIGIBLE_DATE_ONLY),
            (date(2026, 11, 27), date(2026, 11, 27), S.PENDING_TIME_CONFIRMATION),
            (date(2026, 11, 27), date(2026, 11, 30), S.PENDING_TIME_CONFIRMATION),
            (date(2026, 11, 28), date(2026, 11, 30), S.ELIGIBLE_DATE_ONLY),
            (date(2026, 11, 29), date(2026, 11, 30), S.ELIGIBLE_DATE_ONLY),
            (date(2026, 11, 30), date(2026, 11, 30), S.PENDING_TIME_CONFIRMATION),
            (date(2026, 11, 30), date(2026, 12, 1), S.ELIGIBLE_DATE_ONLY),
            (date(2026, 11, 26), date(2026, 11, 30), S.REJECTED_OUTSIDE_WINDOW),
        ],
    )
    def test_exemplos_obrigatorios_secao_7(self, regras, pub, edicao, esperado):
        d = avaliar_data_civil(pub, "America/Sao_Paulo", calcular_janela(edicao, regras))
        assert d.status is esperado
        assert d.eligible is (esperado in {S.ELIGIBLE_DATE_ONLY})

    def test_dia_integralmente_dentro(self, janela):
        d = avaliar_data_civil(date(2026, 11, 26), "America/Sao_Paulo", janela(2026, 11, 27))
        assert d.reason_code is ReasonCode.DAY_FULLY_INSIDE

    def test_dia_parcialmente_dentro(self, janela):
        d = avaliar_data_civil(date(2026, 11, 27), "America/Sao_Paulo", janela(2026, 11, 27))
        assert d.reason_code is ReasonCode.DAY_PARTIALLY_INSIDE and not d.eligible

    def test_dia_integralmente_fora(self, janela):
        d = avaliar_data_civil(date(2026, 11, 20), "America/Sao_Paulo", janela(2026, 11, 27))
        assert d.reason_code is ReasonCode.DAY_FULLY_OUTSIDE

    def test_fuso_desconhecido(self, janela):
        d = avaliar_data_civil(date(2026, 11, 26), None, janela(2026, 11, 27))
        assert d.status is S.PENDING_TIMEZONE_CONFIRMATION and not d.eligible

    def test_data_ausente(self, janela):
        d = avaliar_metadados(RawPublicationMetadata(), janela(2026, 11, 27))
        assert d.status is S.PENDING_PUBLICATION_DATE and d.reason_code is ReasonCode.NO_PUBLICATION_DATE

    def test_somente_atualizacao_disponivel(self, janela):
        d = avaliar_metadados(
            RawPublicationMetadata(updated_raw="2026-11-26T10:00:00-03:00"), janela(2026, 11, 27)
        )
        assert d.status is S.PENDING_PUBLICATION_DATE and d.reason_code is ReasonCode.UPDATED_ONLY
        assert not d.eligible

    def test_dia_civil_em_fuso_internacional(self, janela):
        j = janela(2026, 11, 27)  # [26 00:00 BRT, 27 08:00 BRT) = [26 03:00Z, 27 11:00Z)
        # 26/11 em Tokyo = [25 15:00Z, 26 15:00Z): parcialmente dentro
        assert avaliar_data_civil(date(2026, 11, 26), "Asia/Tokyo", j).status is S.PENDING_TIME_CONFIRMATION
        # 26/11 em New York (UTC-5) = [26 05:00Z, 27 05:00Z): integralmente dentro
        assert avaliar_data_civil(date(2026, 11, 26), "America/New_York", j).status is S.ELIGIBLE_DATE_ONLY
        # 26/11 em UTC = [26 00:00Z, 27 00:00Z): comeca antes de 03:00Z -> parcial
        assert avaliar_data_civil(date(2026, 11, 26), "UTC", j).status is S.PENDING_TIME_CONFIRMATION

    def test_metadados_data_sem_fuso_e_pendente(self, janela):
        d = avaliar_metadados(RawPublicationMetadata(published_raw="2026-11-26"), janela(2026, 11, 27))
        assert d.status is S.PENDING_TIMEZONE_CONFIRMATION

    def test_metadados_data_com_fuso(self, janela):
        d = avaliar_metadados(
            RawPublicationMetadata(published_raw="2026-11-26", timezone_hint="America/Sao_Paulo"),
            janela(2026, 11, 27),
        )
        assert d.status is S.ELIGIBLE_DATE_ONLY

    def test_metadados_data_inexistente(self, janela):
        d = avaliar_metadados(RawPublicationMetadata(published_raw="2026-02-30"), janela(2026, 11, 27))
        assert d.status is S.REJECTED_INVALID_METADATA

    def test_contraditorio_offset_x_fuso(self, janela):
        d = avaliar_metadados(
            RawPublicationMetadata(published_raw="2026-11-26T10:00:00+01:00", timezone_hint="America/Sao_Paulo"),
            janela(2026, 11, 27),
        )
        assert d.status is S.REJECTED_INVALID_METADATA
        assert d.reason_code is ReasonCode.CONTRADICTORY_METADATA

    def test_contraditorio_atualizacao_antes_da_publicacao(self, janela):
        d = avaliar_metadados(
            RawPublicationMetadata(
                published_raw="2026-11-26T10:00:00-03:00", updated_raw="2026-11-26T09:00:00-03:00"
            ),
            janela(2026, 11, 27),
        )
        assert d.status is S.REJECTED_INVALID_METADATA

    def test_contraditorio_datas_civis(self, janela):
        d = avaliar_metadados(
            RawPublicationMetadata(published_raw="2026-11-26", updated_raw="2026-11-25", timezone_hint="UTC"),
            janela(2026, 11, 27),
        )
        assert d.reason_code is ReasonCode.CONTRADICTORY_METADATA

    def test_artigo_somente_data(self, janela):
        a = Article.criar(
            source_id="agencia_brasil",
            title="Exemplo",
            original_url="https://agenciabrasil.ebc.com.br/exemplo",
            language="pt",
            retrieved_at=datetime(2026, 11, 27, 9, tzinfo=UTC),
            publication_precision="date",
            publication_evidence=PublicationEvidence(EvidenceType.VISIBLE_DATE, "26/11/2026"),
            published_date=date(2026, 11, 26),
            publication_timezone="America/Sao_Paulo",
            source_family="news_agency",
            themes=["economia"],
            geographic_scope="nacional",
        )
        assert avaliar_artigo(a, janela(2026, 11, 27)).status is S.ELIGIBLE_DATE_ONLY

    def test_artigo_sem_data_com_atualizacao(self, janela):
        a = Article.criar(
            source_id="agencia_brasil",
            title="Exemplo",
            original_url="https://agenciabrasil.ebc.com.br/exemplo",
            language="pt",
            retrieved_at=datetime(2026, 11, 27, 9, tzinfo=UTC),
            publication_precision="unknown",
            publication_evidence=PublicationEvidence(EvidenceType.UNKNOWN, None),
            updated_at=sp(2026, 11, 26, 10),
            source_family="news_agency",
            themes=["economia"],
            geographic_scope="nacional",
        )
        d = avaliar_artigo(a, janela(2026, 11, 27))
        assert d.status is S.PENDING_PUBLICATION_DATE and d.reason_code is ReasonCode.UPDATED_ONLY

    def test_nenhum_pendente_e_elegivel(self, janela):
        j = janela(2026, 11, 27)
        for st in S:
            if st.name.startswith("PENDING"):
                from briefing.models import TemporalDecision

                with pytest.raises(ErroValidacao):
                    TemporalDecision(st, ReasonCode.TIMEZONE_UNKNOWN, "x", j, eligible=True)


# ============================================================ Grupo D
def _janelas_consecutivas(regras, inicio: date, dias: int):
    out, d = [], inicio
    while len(out) < dias:
        if d.weekday() < 5:
            out.append(calcular_janela(d, regras))
        d += timedelta(days=1)
    return out


class TestGrupoDContinuidade:
    def test_fim_sexta_igual_inicio_segunda(self, janela):
        assert janela(2026, 11, 27).end_exclusive == janela(2026, 11, 30).start

    @pytest.mark.parametrize(
        "instante,edicao",
        [
            (sp(2026, 11, 27, 7, 59, 59), date(2026, 11, 27)),
            (sp(2026, 11, 27, 8), date(2026, 11, 30)),
            (sp(2026, 11, 27, 12), date(2026, 11, 30)),
            (sp(2026, 11, 27, 17, 59, 59), date(2026, 11, 30)),
            (sp(2026, 11, 27, 18), date(2026, 11, 30)),
        ],
    )
    def test_sexta_feira_distribuicao(self, regras, instante, edicao):
        sexta = calcular_janela(date(2026, 11, 27), regras)
        segunda = calcular_janela(date(2026, 11, 30), regras)
        esperado = {date(2026, 11, 27): sexta, date(2026, 11, 30): segunda}[edicao]
        outro = segunda if esperado is sexta else sexta
        assert avaliar_instante(instante, esperado).eligible
        assert not avaliar_instante(instante, outro).eligible

    def test_regressao_segunda_nao_volta_a_sexta_18h(self, janela):
        """Falha se a janela de segunda voltar a iniciar sexta 18:00 (regra revogada)."""
        for seg in (date(2026, 11, 30), date(2026, 12, 7), date(2027, 1, 4)):
            j = janela(seg.year, seg.month, seg.day)
            assert j.start.astimezone(SP).time() == time(8, 0), "inicio de segunda deve ser 08:00"
            assert j.start.astimezone(SP).time() != time(18, 0)
            assert avaliar_instante(sp(2026, 11, 27, 12), janela(2026, 11, 30)).eligible

    def test_sobreposicao_8h_entre_segunda_e_sexta(self, regras):
        js = _janelas_consecutivas(regras, date(2026, 11, 30), 5)  # seg..sex
        for a, b in zip(js, js[1:]):
            sobreposicao = a.end_exclusive.astimezone(UTC) - b.start.astimezone(UTC)
            assert sobreposicao == timedelta(hours=8)

    def test_sem_sobreposicao_sexta_segunda(self, janela):
        assert janela(2026, 12, 4).end_exclusive <= janela(2026, 12, 7).start

    @pytest.mark.parametrize("inicio", [date(2026, 11, 2), date(2026, 12, 21), date(2028, 2, 21)])
    def test_sem_lacunas_multiplas_semanas(self, regras, inicio):
        # cobre viradas de mes (nov/dez), de ano (2026/2027) e bissexto (fev/2028)
        js = _janelas_consecutivas(regras, inicio, 30)
        for a, b in zip(js, js[1:]):
            assert b.start.astimezone(UTC) <= a.end_exclusive.astimezone(UTC), (
                f"lacuna entre {a.execution_date} e {b.execution_date}"
            )
        # cobertura continua: toda hora cheia do periodo pertence a alguma janela
        t = js[0].start
        while t < js[-1].end_exclusive:
            assert any(j.contem(t) for j in js), f"instante descoberto {t}"
            t += timedelta(hours=1)
