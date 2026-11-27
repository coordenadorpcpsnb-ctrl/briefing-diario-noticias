"""Carregamento seguro e validacao deterministica de ``config/*.yaml``.

- YAML lido com ``SafeLoader`` (sem construcao de objetos arbitrarios) e com
  rejeicao de chaves duplicadas.
- Nenhuma configuracao invalida e ignorada silenciosamente: toda falha gera
  :class:`ErroConfiguracao` indicando arquivo, campo e motivo.
- Validacao de URL e **exclusivamente sintatica**; nao prova que a URL existe.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Mapping

import yaml

from .janela import DIAS_SEMANA, InicioJanela, RegrasJanela
from .models import (
    ErroValidacao,
    GeographicScope,
    SourceConfig,
    SourceFamily,
    ThemeConfig,
    validar_limite_trecho,
)

__all__ = [
    "ErroConfiguracao",
    "Configuracao",
    "RegraDeclarada",
    "StatusRegra",
    "REGRAS_IMPLEMENTADAS",
    "TEMAS_OBRIGATORIOS",
    "TEMAS_SOMENTE_NACIONAIS",
    "FONTES_FLORESTAIS_OBRIGATORIAS",
    "ENV_CONFIG_DIR",
    "ARQUIVOS_CONFIG",
    "diretorio_config_padrao",
    "resolver_diretorio_config",
    "carregar_yaml",
    "carregar_temas",
    "carregar_fontes",
    "carregar_regras",
    "carregar_configuracao",
]

# Invariantes de negocio verificadas contra o YAML (guardas, nao valores).
TEMAS_OBRIGATORIOS = frozenset(
    {
        "politica",
        "economia",
        "mercados_financeiros",
        "inteligencia_artificial",
        "inovacoes_florestais",
        "mercado_aco",
        "setor_florestal",
    }
)
TEMAS_SOMENTE_NACIONAIS = frozenset({"mercado_aco", "setor_florestal"})
FONTES_FLORESTAIS_OBRIGATORIAS = frozenset(
    {"mais_floresta", "malinovski", "imaflora", "embrapa_florestas", "forests_news"}
)

# Regras com status "implementada" no YAML precisam constar aqui, e vice-versa.
# Garante que o YAML nao declare como operacional algo que o codigo nao faz.
REGRAS_IMPLEMENTADAS = frozenset(
    {
        "fuso_referencia",
        "dias_execucao",
        "horario_corte",
        "janela_segunda",
        "janela_terca_a_sexta",
        "politica_feriados",
        "tratamento_datas_incompletas",
        "carregamento_yaml_seguro",
        "validacao_sintatica_url",
        "trecho_fonte_limitado",
    }
)

StatusRegra = ("implementada", "declarada_fase_futura", "politica_proposta", "decisao_pendente")
_CATEGORIAS = ("operacional", "editorial", "publicacao", "seguranca")
_FASES = ("1B", "1C", "1D", "2", "3", "4", "5", "6", "7", "8", "a_definir")
_HORA_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


class ErroConfiguracao(ValueError):
    """Erro de configuracao com arquivo, campo e motivo."""

    def __init__(self, arquivo: str | Path, campo: str, motivo: str) -> None:
        self.arquivo = str(arquivo)
        self.campo = campo
        self.motivo = motivo
        super().__init__(f"{self.arquivo}: {campo}: {motivo}")


# --------------------------------------------------------------------------
# YAML seguro
# --------------------------------------------------------------------------


class _LoaderSeguro(yaml.SafeLoader):
    """SafeLoader que rejeita chaves duplicadas e chaves nao textuais."""


def _construir_mapa(loader: _LoaderSeguro, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    resultado: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        chave = loader.construct_object(key_node, deep=deep)
        if not isinstance(chave, str):
            raise yaml.constructor.ConstructorError(
                None, None, f"chave nao textual {chave!r}", key_node.start_mark
            )
        if chave in resultado:
            raise yaml.constructor.ConstructorError(
                None, None, f"chave duplicada {chave!r}", key_node.start_mark
            )
        resultado[chave] = loader.construct_object(value_node, deep=deep)
    return resultado


_LoaderSeguro.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construir_mapa)


def carregar_yaml(caminho: str | Path) -> Any:
    """Le YAML com SafeLoader estrito. Tags inseguras sao rejeitadas."""
    caminho = Path(caminho)
    try:
        texto = caminho.read_text(encoding="utf-8")
    except OSError as exc:
        raise ErroConfiguracao(caminho, "<arquivo>", f"nao foi possivel ler ({exc.strerror})") from None
    try:
        return yaml.load(texto, Loader=_LoaderSeguro)  # noqa: S506 - loader derivado de SafeLoader
    except yaml.YAMLError as exc:
        raise ErroConfiguracao(caminho, "<yaml>", f"YAML invalido ou inseguro: {exc}") from None


ENV_CONFIG_DIR = "BRIEFING_CONFIG_DIR"
ARQUIVOS_CONFIG = ("temas.yaml", "fontes.yaml", "regras.yaml")
_NOME_PROJETO = 'name = "briefing-diario-noticias"'


def diretorio_config_padrao() -> Path | None:
    """``config/`` do checkout do repositorio, somente se inequivocamente identificado.

    Exige o layout ``<raiz>/src/briefing/`` com ``<raiz>/pyproject.toml`` deste
    projeto. Nao depende do diretorio de trabalho atual e nao procura em
    diretorios ancestrais arbitrarios. Em instalacao convencional
    (``site-packages``) retorna ``None``.
    """
    pacote = Path(__file__).resolve().parent
    raiz = pacote.parent.parent
    if pacote.name != "briefing" or pacote.parent.name != "src":
        return None
    pyproject = raiz / "pyproject.toml"
    try:
        if not pyproject.is_file() or _NOME_PROJETO not in pyproject.read_text(encoding="utf-8"):
            return None
    except OSError:
        return None
    return raiz / "config"


def _validar_diretorio(caminho: Path, origem: str) -> Path:
    if not caminho.exists():
        raise ErroConfiguracao(caminho, origem, "diretorio de configuracao inexistente")
    if not caminho.is_dir():
        raise ErroConfiguracao(caminho, origem, "o caminho nao e um diretorio")
    for nome in ARQUIVOS_CONFIG:
        arq = caminho / nome
        if not arq.exists():
            raise ErroConfiguracao(caminho, origem, f"arquivo obrigatorio ausente: {nome}")
        if not arq.is_file():
            raise ErroConfiguracao(caminho, origem, f"{nome} nao e um arquivo regular")
    return caminho


def resolver_diretorio_config(
    diretorio: str | Path | None = None, *, ambiente: Mapping[str, str] | None = None
) -> tuple[Path, str]:
    """Resolve o diretorio de configuracao. Retorna ``(caminho, origem)``.

    Precedencia (sem fallback silencioso):

    1. ``diretorio`` informado pelo chamador -- se invalido, erro.
    2. Variavel de ambiente ``BRIEFING_CONFIG_DIR`` -- se definida e invalida
       (inclusive vazia), erro.
    3. ``config/`` do checkout do repositorio, se inequivocamente identificado.
    4. Erro explicito.
    """
    env = os.environ if ambiente is None else ambiente
    if diretorio is not None:
        origem = "argumento explicito"
        return _validar_diretorio(Path(diretorio), origem), origem
    if ENV_CONFIG_DIR in env:
        origem = f"variavel de ambiente {ENV_CONFIG_DIR}"
        valor = env[ENV_CONFIG_DIR]
        if not valor.strip():
            raise ErroConfiguracao("<vazio>", origem, "variavel definida, mas vazia")
        return _validar_diretorio(Path(valor), origem), origem
    padrao = diretorio_config_padrao()
    if padrao is not None:
        origem = "checkout do repositorio"
        return _validar_diretorio(padrao, origem), origem
    raise ErroConfiguracao(
        "<nenhum>",
        "diretorio de configuracao",
        f"nenhuma configuracao disponivel: informe o diretorio explicitamente ou "
        f"defina {ENV_CONFIG_DIR} (instalacao fora do checkout do repositorio)",
    )


# --------------------------------------------------------------------------
# Auxiliares
# --------------------------------------------------------------------------


def _mapa(valor: Any, arquivo: Path, campo: str) -> Mapping[str, Any]:
    if not isinstance(valor, Mapping):
        raise ErroConfiguracao(arquivo, campo, "mapeamento esperado")
    return valor


def _lista(valor: Any, arquivo: Path, campo: str) -> list:
    if not isinstance(valor, list):
        raise ErroConfiguracao(arquivo, campo, "lista esperada")
    return valor


def _chaves(dados: Mapping[str, Any], arquivo: Path, campo: str, obrig: set[str], opc: set[str] = frozenset()) -> None:  # type: ignore[assignment]
    faltando = sorted(obrig - set(dados))
    if faltando:
        raise ErroConfiguracao(arquivo, campo, f"campos obrigatorios ausentes: {faltando}")
    extras = sorted(set(dados) - obrig - set(opc))
    if extras:
        raise ErroConfiguracao(arquivo, campo, f"campos desconhecidos: {extras}")


def _hora(valor: Any, arquivo: Path, campo: str) -> time:
    if not isinstance(valor, str) or not _HORA_RE.match(valor):
        raise ErroConfiguracao(arquivo, campo, "horario 'HH:MM:SS' entre aspas esperado")
    try:
        return time.fromisoformat(valor)
    except ValueError:
        raise ErroConfiguracao(arquivo, campo, f"horario invalido {valor!r}") from None


def _construir(cls: Any, dados: Mapping[str, Any], arquivo: Path, prefixo: str) -> Any:
    try:
        return cls.from_dict(dados)
    except ErroValidacao as exc:
        raise ErroConfiguracao(arquivo, f"{prefixo}.{exc.campo}", exc.motivo) from None


# --------------------------------------------------------------------------
# Temas
# --------------------------------------------------------------------------


def carregar_temas(caminho: str | Path) -> dict[str, ThemeConfig]:
    arquivo = Path(caminho)
    raiz = _mapa(carregar_yaml(arquivo), arquivo, "<raiz>")
    _chaves(raiz, arquivo, "<raiz>", {"versao", "temas"})
    itens = _lista(raiz["temas"], arquivo, "temas")
    temas: dict[str, ThemeConfig] = {}
    for i, item in enumerate(itens):
        tema = _construir(ThemeConfig, _mapa(item, arquivo, f"temas[{i}]"), arquivo, f"temas[{i}]")
        if tema.theme_id in temas:
            raise ErroConfiguracao(arquivo, f"temas[{i}].theme_id", f"ID duplicado {tema.theme_id!r}")
        temas[tema.theme_id] = tema
    faltando = sorted(TEMAS_OBRIGATORIOS - set(temas))
    if faltando:
        raise ErroConfiguracao(arquivo, "temas", f"temas obrigatorios ausentes: {faltando}")
    for tid in TEMAS_SOMENTE_NACIONAIS:
        if temas[tid].geographic_scopes != (GeographicScope.NACIONAL,):
            raise ErroConfiguracao(
                arquivo, f"temas.{tid}.geographic_scopes", "tema exclusivamente brasileiro: somente 'nacional'"
            )
    return temas


# --------------------------------------------------------------------------
# Fontes
# --------------------------------------------------------------------------


def carregar_fontes(caminho: str | Path, temas: Mapping[str, ThemeConfig]) -> dict[str, SourceConfig]:
    arquivo = Path(caminho)
    raiz = _mapa(carregar_yaml(arquivo), arquivo, "<raiz>")
    _chaves(raiz, arquivo, "<raiz>", {"versao", "fontes", "categorias_complementares_previstas"})
    fontes: dict[str, SourceConfig] = {}
    for i, item in enumerate(_lista(raiz["fontes"], arquivo, "fontes")):
        prefixo = f"fontes[{i}]"
        fonte = _construir(SourceConfig, _mapa(item, arquivo, prefixo), arquivo, prefixo)
        prefixo = f"fontes.{fonte.source_id}"
        if fonte.source_id in fontes:
            raise ErroConfiguracao(arquivo, f"{prefixo}.source_id", "ID duplicado")
        for tid in fonte.themes:
            if tid not in temas:
                raise ErroConfiguracao(arquivo, f"{prefixo}.themes", f"tema inexistente {tid!r}")
            if fonte.family not in temas[tid].source_families:
                raise ErroConfiguracao(
                    arquivo,
                    f"{prefixo}.family",
                    f"familia {fonte.family.value!r} nao aplicavel ao tema {tid!r}",
                )
        fontes[fonte.source_id] = fonte

    for sid in sorted(FONTES_FLORESTAIS_OBRIGATORIAS):
        if sid not in fontes:
            raise ErroConfiguracao(arquivo, "fontes", f"fonte florestal obrigatoria ausente: {sid!r}")
        f = fontes[sid]
        if not f.mandatory or not {"inovacoes_florestais", "setor_florestal"} & set(f.mandatory_themes):
            raise ErroConfiguracao(
                arquivo, f"fontes.{sid}.mandatory", "fonte florestal deve ser obrigatoria em tema florestal"
            )

    familias_validas = {f.value for f in SourceFamily}
    for i, cat in enumerate(_lista(raiz["categorias_complementares_previstas"], arquivo, "categorias_complementares_previstas")):
        campo = f"categorias_complementares_previstas[{i}]"
        cat = _mapa(cat, arquivo, campo)
        _chaves(cat, arquivo, campo, {"categoria", "family", "status", "descricao"})
        if cat["family"] not in familias_validas:
            raise ErroConfiguracao(arquivo, f"{campo}.family", f"familia invalida {cat['family']!r}")
        if cat["status"] != "pendente_definicao":
            raise ErroConfiguracao(arquivo, f"{campo}.status", "somente 'pendente_definicao' nesta fase")
    return fontes


# --------------------------------------------------------------------------
# Regras
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegraDeclarada:
    regra_id: str
    categoria: str
    status: str
    descricao: str
    fase_prevista: str | None
    valor: Any = None


@dataclass(frozen=True)
class DecisaoRegistrada:
    data: date
    descricao: str
    regra_anterior: str
    regra_aprovada: str
    justificativa: str


@dataclass(frozen=True)
class ConfigRegras:
    janela: RegrasJanela
    apresentacao_fim: str
    decisoes: tuple[DecisaoRegistrada, ...]
    regras: Mapping[str, RegraDeclarada]
    source_excerpt_max_chars: int

    def com_status(self, status: str) -> list[RegraDeclarada]:
        return [r for r in self.regras.values() if r.status == status]


def _carregar_janela(dados: Any, arquivo: Path) -> tuple[RegrasJanela, str, tuple[DecisaoRegistrada, ...]]:
    campo = "janela_temporal"
    j = _mapa(dados, arquivo, campo)
    _chaves(j, arquivo, campo, {"fuso", "fim_exclusivo", "apresentacao_fim", "inicio_por_dia", "historico_decisoes"})
    inicio_cfg = _mapa(j["inicio_por_dia"], arquivo, f"{campo}.inicio_por_dia")
    inicio: dict[int, InicioJanela] = {}
    for nome, cfg in inicio_cfg.items():
        c = f"{campo}.inicio_por_dia.{nome}"
        if nome not in DIAS_SEMANA:
            raise ErroConfiguracao(arquivo, c, "dia da semana desconhecido")
        cfg = _mapa(cfg, arquivo, c)
        _chaves(cfg, arquivo, c, {"dias_antes", "horario"})
        dias = cfg["dias_antes"]
        if isinstance(dias, bool) or not isinstance(dias, int) or dias < 0:
            raise ErroConfiguracao(arquivo, f"{c}.dias_antes", "inteiro >= 0 esperado")
        inicio[DIAS_SEMANA[nome]] = InicioJanela(dias, _hora(cfg["horario"], arquivo, f"{c}.horario"))
    if any(d >= 5 for d in inicio):
        raise ErroConfiguracao(arquivo, f"{campo}.inicio_por_dia", "sabado e domingo nao sao dias de execucao")
    try:
        regras = RegrasJanela(
            fuso=j["fuso"],
            fim_exclusivo=_hora(j["fim_exclusivo"], arquivo, f"{campo}.fim_exclusivo"),
            inicio_por_dia=inicio,
        )
    except ErroValidacao as exc:
        raise ErroConfiguracao(arquivo, f"{campo}.{exc.campo}", exc.motivo) from None
    if not isinstance(j["apresentacao_fim"], str) or not j["apresentacao_fim"].strip():
        raise ErroConfiguracao(arquivo, f"{campo}.apresentacao_fim", "texto esperado")

    decisoes = []
    for i, d in enumerate(_lista(j["historico_decisoes"], arquivo, f"{campo}.historico_decisoes")):
        c = f"{campo}.historico_decisoes[{i}]"
        d = _mapa(d, arquivo, c)
        chaves = {"data", "descricao", "regra_anterior", "regra_aprovada", "justificativa"}
        _chaves(d, arquivo, c, chaves)
        if not isinstance(d["data"], str):
            raise ErroConfiguracao(arquivo, f"{c}.data", "data 'AAAA-MM-DD' entre aspas esperada")
        try:
            dt = date.fromisoformat(d["data"])
        except ValueError:
            raise ErroConfiguracao(arquivo, f"{c}.data", "data invalida") from None
        for k in chaves - {"data"}:
            if not isinstance(d[k], str) or not d[k].strip():
                raise ErroConfiguracao(arquivo, f"{c}.{k}", "texto esperado")
        decisoes.append(DecisaoRegistrada(dt, d["descricao"], d["regra_anterior"], d["regra_aprovada"], d["justificativa"]))
    return regras, j["apresentacao_fim"], tuple(decisoes)


def carregar_regras(caminho: str | Path) -> ConfigRegras:
    arquivo = Path(caminho)
    raiz = _mapa(carregar_yaml(arquivo), arquivo, "<raiz>")
    _chaves(raiz, arquivo, "<raiz>", {"versao", "janela_temporal", "limites", "regras"})
    janela, apresentacao, decisoes = _carregar_janela(raiz["janela_temporal"], arquivo)
    limites = _mapa(raiz["limites"], arquivo, "limites")
    _chaves(limites, arquivo, "limites", {"source_excerpt_max_chars"})
    try:
        limite_trecho = validar_limite_trecho(limites["source_excerpt_max_chars"])
    except ErroValidacao as exc:
        raise ErroConfiguracao(arquivo, f"limites.{exc.campo}", exc.motivo) from None

    regras: dict[str, RegraDeclarada] = {}
    for i, item in enumerate(_lista(raiz["regras"], arquivo, "regras")):
        c = f"regras[{i}]"
        item = _mapa(item, arquivo, c)
        _chaves(item, arquivo, c, {"id", "categoria", "status", "descricao"}, {"fase_prevista", "valor"})
        rid = item["id"]
        if not isinstance(rid, str) or not re.match(r"^[a-z][a-z0-9_]{1,63}$", rid):
            raise ErroConfiguracao(arquivo, f"{c}.id", f"identificador invalido {rid!r}")
        c = f"regras.{rid}"
        if rid in regras:
            raise ErroConfiguracao(arquivo, f"{c}.id", "ID duplicado")
        if item["categoria"] not in _CATEGORIAS:
            raise ErroConfiguracao(arquivo, f"{c}.categoria", f"valor desconhecido; validos: {list(_CATEGORIAS)}")
        if item["status"] not in StatusRegra:
            raise ErroConfiguracao(arquivo, f"{c}.status", f"valor desconhecido; validos: {list(StatusRegra)}")
        if not isinstance(item["descricao"], str) or not item["descricao"].strip():
            raise ErroConfiguracao(arquivo, f"{c}.descricao", "texto esperado")
        fase = item.get("fase_prevista")
        if item["status"] == "implementada":
            if fase is not None:
                raise ErroConfiguracao(arquivo, f"{c}.fase_prevista", "regra implementada nao tem fase prevista")
        elif fase not in _FASES:
            raise ErroConfiguracao(arquivo, f"{c}.fase_prevista", f"obrigatoria; validos: {list(_FASES)}")
        valor = item.get("valor")
        if isinstance(valor, int) and not isinstance(valor, bool) and rid.startswith("horario"):
            raise ErroConfiguracao(arquivo, f"{c}.valor", "horario deve ser texto entre aspas")
        if isinstance(valor, str) and rid in {"horario_execucao_proposto", "meta_publicacao"}:
            if not _HHMM_RE.match(valor):
                raise ErroConfiguracao(arquivo, f"{c}.valor", "horario 'HH:MM' esperado")
        regras[rid] = RegraDeclarada(rid, item["categoria"], item["status"], item["descricao"], fase, valor)

    implementadas = {r.regra_id for r in regras.values() if r.status == "implementada"}
    a_mais = sorted(implementadas - REGRAS_IMPLEMENTADAS)
    if a_mais:
        raise ErroConfiguracao(arquivo, "regras", f"marcadas como implementadas sem implementacao: {a_mais}")
    a_menos = sorted(REGRAS_IMPLEMENTADAS - implementadas)
    if a_menos:
        raise ErroConfiguracao(arquivo, "regras", f"implementadas no codigo mas nao declaradas: {a_menos}")
    return ConfigRegras(janela, apresentacao, decisoes, regras, limite_trecho)


# --------------------------------------------------------------------------
# Conjunto
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Configuracao:
    temas: Mapping[str, ThemeConfig]
    fontes: Mapping[str, SourceConfig]
    regras: ConfigRegras
    diretorio: Path
    origem: str


def carregar_configuracao(
    diretorio: str | Path | None = None, *, ambiente: Mapping[str, str] | None = None
) -> Configuracao:
    """Carrega e valida ``temas.yaml``, ``fontes.yaml`` e ``regras.yaml``.

    O diretorio e resolvido por :func:`resolver_diretorio_config`.
    """
    base, origem = resolver_diretorio_config(diretorio, ambiente=ambiente)
    temas = carregar_temas(base / "temas.yaml")
    fontes = carregar_fontes(base / "fontes.yaml", temas)
    regras = carregar_regras(base / "regras.yaml")
    return Configuracao(temas=temas, fontes=fontes, regras=regras, diretorio=base, origem=origem)
