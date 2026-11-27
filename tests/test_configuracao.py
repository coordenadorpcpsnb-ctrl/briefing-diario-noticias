"""Grupos F (configuracao) e H (seguranca e robustez)."""

from __future__ import annotations

import re
import shutil
from datetime import date, time
from pathlib import Path

import pytest

from briefing.configuracao import (
    FONTES_FLORESTAIS_OBRIGATORIAS,
    REGRAS_IMPLEMENTADAS,
    TEMAS_OBRIGATORIOS,
    ErroConfiguracao,
    carregar_configuracao,
    carregar_yaml,
)
from briefing.janela import DIAS_SEMANA
from briefing.models import (
    AccessStatus,
    ErroValidacao,
    GeographicScope,
    RegistrationStatus,
    UrlProvenance,
    redigir_url,
    validar_url,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"

URLS_FLORESTAIS = {
    "mais_floresta": "https://maisfloresta.com.br/",
    "malinovski": "https://malinovski.com.br/noticias/",
    "imaflora": "https://imaflora.org/noticias",
    "embrapa_florestas": "https://www.embrapa.br/web/florestas/noticias",
    "forests_news": "https://www.forestsnews.org/pt-br",
}


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    destino = tmp_path / "config"
    shutil.copytree(CONFIG, destino)
    return destino


def _substituir(arquivo: Path, antigo: str, novo: str) -> None:
    texto = arquivo.read_text(encoding="utf-8")
    assert antigo in texto, f"trecho nao encontrado: {antigo!r}"
    arquivo.write_text(texto.replace(antigo, novo, 1), encoding="utf-8")


# ============================================================ Grupo F
class TestGrupoFConfiguracao:
    def test_carrega_os_tres_arquivos(self, configuracao):
        assert configuracao.temas and configuracao.fontes and configuracao.regras.regras

    def test_sete_temas(self, configuracao):
        assert set(configuracao.temas) == TEMAS_OBRIGATORIOS and len(configuracao.temas) == 7

    def test_escopos_geograficos(self, configuracao):
        for tid in ("mercado_aco", "setor_florestal"):
            assert configuracao.temas[tid].geographic_scopes == (GeographicScope.NACIONAL,)
        for tid in ("politica", "economia", "mercados_financeiros", "inteligencia_artificial", "inovacoes_florestais"):
            assert set(configuracao.temas[tid].geographic_scopes) == {
                GeographicScope.NACIONAL, GeographicScope.INTERNACIONAL
            }

    def test_meta_editorial_3_a_5(self, configuracao):
        for t in configuracao.temas.values():
            assert (t.editorial_target_min, t.editorial_target_max) == (3, 5)
            assert t.keywords_pt and t.keywords_en and t.coverage

    def test_cinco_fontes_florestais_obrigatorias(self, configuracao):
        for sid, url in URLS_FLORESTAIS.items():
            f = configuracao.fontes[sid]
            assert f.mandatory and set(f.mandatory_themes) == {"inovacoes_florestais", "setor_florestal"}
            assert f.homepage_url == url
            assert f.url_provenance is UrlProvenance.PROVIDED_BY_OWNER
            # obrigatoria != acesso confirmado
            assert f.access_status is AccessStatus.UNVERIFIED and not f.enabled
        assert set(URLS_FLORESTAIS) == FONTES_FLORESTAIS_OBRIGATORIAS

    def test_fontes_prioritarias_cadastradas(self, configuracao):
        esperadas = {
            "reuters", "bloomberg", "financial_times", "wall_street_journal", "associated_press", "bbc",
            "valor_economico", "folha_de_s_paulo", "estadao", "o_globo", "g1", "infomoney", "agencia_brasil",
            "banco_central", "ibge", "ministerio_fazenda", "ministerio_mdic", "ministerio_mma",
            "instituto_aco_brasil", "iba",
        }
        assert esperadas <= set(configuracao.fontes)

    def test_ids_unicos_e_referencias_validas(self, configuracao):
        for f in configuracao.fontes.values():
            assert set(f.themes) <= set(configuracao.temas)
            for tid in f.themes:
                assert f.family in configuracao.temas[tid].source_families

    def test_nenhum_endpoint_nao_verificado_apresentado_como_confirmado(self, configuracao):
        for f in configuracao.fontes.values():
            assert f.rss_url is None, f"{f.source_id}: rss_url nao verificada"
            assert f.collection_methods == ()
            assert f.access_status is AccessStatus.UNVERIFIED
            assert not f.enabled
            assert f.ownership_group is None and f.upstream_agency is None and f.timezone is None

    def test_fontes_pendentes_nao_habilitadas(self, configuracao):
        pendentes = [f for f in configuracao.fontes.values() if f.registration_status is RegistrationStatus.PENDING]
        assert pendentes, "ministerios devem estar pendentes (URL nao verificada)"
        for f in pendentes:
            assert not f.enabled and f.homepage_url is None

    def test_regras_implementadas_coerentes(self, configuracao):
        impl = {r.regra_id for r in configuracao.regras.com_status("implementada")}
        assert impl == REGRAS_IMPLEMENTADAS
        for r in configuracao.regras.regras.values():
            if r.status != "implementada":
                assert r.fase_prevista is not None

    def test_politicas_propostas_nao_operacionais(self, configuracao):
        r = configuracao.regras.regras
        assert r["horario_execucao_proposto"].status == "politica_proposta" and r["horario_execucao_proposto"].valor == "08:03"
        assert r["meta_publicacao"].status == "politica_proposta" and r["meta_publicacao"].valor == "08:30"

    def test_regra_temporal_vem_do_yaml(self, configuracao):
        j = configuracao.regras.janela
        assert j.fuso == "America/Sao_Paulo" and j.fim_exclusivo == time(8)
        assert j.dias_execucao == frozenset(range(5))
        seg = j.inicio_por_dia[DIAS_SEMANA["segunda"]]
        assert (seg.dias_antes, seg.horario) == (3, time(8))
        assert configuracao.regras.apresentacao_fim == "07h59"

    def test_decisao_registrada(self, configuracao):
        (d,) = configuracao.regras.decisoes
        assert d.data == date(2026, 11, 27)
        assert "18:00" in d.regra_anterior and "08:00" in d.regra_aprovada

    # ---- rejeicoes
    def test_id_duplicado(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "source_id: bloomberg", "source_id: reuters")
        with pytest.raises(ErroConfiguracao, match="ID duplicado"):
            carregar_configuracao(cfg_dir)

    def test_tema_inexistente(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "themes: [mercado_aco]", "themes: [siderurgia]")
        with pytest.raises(ErroConfiguracao, match="tema inexistente"):
            carregar_configuracao(cfg_dir)

    def test_familia_invalida(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "family: industry_association", "family: blog")
        with pytest.raises(ErroConfiguracao, match="valor desconhecido"):
            carregar_configuracao(cfg_dir)

    def test_familia_nao_aplicavel_ao_tema(self, cfg_dir):
        _substituir(cfg_dir / "temas.yaml",
                    "source_families: [specialized_press, scientific_institution, civil_society, industry_association, corporate_primary]",
                    "source_families: [specialized_press, scientific_institution, industry_association, corporate_primary]")
        with pytest.raises(ErroConfiguracao, match="nao aplicavel"):
            carregar_configuracao(cfg_dir)

    def test_escopo_incompativel(self, cfg_dir):
        texto = (cfg_dir / "temas.yaml").read_text(encoding="utf-8")
        i = texto.index("theme_id: mercado_aco")
        texto = texto[:i] + texto[i:].replace("geographic_scopes: [nacional]", "geographic_scopes: [nacional, internacional]", 1)
        (cfg_dir / "temas.yaml").write_text(texto, encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="exclusivamente brasileiro"):
            carregar_configuracao(cfg_dir)

    def test_escopo_desconhecido(self, cfg_dir):
        _substituir(cfg_dir / "temas.yaml", "geographic_scopes: [nacional]", "geographic_scopes: [regional]")
        with pytest.raises(ErroConfiguracao, match="geographic_scopes"):
            carregar_configuracao(cfg_dir)

    def test_campo_obrigatorio_ausente(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "    terms_status: unverified\n", "")
        with pytest.raises(ErroConfiguracao, match="campos obrigatorios ausentes"):
            carregar_configuracao(cfg_dir)

    def test_tema_obrigatorio_ausente(self, cfg_dir):
        _substituir(cfg_dir / "temas.yaml", "theme_id: politica", "theme_id: politica_geral")
        with pytest.raises(ErroConfiguracao):
            carregar_configuracao(cfg_dir)

    def test_url_malformada(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", '"https://www.reuters.com/"', '"https//reuters"')
        with pytest.raises(ErroConfiguracao, match="homepage_url"):
            carregar_configuracao(cfg_dir)

    def test_tipo_incorreto(self, cfg_dir):
        _substituir(cfg_dir / "temas.yaml", "editorial_target_min: 3", 'editorial_target_min: "tres"')
        with pytest.raises(ErroConfiguracao, match="editorial_target_min"):
            carregar_configuracao(cfg_dir)

    def test_habilitada_sem_metodo(self, cfg_dir):
        texto = (cfg_dir / "fontes.yaml").read_text(encoding="utf-8")
        (cfg_dir / "fontes.yaml").write_text(texto.replace("    enabled: false", "    enabled: true", 1), encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="enabled"):
            carregar_configuracao(cfg_dir)

    def test_habilitada_sem_url(self, cfg_dir):
        texto = (cfg_dir / "fontes.yaml").read_text(encoding="utf-8")
        i = texto.index("source_id: ministerio_fazenda")
        texto = texto[:i] + texto[i:].replace("enabled: false", "enabled: true", 1)
        (cfg_dir / "fontes.yaml").write_text(texto, encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="enabled"):
            carregar_configuracao(cfg_dir)

    def test_mandatory_inconsistente(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml",
                    "mandatory: true\n    mandatory_themes: [inovacoes_florestais, setor_florestal]",
                    "mandatory: true\n    mandatory_themes: []")
        with pytest.raises(ErroConfiguracao, match="mandatory_themes"):
            carregar_configuracao(cfg_dir)

    def test_fonte_florestal_removida(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "source_id: imaflora", "source_id: imaflora_x")
        with pytest.raises(ErroConfiguracao, match="imaflora"):
            carregar_configuracao(cfg_dir)

    def test_configuracao_temporal_invalida(self, cfg_dir):
        _substituir(cfg_dir / "regras.yaml", 'fim_exclusivo: "08:00:00"', 'fim_exclusivo: "25:00:00"')
        with pytest.raises(ErroConfiguracao, match="fim_exclusivo"):
            carregar_configuracao(cfg_dir)

    def test_horario_sem_aspas(self, cfg_dir):
        # YAML 1.1 interpreta 8:00:00 sem aspas como inteiro sexagesimal (28800).
        _substituir(cfg_dir / "regras.yaml", 'fim_exclusivo: "08:00:00"', "fim_exclusivo: 8:00:00")
        with pytest.raises(ErroConfiguracao, match="fim_exclusivo"):
            carregar_configuracao(cfg_dir)

    def test_fim_de_semana_na_configuracao(self, cfg_dir):
        _substituir(cfg_dir / "regras.yaml", 'sexta:   {dias_antes: 1, horario: "00:00:00"}',
                    'sexta:   {dias_antes: 1, horario: "00:00:00"}\n    sabado:  {dias_antes: 1, horario: "00:00:00"}')
        with pytest.raises(ErroConfiguracao, match="sabado"):
            carregar_configuracao(cfg_dir)

    def test_status_de_regra_desconhecido(self, cfg_dir):
        _substituir(cfg_dir / "regras.yaml", "status: politica_proposta", "status: operacional")
        with pytest.raises(ErroConfiguracao, match="status"):
            carregar_configuracao(cfg_dir)

    def test_regra_marcada_implementada_sem_implementacao(self, cfg_dir):
        _substituir(cfg_dir / "regras.yaml",
                    "status: declarada_fase_futura\n    fase_prevista: \"2\"\n    valor: {minimo: 3, maximo: 5}",
                    "status: implementada\n    valor: {minimo: 3, maximo: 5}")
        with pytest.raises(ErroConfiguracao, match="sem implementacao"):
            carregar_configuracao(cfg_dir)

    def test_regra_implementada_removida_do_yaml(self, cfg_dir):
        _substituir(cfg_dir / "regras.yaml",
                    "id: validacao_sintatica_url\n    categoria: seguranca\n    status: implementada",
                    "id: validacao_sintatica_url\n    categoria: seguranca\n    status: declarada_fase_futura\n    fase_prevista: \"1B\"")
        with pytest.raises(ErroConfiguracao, match="nao declaradas"):
            carregar_configuracao(cfg_dir)

    def test_mensagem_identifica_arquivo_campo_motivo(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "language: en", "language: ingles")
        with pytest.raises(ErroConfiguracao) as exc:
            carregar_configuracao(cfg_dir)
        e = exc.value
        assert e.arquivo.endswith("fontes.yaml") and "language" in e.campo and "idioma" in e.motivo


# ============================================================ Grupo H
# Credenciais FICTICIAS usadas nos testes de redacao (nao sao credenciais reais).
USUARIO_FICTICIO = "admin"
SEGREDO_FICTICIO = "TOKEN123"
URL_COM_CREDENCIAIS = f"https://{USUARIO_FICTICIO}:{SEGREDO_FICTICIO}@www.reuters.com/"
URL_REDIGIDA = "https://***@www.reuters.com/"
URL_REDIGIDA_CITADA = f"'{URL_REDIGIDA}'"
_CAMINHO_WIN = r"C:\Users\runneradmin\AppData\Local\Temp\pytest-0\config\fontes.yaml"
# Qualquer "esquema://<userinfo>@" cujo userinfo nao seja exatamente "***".
_USERINFO_NAO_REDIGIDO = re.compile(r"://(?!\*\*\*@)[^/\s'\"@]+@")


def _assert_sem_vazamento(texto: str) -> None:
    """Falha se o texto expuser a credencial ficticia (token ou usuario na URL).

    A palavra "admin" isolada (ex.: em ``runneradmin`` no caminho do arquivo)
    NAO e vazamento; so o e no contexto de autenticacao da URL.
    """
    assert SEGREDO_FICTICIO not in texto, "token nao redigido"
    assert f"{USUARIO_FICTICIO}:{SEGREDO_FICTICIO}" not in texto, "credencial original exposta"
    assert f"{USUARIO_FICTICIO}:" not in texto, "fragmento de autenticacao 'usuario:' exposto"
    assert f"://{USUARIO_FICTICIO}@" not in texto, "usuario nao redigido na URL"
    assert not _USERINFO_NAO_REDIGIDO.search(texto), "userinfo nao redigido na URL"


class TestGrupoHSeguranca:
    def test_tag_python_rejeitada(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("a: !!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="inseguro"):
            carregar_yaml(p)

    def test_tag_python_name_rejeitada(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("a: !!python/name:os.system\n", encoding="utf-8")
        with pytest.raises(ErroConfiguracao):
            carregar_yaml(p)

    def test_chave_duplicada_rejeitada(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("a: 1\nb: 2\na: 3\n", encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="chave duplicada"):
            carregar_yaml(p)

    def test_chave_duplicada_aninhada(self, cfg_dir):
        _substituir(cfg_dir / "regras.yaml", "  fuso: America/Sao_Paulo\n",
                    "  fuso: America/Sao_Paulo\n  fuso: UTC\n")
        with pytest.raises(ErroConfiguracao, match="chave duplicada"):
            carregar_configuracao(cfg_dir)

    def test_chave_nao_textual(self, tmp_path):
        p = tmp_path / "x.yaml"
        p.write_text("1: a\n", encoding="utf-8")
        with pytest.raises(ErroConfiguracao, match="nao textual"):
            carregar_yaml(p)

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "ftp://exemplo.com.br/",
            "data:text/html,<script>",
            "http://exemplo.com.br/",
            "https://",
            "https://exemplo .com.br/",
            "https://exemplo.com.br:99999/",
        ],
    )
    def test_urls_inseguras_ou_malformadas(self, url):
        with pytest.raises(ErroValidacao):
            validar_url(url, "u")

    def test_url_com_credenciais_rejeitada_sem_expor_segredo(self):
        with pytest.raises(ErroValidacao) as exc:
            validar_url("https://usuario:SENHA_SECRETA@exemplo.com.br/feed", "rss_url")
        assert "SENHA_SECRETA" not in str(exc.value) and "usuario" not in str(exc.value)
        assert "***@exemplo.com.br" in str(exc.value)

    @pytest.mark.parametrize(
        "pasta",
        [
            "comum",  # caminho temporario comum
            "runneradmin",  # usuario do runner Windows (C:\\Users\\runneradmin\\...)
            "admin_pasta",  # "admin" no caminho, fora do contexto de autenticacao
        ],
    )
    def test_credenciais_na_config_nao_vazam(self, tmp_path, pasta):
        cfg_dir = tmp_path / pasta / "config"
        shutil.copytree(CONFIG, cfg_dir)
        _substituir(cfg_dir / "fontes.yaml", '"https://www.reuters.com/"', f'"{URL_COM_CREDENCIAIS}"')
        with pytest.raises(ErroConfiguracao) as exc:
            carregar_configuracao(cfg_dir)
        erro = exc.value
        # Nem o motivo nem a mensagem completa (que inclui o caminho do arquivo) vazam.
        _assert_sem_vazamento(erro.motivo)
        _assert_sem_vazamento(str(erro))
        _assert_sem_vazamento(repr(erro))
        # A URL aparece redigida no motivo, e o motivo integra a mensagem completa.
        assert URL_REDIGIDA_CITADA in erro.motivo
        assert erro.motivo in str(erro)
        assert erro.campo.endswith("homepage_url")
        # O caminho pode conter "admin" legitimamente (ex.: runneradmin); nao e vazamento.
        assert pasta in erro.arquivo

    @pytest.mark.parametrize(
        "mensagem",
        [
            f"{_CAMINHO_WIN}: fontes[0].homepage_url: URL com credenciais: '{URL_COM_CREDENCIAIS}'",  # token
            f"{_CAMINHO_WIN}: fontes[0].homepage_url: URL com credenciais: 'https://{USUARIO_FICTICIO}@www.reuters.com/'",
            "erro: token TOKEN123 exposto",
            "erro: admin:TOKEN123",
            "erro: https://admin:@www.reuters.com/",
        ],
    )
    def test_detector_acusa_vazamento_sintetico(self, mensagem):
        """Controle negativo: o verificador detecta token ou usuario nao redigidos."""
        with pytest.raises(AssertionError):
            _assert_sem_vazamento(mensagem)

    @pytest.mark.parametrize(
        "mensagem",
        [
            f"{_CAMINHO_WIN}: fontes[0].homepage_url: URL com credenciais: '{URL_REDIGIDA}'",
            "/home/admin/config/fontes.yaml: fontes[0].homepage_url: 'https://www.reuters.com/'",
        ],
    )
    def test_detector_aceita_mensagem_redigida(self, mensagem):
        _assert_sem_vazamento(mensagem)

    def test_vazamento_real_simulado_e_detectado(self, tmp_path, monkeypatch):
        """Controle negativo ponta a ponta: sabotando a redacao (monkeypatch, sem
        alterar o codigo de producao), o vazamento passa a ser detectado."""
        import briefing.models as modelos

        monkeypatch.setattr(modelos, "redigir_url", lambda url: url)
        cfg_dir = tmp_path / "runneradmin" / "config"
        shutil.copytree(CONFIG, cfg_dir)
        _substituir(cfg_dir / "fontes.yaml", '"https://www.reuters.com/"', f'"{URL_COM_CREDENCIAIS}"')
        with pytest.raises(ErroConfiguracao) as exc:
            carregar_configuracao(cfg_dir)
        assert SEGREDO_FICTICIO in exc.value.motivo  # a sabotagem expoe o segredo
        for texto in (exc.value.motivo, str(exc.value)):
            with pytest.raises(AssertionError):
                _assert_sem_vazamento(texto)

    def test_redigir_url(self):
        assert redigir_url("https://a:b@h.com/x") == "https://***@h.com/x"
        assert redigir_url("https://h.com/x") == "https://h.com/x"

    def test_enum_inesperado_na_config(self, cfg_dir):
        _substituir(cfg_dir / "fontes.yaml", "access_status: unverified", "access_status: ok")
        with pytest.raises(ErroConfiguracao, match="valor desconhecido"):
            carregar_configuracao(cfg_dir)

    def test_validacao_url_e_apenas_sintatica(self):
        # dominio reservado (RFC 2606) passa: a validacao nao comprova existencia
        assert validar_url("https://inexistente.example/", "u")
