"""Motor deterministico da janela temporal e da elegibilidade de publicacoes.

Nao consulta o relogio do sistema: a data de execucao e sempre argumento
explicito. As regras (fuso, corte, inicio por dia da semana) vem de
``config/regras.yaml`` via :class:`RegrasJanela` -- fonte unica de verdade; este
modulo nao contem constantes de horario.

Todas as comparacoes de instantes sao feitas apos conversao para UTC, o que
evita o comportamento de comparacao "de parede" do Python entre datetimes que
compartilham o mesmo ``tzinfo``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    ELIGIBLE_STATUSES,
    Article,
    ErroValidacao,
    PublicationPrecision,
    PublicationWindow,
    RawPublicationMetadata,
    ReasonCode,
    TemporalDecision,
    TemporalStatus,
    validar_fuso_iana,
)

__all__ = [
    "DIAS_SEMANA",
    "ErroDataExecucao",
    "RegrasJanela",
    "calcular_janela",
    "avaliar_instante",
    "avaliar_data_civil",
    "avaliar_publicacao",
    "avaliar_artigo",
    "avaliar_metadados",
]

_UTC = ZoneInfo("UTC")

# Mapeamento de nomes (configuracao) -> date.weekday(). Nao e regra de negocio.
DIAS_SEMANA: Mapping[str, int] = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}
_NOME_DIA = {v: k for k, v in DIAS_SEMANA.items()}
_DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ErroDataExecucao(ValueError):
    """Data de execucao fora dos dias de publicacao (ex.: fim de semana)."""


@dataclass(frozen=True)
class InicioJanela:
    dias_antes: int
    horario: time


@dataclass(frozen=True)
class RegrasJanela:
    """Parametros da janela, carregados de ``config/regras.yaml``."""

    fuso: str
    fim_exclusivo: time
    inicio_por_dia: Mapping[int, InicioJanela]

    def __post_init__(self) -> None:
        validar_fuso_iana(self.fuso, "fuso")
        if not isinstance(self.fim_exclusivo, time) or self.fim_exclusivo.tzinfo is not None:
            raise ErroValidacao("fim_exclusivo", "horario local (sem fuso) esperado")
        if not self.inicio_por_dia:
            raise ErroValidacao("inicio_por_dia", "ao menos um dia de execucao e obrigatorio")
        for dia, ini in self.inicio_por_dia.items():
            if dia not in _NOME_DIA:
                raise ErroValidacao("inicio_por_dia", f"dia da semana invalido: {dia!r}")
            if not isinstance(ini, InicioJanela):
                raise ErroValidacao("inicio_por_dia", "InicioJanela esperado")
            if isinstance(ini.dias_antes, bool) or not isinstance(ini.dias_antes, int) or ini.dias_antes < 0:
                raise ErroValidacao(f"inicio_por_dia.{_NOME_DIA[dia]}.dias_antes", "inteiro >= 0")
            if not isinstance(ini.horario, time) or ini.horario.tzinfo is not None:
                raise ErroValidacao(f"inicio_por_dia.{_NOME_DIA[dia]}.horario", "horario local esperado")
            if ini.dias_antes == 0 and ini.horario >= self.fim_exclusivo:
                raise ErroValidacao(
                    f"inicio_por_dia.{_NOME_DIA[dia]}", "inicio nao e anterior ao fim exclusivo"
                )

    @property
    def dias_execucao(self) -> frozenset[int]:
        return frozenset(self.inicio_por_dia)


def calcular_janela(data_execucao: date, regras: RegrasJanela) -> PublicationWindow:
    """Calcula a janela semiaberta ``[start, end_exclusive)`` da edicao.

    Os horarios sao construidos como horario civil no fuso configurado
    (zoneinfo), nunca com offset fixo.
    """
    if isinstance(data_execucao, datetime) or not isinstance(data_execucao, date):
        raise TypeError("data_execucao deve ser datetime.date (nao datetime)")
    dia = data_execucao.weekday()
    if dia not in regras.inicio_por_dia:
        raise ErroDataExecucao(
            f"{data_execucao.isoformat()} ({_NOME_DIA[dia]}) nao e dia de execucao; "
            f"dias validos: {[_NOME_DIA[d] for d in sorted(regras.dias_execucao)]}"
        )
    tz = ZoneInfo(regras.fuso)
    ini = regras.inicio_por_dia[dia]
    start = datetime.combine(data_execucao - timedelta(days=ini.dias_antes), ini.horario, tzinfo=tz)
    end = datetime.combine(data_execucao, regras.fim_exclusivo, tzinfo=tz)
    return PublicationWindow(
        execution_date=data_execucao, timezone=regras.fuso, start=start, end_exclusive=end
    )


# --------------------------------------------------------------------------
# Decisoes
# --------------------------------------------------------------------------


def _decisao(
    status: TemporalStatus, motivo: ReasonCode, descricao: str, janela: PublicationWindow
) -> TemporalDecision:
    return TemporalDecision(
        status=status,
        reason_code=motivo,
        description=descricao,
        window=janela,
        eligible=status in ELIGIBLE_STATUSES,
    )


def avaliar_instante(instante: datetime, janela: PublicationWindow) -> TemporalDecision:
    """Publicacao com data e horario conhecidos (datetime com fuso)."""
    if not isinstance(instante, datetime):
        raise TypeError("instante deve ser datetime")
    if instante.tzinfo is None or instante.utcoffset() is None:
        return _decisao(
            TemporalStatus.PENDING_TIMEZONE_CONFIRMATION,
            ReasonCode.NAIVE_DATETIME,
            "Horario de publicacao sem fuso; nao e possivel confirmar a janela.",
            janela,
        )
    i = instante.astimezone(_UTC)
    if i < janela.start.astimezone(_UTC):
        return _decisao(
            TemporalStatus.REJECTED_OUTSIDE_WINDOW,
            ReasonCode.BEFORE_WINDOW,
            f"Publicado antes do inicio da janela ({janela.descrever()}).",
            janela,
        )
    if i >= janela.end_exclusive.astimezone(_UTC):
        return _decisao(
            TemporalStatus.REJECTED_OUTSIDE_WINDOW,
            ReasonCode.AT_OR_AFTER_WINDOW_END,
            f"Publicado no fim exclusivo da janela ou depois ({janela.descrever()}).",
            janela,
        )
    return _decisao(
        TemporalStatus.ELIGIBLE_CONFIRMED,
        ReasonCode.INSIDE_WINDOW,
        f"Publicado dentro da janela ({janela.descrever()}).",
        janela,
    )


def avaliar_data_civil(dia: date, fuso: str | None, janela: PublicationWindow) -> TemporalDecision:
    """Publicacao com data conhecida e horario desconhecido.

    O dia civil e o intervalo ``[00:00 local do dia, 00:00 local do dia
    seguinte)`` no fuso documentado da publicacao. Sem fuso comprovado, a
    decisao e conservadora (pendente de fuso).
    """
    if isinstance(dia, datetime) or not isinstance(dia, date):
        raise TypeError("dia deve ser datetime.date")
    if fuso is None:
        return _decisao(
            TemporalStatus.PENDING_TIMEZONE_CONFIRMATION,
            ReasonCode.TIMEZONE_UNKNOWN,
            "Data sem horario e sem fuso comprovado; dia civil indeterminado.",
            janela,
        )
    tz = ZoneInfo(validar_fuso_iana(fuso, "fuso"))
    ini_dia = datetime.combine(dia, time(0), tzinfo=tz).astimezone(_UTC)
    fim_dia = datetime.combine(dia + timedelta(days=1), time(0), tzinfo=tz).astimezone(_UTC)
    j_ini = janela.start.astimezone(_UTC)
    j_fim = janela.end_exclusive.astimezone(_UTC)
    if ini_dia >= j_ini and fim_dia <= j_fim:
        return _decisao(
            TemporalStatus.ELIGIBLE_DATE_ONLY,
            ReasonCode.DAY_FULLY_INSIDE,
            f"Dia {dia.isoformat()} ({fuso}) integralmente contido na janela.",
            janela,
        )
    if fim_dia <= j_ini or ini_dia >= j_fim:
        return _decisao(
            TemporalStatus.REJECTED_OUTSIDE_WINDOW,
            ReasonCode.DAY_FULLY_OUTSIDE,
            f"Dia {dia.isoformat()} ({fuso}) integralmente fora da janela.",
            janela,
        )
    return _decisao(
        TemporalStatus.PENDING_TIME_CONFIRMATION,
        ReasonCode.DAY_PARTIALLY_INSIDE,
        f"Dia {dia.isoformat()} ({fuso}) apenas parcialmente na janela; horario necessario.",
        janela,
    )


def avaliar_publicacao(
    janela: PublicationWindow,
    *,
    published_at: datetime | None = None,
    published_date: date | None = None,
    timezone: str | None = None,
    updated_available: bool = False,
) -> TemporalDecision:
    """Nucleo comum: instante > data civil > ausencia de data.

    ``updated_available`` apenas qualifica o motivo; a data de atualizacao
    nunca substitui a de publicacao.
    """
    if published_at is not None:
        return avaliar_instante(published_at, janela)
    if published_date is not None:
        return avaliar_data_civil(published_date, timezone, janela)
    if updated_available:
        return _decisao(
            TemporalStatus.PENDING_PUBLICATION_DATE,
            ReasonCode.UPDATED_ONLY,
            "Somente a data de atualizacao esta disponivel; ela nao substitui a publicacao original.",
            janela,
        )
    return _decisao(
        TemporalStatus.PENDING_PUBLICATION_DATE,
        ReasonCode.NO_PUBLICATION_DATE,
        "Data original de publicacao ausente.",
        janela,
    )


def avaliar_artigo(artigo: Article, janela: PublicationWindow) -> TemporalDecision:
    """Decisao temporal para um :class:`Article` (metadados ja validados)."""
    if artigo.publication_precision is PublicationPrecision.DATETIME:
        return avaliar_instante(artigo.published_at, janela)  # type: ignore[arg-type]
    if artigo.publication_precision is PublicationPrecision.DATE:
        return avaliar_data_civil(artigo.published_date, artigo.publication_timezone, janela)  # type: ignore[arg-type]
    return avaliar_publicacao(janela, updated_available=artigo.updated_at is not None)


# --------------------------------------------------------------------------
# Metadados brutos
# --------------------------------------------------------------------------


class _Invalido(Exception):
    def __init__(self, motivo: ReasonCode, descricao: str) -> None:
        self.motivo = motivo
        self.descricao = descricao


def _interpretar(valor: str, campo: str) -> date | datetime:
    """Aceita ISO 8601: data (AAAA-MM-DD) ou data e hora (com ou sem offset)."""
    v = valor.strip()
    if _DATA_RE.match(v):
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise _Invalido(ReasonCode.UNPARSEABLE_VALUE, f"{campo}: data inexistente {valor!r}") from None
    if "T" not in v and " " not in v:
        raise _Invalido(ReasonCode.UNPARSEABLE_VALUE, f"{campo}: formato nao reconhecido {valor!r}")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        raise _Invalido(ReasonCode.UNPARSEABLE_VALUE, f"{campo}: valor invalido {valor!r}") from None


def avaliar_metadados(meta: RawPublicationMetadata, janela: PublicationWindow) -> TemporalDecision:
    """Valida metadados **recebidos** e produz a decisao temporal.

    Formatos aceitos nesta fase: ISO 8601. (RFC 2822 de feeds RSS fica para a
    Fase 1B.) ``timezone_hint`` deve vir de evidencia; nunca e inferido.
    """
    if not isinstance(meta, RawPublicationMetadata):
        raise TypeError("RawPublicationMetadata esperado")
    try:
        tz_nome: str | None = None
        if meta.timezone_hint is not None:
            try:
                ZoneInfo(meta.timezone_hint)
            except (ZoneInfoNotFoundError, ValueError):
                raise _Invalido(
                    ReasonCode.INVALID_TIMEZONE, f"Fuso IANA invalido: {meta.timezone_hint!r}"
                ) from None
            tz_nome = meta.timezone_hint

        publicado = None
        if meta.published_raw is not None and meta.published_raw.strip():
            publicado = _interpretar(meta.published_raw, "published")
        atualizado = None
        if meta.updated_raw is not None and meta.updated_raw.strip():
            atualizado = _interpretar(meta.updated_raw, "updated")

        if publicado is None:
            return avaliar_publicacao(janela, updated_available=atualizado is not None)

        # Normaliza o instante publicado (se houver horario).
        instante: datetime | None = None
        if isinstance(publicado, datetime):
            if publicado.tzinfo is not None:
                if tz_nome is not None:
                    esperado = publicado.astimezone(ZoneInfo(tz_nome)).utcoffset()
                    if esperado != publicado.utcoffset():
                        raise _Invalido(
                            ReasonCode.CONTRADICTORY_METADATA,
                            f"Offset {publicado.utcoffset()} incompativel com o fuso {tz_nome}.",
                        )
                instante = publicado
            elif tz_nome is not None:
                instante = publicado.replace(tzinfo=ZoneInfo(tz_nome))
            else:
                return _decisao(
                    TemporalStatus.PENDING_TIMEZONE_CONFIRMATION,
                    ReasonCode.NAIVE_DATETIME,
                    "Horario de publicacao sem fuso e sem fuso comprovado por evidencia.",
                    janela,
                )

        # Coerencia com a data de atualizacao.
        if atualizado is not None:
            if isinstance(atualizado, datetime):
                if atualizado.tzinfo is None:
                    if tz_nome is None:
                        atualizado = None  # sem fuso: nao comparavel, nao usado
                    else:
                        atualizado = atualizado.replace(tzinfo=ZoneInfo(tz_nome))
            if atualizado is not None:
                if instante is not None and isinstance(atualizado, datetime):
                    if atualizado.astimezone(_UTC) < instante.astimezone(_UTC):
                        raise _Invalido(
                            ReasonCode.CONTRADICTORY_METADATA,
                            "Data de atualizacao anterior a data de publicacao.",
                        )
                else:
                    d_pub = publicado if not isinstance(publicado, datetime) else None
                    d_upd = atualizado if not isinstance(atualizado, datetime) else None
                    if d_pub is not None and d_upd is not None and d_upd < d_pub:
                        raise _Invalido(
                            ReasonCode.CONTRADICTORY_METADATA,
                            "Data de atualizacao anterior a data de publicacao.",
                        )

        if instante is not None:
            return avaliar_instante(instante, janela)
        return avaliar_data_civil(publicado, tz_nome, janela)  # type: ignore[arg-type]
    except _Invalido as erro:
        return _decisao(TemporalStatus.REJECTED_INVALID_METADATA, erro.motivo, erro.descricao, janela)
