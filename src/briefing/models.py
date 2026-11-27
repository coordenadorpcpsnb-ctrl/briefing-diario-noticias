"""Contratos de dados do briefing (Fase 1A).

Todos os modelos sao ``dataclasses`` imutaveis (``frozen=True``) com validacao
explicita em ``__post_init__`` e serializacao previsivel por ``to_dict`` /
``from_dict``. ``from_dict`` e estrito: campos desconhecidos sao rejeitados.

Tres niveis de informacao temporal sao mantidos separados:

1. **Metadado recebido** (:class:`RawPublicationMetadata`): valores brutos,
   como vieram da fonte, sem garantia de validade. Pode estar incompleto,
   invalido ou contraditorio.
2. **Metadado validado** (:class:`Article`): so aceita combinacoes coerentes de
   ``published_at`` / ``published_date`` / ``publication_timezone`` /
   ``publication_precision``. Nunca aceita datetime sem fuso.
3. **Decisao temporal** (:class:`TemporalDecision`): resultado da comparacao com
   uma :class:`PublicationWindow`. Elegibilidade temporal NAO e aprovacao
   editorial nem confirmacao jornalistica independente.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "ErroValidacao",
    "SourceFamily",
    "GeographicScope",
    "AccessStatus",
    "CollectionMethod",
    "TermsStatus",
    "RegistrationStatus",
    "UrlProvenance",
    "EvidenceType",
    "PublicationPrecision",
    "TemporalStatus",
    "ReasonCode",
    "CollectionStatus",
    "ELIGIBLE_STATUSES",
    "ThemeConfig",
    "SourceConfig",
    "PublicationEvidence",
    "RawPublicationMetadata",
    "Article",
    "PublicationWindow",
    "TemporalDecision",
    "CollectionResult",
    "gerar_article_id",
    "validar_limite_trecho",
    "normalizar_trecho",
    "verificar_limite_trecho",
    "validar_url",
    "redigir_url",
    "validar_fuso_iana",
]

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x20\x7f]")


class ErroValidacao(ValueError):
    """Erro de validacao de contrato. ``campo`` identifica o atributo."""

    def __init__(self, campo: str, motivo: str) -> None:
        self.campo = campo
        self.motivo = motivo
        super().__init__(f"{campo}: {motivo}")


# --------------------------------------------------------------------------
# Enumeracoes
# --------------------------------------------------------------------------


class SourceFamily(str, Enum):
    """Familias de fontes. A familia NAO comprova independencia editorial."""

    NEWS_AGENCY = "news_agency"
    GENERAL_PRESS = "general_press"
    FINANCIAL_PRESS = "financial_press"
    GOVERNMENT = "government"
    SCIENTIFIC_INSTITUTION = "scientific_institution"
    INDUSTRY_ASSOCIATION = "industry_association"
    SPECIALIZED_PRESS = "specialized_press"
    CORPORATE_PRIMARY = "corporate_primary"
    CIVIL_SOCIETY = "civil_society"


class GeographicScope(str, Enum):
    NACIONAL = "nacional"
    INTERNACIONAL = "internacional"


class AccessStatus(str, Enum):
    """Estado de acesso tecnico. ``unverified`` = nunca testado."""

    UNVERIFIED = "unverified"
    ACCESSIBLE = "accessible"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class CollectionMethod(str, Enum):
    """Metodos de coleta previstos (nenhum implementado na Fase 1A)."""

    RSS = "rss"
    HTML_LISTING = "html_listing"
    API = "api"


class TermsStatus(str, Enum):
    UNVERIFIED = "unverified"
    PERMITTED = "permitted"
    RESTRICTED = "restricted"


class RegistrationStatus(str, Enum):
    """``pending``: cadastro incompleto (ex.: URL nao estabelecida)."""

    COMPLETE = "complete"
    PENDING = "pending"


class UrlProvenance(str, Enum):
    """De onde veio a ``homepage_url`` cadastrada (rastreabilidade)."""

    PROVIDED_BY_OWNER = "provided_by_owner"
    OBSERVED_IN_HISTORICAL_EDITION = "observed_in_historical_edition"
    PUBLIC_KNOWLEDGE_UNTESTED = "public_knowledge_untested"


class EvidenceType(str, Enum):
    """Tipos de evidencia de publicacao (possibilidades futuras de coleta)."""

    RSS = "rss"
    JSON_LD_DATE_PUBLISHED = "json_ld_date_published"
    HTML_META = "html_meta"
    VISIBLE_DATE = "visible_date"
    API = "api"
    OTHER = "other"
    UNKNOWN = "unknown"


class PublicationPrecision(str, Enum):
    DATETIME = "datetime"  # data e horario com fuso conhecido
    DATE = "date"  # somente a data civil
    UNKNOWN = "unknown"  # data original de publicacao desconhecida


class TemporalStatus(str, Enum):
    ELIGIBLE_CONFIRMED = "ELIGIBLE_CONFIRMED"
    ELIGIBLE_DATE_ONLY = "ELIGIBLE_DATE_ONLY"
    PENDING_TIME_CONFIRMATION = "PENDING_TIME_CONFIRMATION"
    PENDING_TIMEZONE_CONFIRMATION = "PENDING_TIMEZONE_CONFIRMATION"
    REJECTED_OUTSIDE_WINDOW = "REJECTED_OUTSIDE_WINDOW"
    REJECTED_INVALID_METADATA = "REJECTED_INVALID_METADATA"
    PENDING_PUBLICATION_DATE = "PENDING_PUBLICATION_DATE"


ELIGIBLE_STATUSES = frozenset(
    {TemporalStatus.ELIGIBLE_CONFIRMED, TemporalStatus.ELIGIBLE_DATE_ONLY}
)


class ReasonCode(str, Enum):
    INSIDE_WINDOW = "inside_window"
    BEFORE_WINDOW = "before_window"
    AT_OR_AFTER_WINDOW_END = "at_or_after_window_end"
    DAY_FULLY_INSIDE = "day_fully_inside"
    DAY_PARTIALLY_INSIDE = "day_partially_inside"
    DAY_FULLY_OUTSIDE = "day_fully_outside"
    TIMEZONE_UNKNOWN = "timezone_unknown"
    NAIVE_DATETIME = "naive_datetime"
    NO_PUBLICATION_DATE = "no_publication_date"
    UPDATED_ONLY = "updated_only"
    UNPARSEABLE_VALUE = "unparseable_value"
    INVALID_TIMEZONE = "invalid_timezone"
    CONTRADICTORY_METADATA = "contradictory_metadata"


class CollectionStatus(str, Enum):
    SUCCESS_WITH_RESULTS = "success_with_results"
    SUCCESS_EMPTY = "success_empty"
    TECHNICAL_FAILURE = "technical_failure"
    SOURCE_NOT_CONFIGURED = "source_not_configured"
    SOURCE_NOT_QUERIED = "source_not_queried"
    ACCESS_RESTRICTED = "access_restricted"
    PARTIAL = "partial"


# --------------------------------------------------------------------------
# Utilitarios de validacao
# --------------------------------------------------------------------------


def redigir_url(url: str) -> str:
    """Remove credenciais embutidas (``user:senha@``) para mensagens de erro."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url invalida>"
    if "@" in parts.netloc:
        host = parts.netloc.rsplit("@", 1)[1]
        return urlunsplit((parts.scheme, "***@" + host, parts.path, parts.query, parts.fragment))
    return url


def validar_url(url: Any, campo: str, *, somente_https: bool = True) -> str:
    """Validacao **exclusivamente sintatica** de URL.

    Passar nesta validacao NAO significa que a URL existe ou responde.
    Rejeita esquemas diferentes de https (ou http, se ``somente_https=False``),
    credenciais embutidas, ausencia de host, espacos e caracteres de controle.
    """
    if not isinstance(url, str) or not url:
        raise ErroValidacao(campo, "URL deve ser texto nao vazio")
    seguro = redigir_url(url)
    if _CONTROL_RE.search(url):
        raise ErroValidacao(campo, f"URL contem espaco ou caractere de controle: {seguro!r}")
    try:
        parts = urlsplit(url)
        port = parts.port  # dispara ValueError para porta invalida
    except ValueError:
        raise ErroValidacao(campo, f"URL malformada: {seguro!r}") from None
    permitidos = {"https"} if somente_https else {"https", "http"}
    if parts.scheme.lower() not in permitidos:
        raise ErroValidacao(
            campo, f"esquema nao permitido {parts.scheme!r} (permitidos: {sorted(permitidos)})"
        )
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ErroValidacao(campo, f"URL com credenciais embutidas nao e permitida: {seguro!r}")
    host = parts.hostname
    if not host or "." not in host:
        raise ErroValidacao(campo, f"URL sem host valido: {seguro!r}")
    if port is not None and not (1 <= port <= 65535):
        raise ErroValidacao(campo, f"porta invalida: {seguro!r}")
    return url


def validar_fuso_iana(nome: Any, campo: str) -> str:
    """Confirma que ``nome`` e um identificador IANA carregavel por zoneinfo."""
    if not isinstance(nome, str) or not nome or nome != nome.strip():
        raise ErroValidacao(campo, "fuso deve ser identificador IANA nao vazio")
    try:
        ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError):
        raise ErroValidacao(campo, f"fuso IANA invalido: {nome!r}") from None
    return nome


def _exigir_id(valor: Any, campo: str) -> str:
    if not isinstance(valor, str) or not _ID_RE.match(valor):
        raise ErroValidacao(campo, f"identificador invalido {valor!r} (padrao: {_ID_RE.pattern})")
    return valor


def _exigir_texto(valor: Any, campo: str, *, opcional: bool = False) -> str | None:
    if valor is None and opcional:
        return None
    if not isinstance(valor, str) or not valor.strip():
        raise ErroValidacao(campo, "texto nao vazio obrigatorio")
    return valor


def _exigir_bool(valor: Any, campo: str) -> bool:
    if not isinstance(valor, bool):
        raise ErroValidacao(campo, "valor booleano obrigatorio")
    return valor


def _exigir_aware(valor: Any, campo: str, *, opcional: bool = False) -> datetime | None:
    if valor is None and opcional:
        return None
    if not isinstance(valor, datetime):
        raise ErroValidacao(campo, "datetime obrigatorio")
    if valor.tzinfo is None or valor.utcoffset() is None:
        raise ErroValidacao(campo, "datetime sem fuso nao e aceito")
    return valor


def _enum(cls: type[Enum], valor: Any, campo: str) -> Any:
    if isinstance(valor, cls):
        return valor
    try:
        return cls(valor)
    except ValueError:
        validos = [m.value for m in cls]  # type: ignore[attr-defined]
        raise ErroValidacao(campo, f"valor desconhecido {valor!r}; validos: {validos}") from None


def _enum_tuple(cls: type[Enum], valores: Any, campo: str) -> tuple:
    if not isinstance(valores, (list, tuple)):
        raise ErroValidacao(campo, "lista obrigatoria")
    convertidos = tuple(_enum(cls, v, f"{campo}[{i}]") for i, v in enumerate(valores))
    if len(set(convertidos)) != len(convertidos):
        raise ErroValidacao(campo, "valores duplicados")
    return convertidos


def _id_tuple(valores: Any, campo: str, *, nao_vazio: bool) -> tuple[str, ...]:
    if not isinstance(valores, (list, tuple)):
        raise ErroValidacao(campo, "lista obrigatoria")
    ids = tuple(_exigir_id(v, f"{campo}[{i}]") for i, v in enumerate(valores))
    if nao_vazio and not ids:
        raise ErroValidacao(campo, "lista nao pode ser vazia")
    if len(set(ids)) != len(ids):
        raise ErroValidacao(campo, "valores duplicados")
    return ids


def _str_tuple(valores: Any, campo: str, *, nao_vazio: bool) -> tuple[str, ...]:
    if not isinstance(valores, (list, tuple)):
        raise ErroValidacao(campo, "lista obrigatoria")
    itens = tuple(_exigir_texto(v, f"{campo}[{i}]") for i, v in enumerate(valores))
    if nao_vazio and not itens:
        raise ErroValidacao(campo, "lista nao pode ser vazia")
    if len({i.casefold() for i in itens}) != len(itens):  # type: ignore[union-attr]
        raise ErroValidacao(campo, "valores duplicados")
    return itens  # type: ignore[return-value]


def _serializar(valor: Any) -> Any:
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, tuple):
        return [_serializar(v) for v in valor]
    if hasattr(valor, "to_dict"):
        return valor.to_dict()
    return valor


def _to_dict(obj: Any) -> dict[str, Any]:
    return {f.name: _serializar(getattr(obj, f.name)) for f in fields(obj)}


def _checar_chaves(cls: type, dados: Mapping[str, Any]) -> None:
    if not isinstance(dados, Mapping):
        raise ErroValidacao(cls.__name__, "objeto (mapeamento) esperado")
    conhecidos = {f.name for f in fields(cls)}
    desconhecidos = sorted(set(dados) - conhecidos)
    if desconhecidos:
        raise ErroValidacao(cls.__name__, f"campos desconhecidos: {desconhecidos}")


def _parse_dt(valor: Any, campo: str) -> datetime | None:
    if valor is None or isinstance(valor, datetime):
        return valor
    if not isinstance(valor, str):
        raise ErroValidacao(campo, "datetime ISO 8601 esperado")
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        raise ErroValidacao(campo, f"datetime ISO 8601 invalido: {valor!r}") from None


def _parse_date(valor: Any, campo: str) -> date | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        raise ErroValidacao(campo, "data civil esperada, recebido datetime")
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str):
        raise ErroValidacao(campo, "data ISO (AAAA-MM-DD) esperada")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ErroValidacao(campo, f"data invalida: {valor!r}") from None


# --------------------------------------------------------------------------
# Configuracao: temas e fontes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeConfig:
    """Tema editorial. A meta editorial nao obriga preenchimento."""

    theme_id: str
    name: str
    description: str
    geographic_scopes: tuple[GeographicScope, ...]
    coverage: tuple[str, ...]
    keywords_pt: tuple[str, ...]
    keywords_en: tuple[str, ...]
    source_families: tuple[SourceFamily, ...]
    editorial_target_min: int
    editorial_target_max: int

    def __post_init__(self) -> None:
        _exigir_id(self.theme_id, "theme_id")
        _exigir_texto(self.name, "name")
        _exigir_texto(self.description, "description")
        object.__setattr__(
            self,
            "geographic_scopes",
            _enum_tuple(GeographicScope, self.geographic_scopes, "geographic_scopes"),
        )
        if not self.geographic_scopes:
            raise ErroValidacao("geographic_scopes", "ao menos um escopo e obrigatorio")
        object.__setattr__(self, "coverage", _str_tuple(self.coverage, "coverage", nao_vazio=True))
        object.__setattr__(
            self, "keywords_pt", _str_tuple(self.keywords_pt, "keywords_pt", nao_vazio=True)
        )
        object.__setattr__(
            self, "keywords_en", _str_tuple(self.keywords_en, "keywords_en", nao_vazio=True)
        )
        object.__setattr__(
            self,
            "source_families",
            _enum_tuple(SourceFamily, self.source_families, "source_families"),
        )
        if not self.source_families:
            raise ErroValidacao("source_families", "ao menos uma familia e obrigatoria")
        for nome in ("editorial_target_min", "editorial_target_max"):
            v = getattr(self, nome)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ErroValidacao(nome, "inteiro nao negativo obrigatorio")
        if self.editorial_target_min > self.editorial_target_max:
            raise ErroValidacao("editorial_target_min", "maior que editorial_target_max")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, dados: Mapping[str, Any]) -> "ThemeConfig":
        _checar_chaves(cls, dados)
        try:
            return cls(**dados)
        except TypeError as exc:
            raise ErroValidacao(cls.__name__, f"campos obrigatorios ausentes ({exc})") from None


@dataclass(frozen=True)
class SourceConfig:
    """Fonte cadastrada.

    - *cadastrada* != *habilitada*: ``enabled`` so pode ser ``True`` com cadastro
      completo, URL valida, metodo de coleta declarado (com a URL que o metodo
      exige) e ``access_status == accessible`` (acesso efetivamente verificado).
    - *obrigatoria* != *disponivel*: ``mandatory`` nao implica acesso confirmado.
    - URLs de feed/API nunca sao presumidas; desconhecido permanece ``None``.
    """

    source_id: str
    name: str
    homepage_url: str | None
    country: str | None
    language: str
    family: SourceFamily
    themes: tuple[str, ...]
    enabled: bool
    mandatory: bool
    mandatory_themes: tuple[str, ...]
    access_status: AccessStatus
    collection_methods: tuple[CollectionMethod, ...]
    terms_status: TermsStatus
    notes: str
    registration_status: RegistrationStatus = RegistrationStatus.COMPLETE
    url_provenance: UrlProvenance | None = None
    rss_url: str | None = None
    listing_url: str | None = None
    timezone: str | None = None
    ownership_group: str | None = None
    upstream_agency: str | None = None

    def __post_init__(self) -> None:
        _exigir_id(self.source_id, "source_id")
        _exigir_texto(self.name, "name")
        _exigir_texto(self.notes, "notes")
        object.__setattr__(
            self,
            "registration_status",
            _enum(RegistrationStatus, self.registration_status, "registration_status"),
        )
        object.__setattr__(self, "family", _enum(SourceFamily, self.family, "family"))
        object.__setattr__(
            self, "access_status", _enum(AccessStatus, self.access_status, "access_status")
        )
        object.__setattr__(self, "terms_status", _enum(TermsStatus, self.terms_status, "terms_status"))
        object.__setattr__(
            self,
            "collection_methods",
            _enum_tuple(CollectionMethod, self.collection_methods, "collection_methods"),
        )
        if self.url_provenance is not None:
            object.__setattr__(
                self, "url_provenance", _enum(UrlProvenance, self.url_provenance, "url_provenance")
            )
        object.__setattr__(self, "themes", _id_tuple(self.themes, "themes", nao_vazio=True))
        object.__setattr__(
            self,
            "mandatory_themes",
            _id_tuple(self.mandatory_themes, "mandatory_themes", nao_vazio=False),
        )
        _exigir_bool(self.enabled, "enabled")
        _exigir_bool(self.mandatory, "mandatory")
        if not isinstance(self.language, str) or not _LANG_RE.match(self.language):
            raise ErroValidacao("language", f"codigo de idioma invalido {self.language!r}")
        if self.country is not None and (
            not isinstance(self.country, str) or not _COUNTRY_RE.match(self.country)
        ):
            raise ErroValidacao("country", f"codigo ISO 3166-1 alfa-2 invalido {self.country!r}")
        for nome in ("ownership_group", "upstream_agency"):
            _exigir_texto(getattr(self, nome), nome, opcional=True)
        if self.timezone is not None:
            validar_fuso_iana(self.timezone, "timezone")

        # URLs: validacao exclusivamente sintatica.
        if self.homepage_url is None:
            if self.registration_status is not RegistrationStatus.PENDING:
                raise ErroValidacao(
                    "homepage_url", "obrigatoria quando o cadastro esta completo"
                )
        else:
            validar_url(self.homepage_url, "homepage_url")
            if self.url_provenance is None:
                raise ErroValidacao("url_provenance", "obrigatoria quando ha homepage_url")
        for nome in ("rss_url", "listing_url"):
            v = getattr(self, nome)
            if v is not None:
                validar_url(v, nome)

        # mandatory x mandatory_themes
        if self.mandatory and not self.mandatory_themes:
            raise ErroValidacao("mandatory_themes", "fonte obrigatoria exige mandatory_themes")
        if not self.mandatory and self.mandatory_themes:
            raise ErroValidacao("mandatory_themes", "so pode ser preenchido se mandatory=true")
        fora = sorted(set(self.mandatory_themes) - set(self.themes))
        if fora:
            raise ErroValidacao("mandatory_themes", f"temas fora de 'themes': {fora}")

        # Metodo declarado precisa da URL correspondente.
        if CollectionMethod.RSS in self.collection_methods and self.rss_url is None:
            raise ErroValidacao("rss_url", "metodo 'rss' declarado sem rss_url")
        if CollectionMethod.HTML_LISTING in self.collection_methods and self.listing_url is None:
            raise ErroValidacao("listing_url", "metodo 'html_listing' declarado sem listing_url")

        # Habilitacao para coleta.
        if self.enabled:
            if self.registration_status is not RegistrationStatus.COMPLETE:
                raise ErroValidacao("enabled", "fonte com cadastro pendente nao pode ser habilitada")
            if self.homepage_url is None:
                raise ErroValidacao("enabled", "fonte sem URL valida nao pode ser habilitada")
            if not self.collection_methods:
                raise ErroValidacao("enabled", "fonte sem metodo de coleta nao pode ser habilitada")
            if self.access_status is not AccessStatus.ACCESSIBLE:
                raise ErroValidacao(
                    "enabled", "fonte sem acesso verificado ('accessible') nao pode ser habilitada"
                )

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, dados: Mapping[str, Any]) -> "SourceConfig":
        _checar_chaves(cls, dados)
        try:
            return cls(**dados)
        except TypeError as exc:
            raise ErroValidacao(cls.__name__, f"campos obrigatorios ausentes ({exc})") from None


# --------------------------------------------------------------------------
# Evidencia e metadados de publicacao
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationEvidence:
    """Evidencia estruturada da data de publicacao. Nunca deve ser ficticia.

    ``raw_value`` guarda o valor original exatamente como observado.
    """

    evidence_type: EvidenceType
    raw_value: str | None
    source_url: str | None = None
    timezone: str | None = None
    retrieved_at: datetime | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_type", _enum(EvidenceType, self.evidence_type, "evidence_type")
        )
        if self.evidence_type is EvidenceType.UNKNOWN:
            if self.raw_value is not None and not isinstance(self.raw_value, str):
                raise ErroValidacao("raw_value", "texto esperado")
        else:
            _exigir_texto(self.raw_value, "raw_value")
        if self.source_url is not None:
            validar_url(self.source_url, "source_url", somente_https=False)
        if self.timezone is not None:
            validar_fuso_iana(self.timezone, "timezone")
        _exigir_aware(self.retrieved_at, "retrieved_at", opcional=True)
        _exigir_texto(self.notes, "notes", opcional=True)

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, dados: Mapping[str, Any]) -> "PublicationEvidence":
        _checar_chaves(cls, dados)
        d = dict(dados)
        d["retrieved_at"] = _parse_dt(d.get("retrieved_at"), "retrieved_at")
        try:
            return cls(**d)
        except TypeError as exc:
            raise ErroValidacao(cls.__name__, f"campos obrigatorios ausentes ({exc})") from None


@dataclass(frozen=True)
class RawPublicationMetadata:
    """Metadado temporal **recebido** (bruto, nao validado).

    Pode conter valores invalidos, incompletos ou contraditorios; a validacao e
    a decisao temporal ficam a cargo de :func:`briefing.janela.avaliar_metadados`.
    ``timezone_hint`` so deve ser preenchido com fuso comprovado por evidencia;
    nunca inferido de idioma ou pais do veiculo.
    """

    published_raw: str | None = None
    updated_raw: str | None = None
    timezone_hint: str | None = None
    evidence: tuple[PublicationEvidence, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for nome in ("published_raw", "updated_raw", "timezone_hint"):
            v = getattr(self, nome)
            if v is not None and not isinstance(v, str):
                raise ErroValidacao(nome, "texto ou nulo esperado")
        if not isinstance(self.evidence, (list, tuple)) or not all(
            isinstance(e, PublicationEvidence) for e in self.evidence
        ):
            raise ErroValidacao("evidence", "lista de PublicationEvidence esperada")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


# --------------------------------------------------------------------------
# Trecho da fonte (source_excerpt)
# --------------------------------------------------------------------------


def validar_limite_trecho(valor: Any, campo: str = "source_excerpt_max_chars") -> int:
    """Valida o limite de caracteres do trecho: inteiro positivo (nao bool)."""
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErroValidacao(campo, f"inteiro positivo esperado, recebido {type(valor).__name__}")
    if valor <= 0:
        raise ErroValidacao(campo, f"inteiro positivo esperado, recebido {valor}")
    return valor


def _normalizar_texto(texto: str) -> str:
    # NFC + colapso de espacos (inclui quebras de linha/tabulacoes) + strip.
    return " ".join(unicodedata.normalize("NFC", texto).split())


def normalizar_trecho(texto: str | None, limite: int) -> tuple[str | None, bool]:
    """Normaliza o trecho/descricao **recebido da fonte**.

    Deterministico, sem IA e sem rede. Retorna ``(trecho, truncado)``:

    - ``None`` -> ``(None, False)``.
    - Texto vazio ou so com espacos -> ``(None, False)`` (tratado como ausente).
    - Normalizacao: Unicode NFC, espacos internos colapsados, bordas removidas.
    - Ate ``limite`` caracteres (apos normalizacao) -> preservado, ``False``.
    - Acima do limite -> reduzido a no maximo ``limite`` caracteres, ``True``.
      O corte e por caractere (code point), nunca por byte; nao separa um
      caractere de suas marcas combinantes; nao acrescenta reticencias.
    """
    validar_limite_trecho(limite, "limite")
    if texto is None:
        return None, False
    if not isinstance(texto, str):
        raise ErroValidacao("texto", "texto ou nulo esperado")
    normal = _normalizar_texto(texto)
    if not normal:
        return None, False
    if len(normal) <= limite:
        return normal, False
    corte = limite
    # Nao deixar marca combinante orfa: recua ate o inicio do cluster.
    while corte > 0 and unicodedata.combining(normal[corte]):
        corte -= 1
    trecho = normal[:corte].rstrip() or normal[:limite].rstrip()
    return trecho, True


def verificar_limite_trecho(artigo: "Article", limite: int) -> None:
    """Confere um Article ja construido contra o limite configurado."""
    validar_limite_trecho(limite, "limite")
    if artigo.source_excerpt is not None and len(artigo.source_excerpt) > limite:
        raise ErroValidacao(
            "source_excerpt", f"excede o limite configurado de {limite} caracteres"
        )


# --------------------------------------------------------------------------
# Article
# --------------------------------------------------------------------------


def gerar_article_id(source_id: str, original_url: str) -> str:
    """ID estavel e reproduzivel: SHA-256 de ``source_id`` + URL original.

    Sem aleatoriedade e sem canonicalizacao agressiva (apenas a URL exata).
    """
    _exigir_id(source_id, "source_id")
    if not isinstance(original_url, str) or not original_url:
        raise ErroValidacao("original_url", "URL obrigatoria")
    digest = hashlib.sha256(f"{source_id}\n{original_url}".encode("utf-8")).hexdigest()
    return f"art_{digest[:32]}"


@dataclass(frozen=True)
class Article:
    """Noticia com metadados **validados**.

    Regras de coerencia temporal:

    - ``DATETIME``: ``published_at`` obrigatorio e com fuso. Se
      ``publication_timezone`` e ``published_date`` forem informados, a data
      deve coincidir com ``published_at`` convertido para esse fuso.
    - ``DATE``: ``published_at`` deve ser nulo (nao se cria meia-noite
      ficticia); ``published_date`` obrigatorio; ``publication_timezone`` pode
      ser nulo (fuso nao comprovado -> decisao pendente).
    - ``UNKNOWN``: ``published_at`` e ``published_date`` nulos.
    - ``updated_at`` nunca substitui ``published_at``; se ambos existirem,
      ``updated_at >= published_at``.
    - ``retrieved_at`` e o momento da coleta, nunca a data de publicacao.

    Trecho da fonte:

    - ``source_excerpt`` e exclusivamente um trecho/descricao fornecido pela
      fonte, ja normalizado por :func:`normalizar_trecho`. NAO e analise
      editorial nem resumo executivo (modelo separado, Fase 3).
    - ``excerpt_truncated=True`` exige ``source_excerpt``.
    - O limite de tamanho vem de ``config/regras.yaml``
      (``limites.source_excerpt_max_chars``) e e aplicado por
      :meth:`criar_a_partir_da_fonte` / :func:`verificar_limite_trecho`; o
      modelo nao carrega YAML.
    """

    article_id: str
    source_id: str
    title: str
    original_url: str
    language: str
    retrieved_at: datetime
    publication_precision: PublicationPrecision
    publication_evidence: PublicationEvidence
    source_family: SourceFamily
    themes: tuple[str, ...]
    geographic_scope: GeographicScope
    canonical_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    published_date: date | None = None
    publication_timezone: str | None = None
    updated_at: datetime | None = None
    upstream_agency: str | None = None
    source_excerpt: str | None = None
    excerpt_truncated: bool = False

    def __post_init__(self) -> None:
        _exigir_id(self.source_id, "source_id")
        _exigir_texto(self.title, "title")
        validar_url(self.original_url, "original_url", somente_https=False)
        if self.canonical_url is not None:
            validar_url(self.canonical_url, "canonical_url", somente_https=False)
        esperado = gerar_article_id(self.source_id, self.original_url)
        if self.article_id != esperado:
            raise ErroValidacao(
                "article_id", "nao corresponde ao ID estavel derivado de source_id + original_url"
            )
        if not isinstance(self.language, str) or not _LANG_RE.match(self.language):
            raise ErroValidacao("language", f"codigo de idioma invalido {self.language!r}")
        _exigir_aware(self.retrieved_at, "retrieved_at")
        object.__setattr__(
            self,
            "publication_precision",
            _enum(PublicationPrecision, self.publication_precision, "publication_precision"),
        )
        if not isinstance(self.publication_evidence, PublicationEvidence):
            raise ErroValidacao("publication_evidence", "PublicationEvidence obrigatoria")
        object.__setattr__(self, "source_family", _enum(SourceFamily, self.source_family, "source_family"))
        object.__setattr__(self, "themes", _id_tuple(self.themes, "themes", nao_vazio=True))
        object.__setattr__(
            self,
            "geographic_scope",
            _enum(GeographicScope, self.geographic_scope, "geographic_scope"),
        )
        for nome in ("author", "upstream_agency"):
            _exigir_texto(getattr(self, nome), nome, opcional=True)
        _exigir_bool(self.excerpt_truncated, "excerpt_truncated")
        if self.source_excerpt is not None:
            _exigir_texto(self.source_excerpt, "source_excerpt")
            if _normalizar_texto(self.source_excerpt) != self.source_excerpt:
                raise ErroValidacao(
                    "source_excerpt", "trecho nao normalizado; use normalizar_trecho()"
                )
        elif self.excerpt_truncated:
            raise ErroValidacao("excerpt_truncated", "True exige source_excerpt")
        _exigir_aware(self.published_at, "published_at", opcional=True)
        _exigir_aware(self.updated_at, "updated_at", opcional=True)
        if self.published_date is not None and (
            isinstance(self.published_date, datetime) or not isinstance(self.published_date, date)
        ):
            raise ErroValidacao("published_date", "data civil (date) esperada")
        if self.publication_timezone is not None:
            validar_fuso_iana(self.publication_timezone, "publication_timezone")
        self._validar_coerencia_temporal()

    def _validar_coerencia_temporal(self) -> None:
        p = self.publication_precision
        ev = self.publication_evidence.evidence_type
        if p is PublicationPrecision.DATETIME:
            if self.published_at is None:
                raise ErroValidacao("published_at", "obrigatorio para precisao 'datetime'")
            if self.published_date is not None:
                if self.publication_timezone is None:
                    raise ErroValidacao(
                        "published_date",
                        "com precisao 'datetime', informar publication_timezone para conferir a data",
                    )
                local = self.published_at.astimezone(ZoneInfo(self.publication_timezone)).date()
                if local != self.published_date:
                    raise ErroValidacao(
                        "published_date",
                        f"incoerente com published_at no fuso {self.publication_timezone} ({local})",
                    )
        elif p is PublicationPrecision.DATE:
            if self.published_at is not None:
                raise ErroValidacao(
                    "published_at", "deve ser nulo com precisao 'date' (sem meia-noite ficticia)"
                )
            if self.published_date is None:
                raise ErroValidacao("published_date", "obrigatorio para precisao 'date'")
        else:  # UNKNOWN
            if self.published_at is not None or self.published_date is not None:
                raise ErroValidacao(
                    "publication_precision", "'unknown' exige published_at e published_date nulos"
                )
        if p is not PublicationPrecision.UNKNOWN and ev is EvidenceType.UNKNOWN:
            raise ErroValidacao(
                "publication_evidence", "data de publicacao conhecida exige evidencia identificada"
            )
        if self.updated_at is not None and self.published_at is not None:
            utc = ZoneInfo("UTC")
            if self.updated_at.astimezone(utc) < self.published_at.astimezone(utc):
                raise ErroValidacao("updated_at", "anterior a published_at (metadados contraditorios)")

    @classmethod
    def criar(cls, **kwargs: Any) -> "Article":
        """Cria o Article calculando ``article_id`` de forma deterministica."""
        if "article_id" in kwargs:
            raise ErroValidacao("article_id", "e calculado automaticamente por criar()")
        kwargs["article_id"] = gerar_article_id(kwargs.get("source_id"), kwargs.get("original_url"))  # type: ignore[arg-type]
        return cls(**kwargs)

    @classmethod
    def criar_a_partir_da_fonte(
        cls, *, trecho_bruto: str | None, limite_trecho: int, **kwargs: Any
    ) -> "Article":
        """Caminho explicito para descricoes recebidas da fonte.

        Normaliza ``trecho_bruto`` com o limite **ja validado da configuracao**
        e cria o Article. Uma descricao extensa e truncada e sinalizada, nunca
        causa a perda do artigo. O truncamento nao afeta ID, titulo, URL,
        autor, datas, evidencia ou elegibilidade temporal.
        """
        for proibido in ("source_excerpt", "excerpt_truncated"):
            if proibido in kwargs:
                raise ErroValidacao(proibido, "e definido pela normalizacao do trecho bruto")
        trecho, truncado = normalizar_trecho(trecho_bruto, limite_trecho)
        return cls.criar(source_excerpt=trecho, excerpt_truncated=truncado, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, dados: Mapping[str, Any]) -> "Article":
        _checar_chaves(cls, dados)
        d = dict(dados)
        for nome in ("retrieved_at", "published_at", "updated_at"):
            d[nome] = _parse_dt(d.get(nome), nome)
        d["published_date"] = _parse_date(d.get("published_date"), "published_date")
        ev = d.get("publication_evidence")
        if isinstance(ev, Mapping):
            d["publication_evidence"] = PublicationEvidence.from_dict(ev)
        try:
            return cls(**d)
        except TypeError as exc:
            raise ErroValidacao(cls.__name__, f"campos obrigatorios ausentes ({exc})") from None


# --------------------------------------------------------------------------
# Janela e decisao temporal
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationWindow:
    """Janela semiaberta: ``start <= instante < end_exclusive``.

    O limite exclusivo e 08:00:00 do dia de execucao; para o usuario, a janela
    e descrita como encerrada as 07h59. Comparacoes sao feitas em UTC.
    """

    execution_date: date
    timezone: str
    start: datetime
    end_exclusive: datetime

    def __post_init__(self) -> None:
        if isinstance(self.execution_date, datetime) or not isinstance(self.execution_date, date):
            raise ErroValidacao("execution_date", "data civil (date) esperada")
        validar_fuso_iana(self.timezone, "timezone")
        _exigir_aware(self.start, "start")
        _exigir_aware(self.end_exclusive, "end_exclusive")
        if not self.start.astimezone(ZoneInfo("UTC")) < self.end_exclusive.astimezone(ZoneInfo("UTC")):
            raise ErroValidacao("start", "deve ser anterior a end_exclusive")

    @property
    def duracao(self) -> timedelta:
        """Duracao real decorrida (diferenca em UTC)."""
        return self.end_exclusive.astimezone(ZoneInfo("UTC")) - self.start.astimezone(ZoneInfo("UTC"))

    def contem(self, instante: datetime) -> bool:
        _exigir_aware(instante, "instante")
        utc = ZoneInfo("UTC")
        i = instante.astimezone(utc)
        return self.start.astimezone(utc) <= i < self.end_exclusive.astimezone(utc)

    def descrever(self) -> str:
        tz = ZoneInfo(self.timezone)
        ini = self.start.astimezone(tz)
        fim = (self.end_exclusive - timedelta(minutes=1)).astimezone(tz)
        return f"{ini:%d/%m/%Y %Hh%M} a {fim:%d/%m/%Y %Hh%M} ({self.timezone})"

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, dados: Mapping[str, Any]) -> "PublicationWindow":
        _checar_chaves(cls, dados)
        d = dict(dados)
        d["execution_date"] = _parse_date(d.get("execution_date"), "execution_date")
        for nome in ("start", "end_exclusive"):
            d[nome] = _parse_dt(d.get(nome), nome)
        try:
            return cls(**d)
        except TypeError as exc:
            raise ErroValidacao(cls.__name__, f"campos obrigatorios ausentes ({exc})") from None


@dataclass(frozen=True)
class TemporalDecision:
    """Decisao temporal. ``eligible`` e explicito e coerente com ``status``.

    Elegibilidade temporal NAO e aprovacao editorial nem confirmacao
    jornalistica independente.
    """

    status: TemporalStatus
    reason_code: ReasonCode
    description: str
    window: PublicationWindow
    eligible: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(TemporalStatus, self.status, "status"))
        object.__setattr__(self, "reason_code", _enum(ReasonCode, self.reason_code, "reason_code"))
        _exigir_texto(self.description, "description")
        if not isinstance(self.window, PublicationWindow):
            raise ErroValidacao("window", "PublicationWindow obrigatoria")
        _exigir_bool(self.eligible, "eligible")
        if self.eligible != (self.status in ELIGIBLE_STATUSES):
            raise ErroValidacao("eligible", f"incoerente com status {self.status.value}")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


# --------------------------------------------------------------------------
# Resultado de coleta (somente contrato; coletores na Fase 1B)
# --------------------------------------------------------------------------

_STATUS_SEM_EXECUCAO = {CollectionStatus.SOURCE_NOT_CONFIGURED, CollectionStatus.SOURCE_NOT_QUERIED}
_STATUS_COM_ERRO = {
    CollectionStatus.TECHNICAL_FAILURE,
    CollectionStatus.ACCESS_RESTRICTED,
    CollectionStatus.PARTIAL,
}


@dataclass(frozen=True)
class CollectionResult:
    """Resultado da coleta de uma fonte para uma edicao.

    Ver :attr:`coleta_vazia_confirmada`: so ``SUCCESS_EMPTY`` indica que esta
    operacao de coleta terminou corretamente com zero itens. Falha tecnica,
    acesso restrito, coleta parcial ou fonte nao consultada nunca equivalem a
    coleta vazia. Nenhum estado deste contrato representa ausencia de noticias
    em um tema -- essa conclusao pertence a auditoria de cobertura (Fase 2).
    """

    source_id: str
    execution_date: date
    status: CollectionStatus
    articles_found: int
    articles: tuple[Article, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _exigir_id(self.source_id, "source_id")
        if isinstance(self.execution_date, datetime) or not isinstance(self.execution_date, date):
            raise ErroValidacao("execution_date", "data civil (date) esperada")
        object.__setattr__(self, "status", _enum(CollectionStatus, self.status, "status"))
        if not isinstance(self.articles, (list, tuple)) or not all(
            isinstance(a, Article) for a in self.articles
        ):
            raise ErroValidacao("articles", "lista de Article esperada")
        object.__setattr__(self, "articles", tuple(self.articles))
        if (
            not isinstance(self.articles_found, int)
            or isinstance(self.articles_found, bool)
            or self.articles_found < 0
        ):
            raise ErroValidacao("articles_found", "inteiro nao negativo obrigatorio")
        if self.articles_found != len(self.articles):
            raise ErroValidacao("articles_found", "difere da quantidade de artigos informados")
        outras = sorted({a.source_id for a in self.articles} - {self.source_id})
        if outras:
            raise ErroValidacao("articles", f"artigos de outras fontes: {outras}")
        _exigir_aware(self.started_at, "started_at", opcional=True)
        _exigir_aware(self.finished_at, "finished_at", opcional=True)
        _exigir_texto(self.error_code, "error_code", opcional=True)
        _exigir_texto(self.error_message, "error_message", opcional=True)

        s = self.status
        if s in _STATUS_SEM_EXECUCAO:
            if self.started_at is not None or self.finished_at is not None:
                raise ErroValidacao("started_at", f"status {s.value} nao admite horarios de execucao")
            if self.articles:
                raise ErroValidacao("articles", f"status {s.value} nao admite artigos")
        else:
            if self.started_at is None or self.finished_at is None:
                raise ErroValidacao("started_at", f"status {s.value} exige started_at e finished_at")
            utc = ZoneInfo("UTC")
            if self.finished_at.astimezone(utc) < self.started_at.astimezone(utc):
                raise ErroValidacao("finished_at", "anterior a started_at")
        if s is CollectionStatus.SUCCESS_WITH_RESULTS and not self.articles:
            raise ErroValidacao("articles", "sucesso com resultados exige ao menos um artigo")
        if s is CollectionStatus.SUCCESS_EMPTY and self.articles:
            raise ErroValidacao("articles", "sucesso vazio nao admite artigos")
        if s in {CollectionStatus.TECHNICAL_FAILURE, CollectionStatus.ACCESS_RESTRICTED} and self.articles:
            raise ErroValidacao("articles", f"status {s.value} nao admite artigos")
        if s in _STATUS_COM_ERRO:
            if self.error_code is None:
                raise ErroValidacao("error_code", f"status {s.value} exige error_code")
        elif self.error_code is not None or self.error_message is not None:
            raise ErroValidacao("error_code", f"status {s.value} nao admite erro")

    @property
    def coleta_vazia_confirmada(self) -> bool:
        """True somente se ESTA operacao de coleta terminou corretamente com 0 itens.

        Nao significa: ausencia de noticias em um tema; ausencia de publicacoes
        no periodo; ausencia em todos os canais da fonte; cobertura suficiente;
        nem confirmacao editorial. A ausencia tematica sera concluida apenas
        pela auditoria de cobertura (Fase 2), a partir de varias fontes.
        """
        return self.status is CollectionStatus.SUCCESS_EMPTY

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, dados: Mapping[str, Any]) -> "CollectionResult":
        _checar_chaves(cls, dados)
        d = dict(dados)
        d["execution_date"] = _parse_date(d.get("execution_date"), "execution_date")
        for nome in ("started_at", "finished_at"):
            d[nome] = _parse_dt(d.get(nome), nome)
        d["articles"] = tuple(
            a if isinstance(a, Article) else Article.from_dict(a) for a in d.get("articles", ())
        )
        try:
            return cls(**d)
        except TypeError as exc:
            raise ErroValidacao(cls.__name__, f"campos obrigatorios ausentes ({exc})") from None
