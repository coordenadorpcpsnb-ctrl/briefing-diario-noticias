# briefing-diario-noticias

Automação do **briefing executivo diário de notícias** para a gestão de empresas
dos setores florestal e siderúrgico: pesquisa, validação, análise, geração e
publicação em <https://coordenadorpcpsnb-ctrl.github.io/briefing-diario-noticias/>,
sem dependência operacional de conversas ou tarefas agendadas de assistentes de IA.

> **Estado atual (Fases 1A, 1A.1 e 1A.2):** existe apenas a **fundação técnica** — contratos de
> dados, motor determinístico da janela temporal, configuração declarativa
> validada e testes. **Não há** coleta de notícias, uso de APIs, inteligência
> artificial, indicadores, geração de HTML, publicação ou workflows. As edições
> em `docs/` continuam sendo produzidas pelo processo anterior.

## Arquitetura geral (planejada)

Pipeline em estágios desacoplados, cada um com contrato de dados próprio:

```
coleta -> normalização -> validação temporal -> deduplicação -> validação de fontes
       -> auditoria de cobertura -> indicadores -> análise por IA -> validação da saída
       -> renderização HTML -> publicação -> monitoramento
```

A IA nunca será autoridade exclusiva para datas, links, cotações ou integridade
de arquivos: esses pontos são validados por código determinístico.

Nesta fase, existem somente os estágios de **contratos** e **validação temporal**.

## Estrutura de arquivos

```
pyproject.toml            # pacote Python (src layout), dependências
requirements/
  runtime.lock            # dependências de execução: versões exatas + hashes SHA-256
  dev.lock                # execução + desenvolvimento (pytest), universal Linux/Windows
  build.in, build.lock    # dependências de build (setuptools, wheel) + hashes
ferramentas/
  gerar_locks.sh          # regeneração dos locks (uv fixado, somente na manutenção)
  verificacao_ci.py       # verificações de configuração e de instalação convencional
  verificar_docs_baseline.py  # integridade de docs/ contra a baseline 9d2f2d9
auditoria/
  docs-baseline-9d2f2d9.sha256  # hashes SHA-256 completos de docs/ na baseline
.github/workflows/ci.yml  # CI (somente verificação; sem deploy, coleta ou secrets)
config/
  temas.yaml              # 7 temas editoriais
  fontes.yaml             # cadastro de fontes (nenhuma habilitada para coleta)
  regras.yaml             # regras de negócio (fonte única dos parâmetros da janela)
src/briefing/
  models.py               # contratos de dados (dataclasses validadas)
  janela.py               # janela temporal e elegibilidade
  configuracao.py         # carregamento seguro e validação dos YAML
tests/                    # pytest, sem rede, sem APIs, sem relógio do sistema
docs/                     # portal e edições publicadas (preservados, não gerados aqui)
```

## Requisitos e instalação

- Python **3.12** (referência; `requires-python >= 3.12`).
- Dependências de execução: `PyYAML` e `tzdata`. Desenvolvimento: `pytest`.
  Build: `setuptools` e `wheel`.
- `tzdata` fornece a base IANA de fusos ao `zoneinfo` quando o sistema
  operacional não a possui.
- Plataformas no CI: `ubuntu-24.04` e `windows-2025`, ambas com Python 3.12. A
  compatibilidade só é **comprovada** pelo resultado efetivo do job B de cada
  execução (veja os checks da PR); localmente, apenas Linux foi testado.
  macOS não é verificado.

### Instalação reproduzível (locks com hashes)

Dependências externas são instaladas **somente** a partir dos locks, com
`--require-hashes` (o pip recusa qualquer artefato cujo hash não confira) e
`--no-deps` (nenhuma resolução adicional). O próprio projeto é local e não tem
hash publicado: é instalado com `--no-deps` (e `--no-index` para o wheel), sem
resolver nada novo.

Desenvolvimento (projeto editável, como no CI):

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install --require-hashes --no-deps -r requirements/dev.lock -r requirements/build.lock
```

```bash
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

```bash
.venv/bin/python -m pip check
```

No Windows, use `.venv\Scripts\python.exe` no lugar de `.venv/bin/python`.
`--no-build-isolation` faz o build usar o setuptools/wheel instalados a partir
de `build.lock`, em vez de baixar versões não fixadas.

### Instalação convencional (wheel + configuração externa)

Os arquivos YAML **não** são empacotados no wheel. Três etapas separadas:

1. Build controlado (dependências de build verificadas):

```bash
python3.12 -m venv /tmp/build && /tmp/build/bin/python -m pip install --require-hashes --no-deps -r requirements/build.lock
```

```bash
/tmp/build/bin/python -m pip wheel --no-deps --no-build-isolation -w dist .
```

2. Instalação em ambiente limpo: dependências externas com hashes e depois o
   wheel local, sem nova resolução:

```bash
python3.12 -m venv /tmp/inst && /tmp/inst/bin/python -m pip install --require-hashes --no-deps -r requirements/runtime.lock
```

```bash
/tmp/inst/bin/python -m pip install --no-deps --no-index dist/*.whl && /tmp/inst/bin/python -m pip check
```

3. Execução fora do checkout, com configuração externa:

```bash
cd /tmp && BRIEFING_CONFIG_DIR=/caminho/para/config /tmp/inst/bin/python -c "from briefing.configuracao import carregar_configuracao; print(carregar_configuracao().origem)"
```

### Resolução do diretório de configuração

`carregar_configuracao(diretorio=None)` usa, nesta ordem, **sem fallback
silencioso**:

1. o diretório informado pelo chamador (se inválido → erro);
2. a variável `BRIEFING_CONFIG_DIR` (se definida, mesmo vazia, e inválida → erro);
3. `config/` do checkout, somente se o pacote estiver em `<raiz>/src/briefing/`
   e `<raiz>/pyproject.toml` for deste projeto (não depende do diretório de
   trabalho e não procura em ancestrais);
4. erro explícito.

O diretório precisa conter `temas.yaml`, `fontes.yaml` e `regras.yaml` como
arquivos regulares. A configuração carregada registra `diretorio` e `origem`.

### Estratégia de locks e atualização de dependências

- Os locks são gerados por `ferramentas/gerar_locks.sh` com **uv 0.11.32**
  (`uv pip compile --universal --python-version 3.12 --generate-hashes`). O uv
  serve **apenas para gerar** os locks; instalação e CI usam somente pip.
- Por que não pip-tools: o `pip-compile` resolve só para a plataforma em que
  roda, e o pytest exige `colorama` apenas no Windows. O lock universal registra
  `colorama==0.4.6 ; sys_platform == 'win32'`, de modo que um único arquivo
  serve para Linux e Windows, sem listas duplicadas por plataforma.
- `tests/test_dependencias.py` verifica a consistência: todo pacote fixado com
  hash; os locks satisfazem `pyproject.toml` (`dependencies`, extra `dev`,
  `[build-system].requires`); `runtime.lock` e `dev.lock` são idênticos nos
  pacotes comuns; e o ambiente instalado corresponde exatamente ao `dev.lock`.

Atualização:

1. Em ambiente limpo, `pip install "uv==0.11.32"`.
2. `bash ferramentas/gerar_locks.sh` mantém as versões; com `--upgrade`, busca
   versões novas.
3. Revise `git diff requirements/`: versões, pacotes novos ou removidos, notas
   de versão.
4. Valide os hashes reinstalando em ambiente virtual limpo com
   `--require-hashes` (passos acima) e rode `pytest`.
5. Faça o commit dos locks junto com a alteração e deixe o CI confirmar em
   Linux e Windows.

Não há atualização automática de dependências.

## Execução dos testes

```bash
.venv/bin/python -m pytest
```

Os testes são determinísticos: não acessam internet, APIs pagas ou o relógio do
sistema (a data de execução é sempre argumento explícito). A suíte exige um
ambiente instalado pelos locks (`test_dependencias.py` compara versões).

Verificações adicionais, também usadas no CI:

```bash
.venv/bin/python ferramentas/verificacao_ci.py configuracao
```

```bash
.venv/bin/python ferramentas/verificar_docs_baseline.py
```

## Integração contínua (`.github/workflows/ci.yml`)

**Gatilhos:** `pull_request` para `main`, `push` em `main` e `workflow_dispatch`
(que só aparece depois que o workflow existe na branch padrão). Sem
`pull_request_target`, sem agendamento, sem filtros de caminho, sem coleta,
sem deploy e sem secrets.

**Segurança:**
- `permissions: contents: read`, sem redefinição por job;
- checkout com `persist-credentials: false`;
- Actions fixadas por SHA completo de commit (verificado nos repositórios
  oficiais):
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1)
  - `actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` (v7.1.0)
- nenhuma expressão `${{ }}` dentro de `run:`; nenhum `curl` ou `wget`.

**Controle operacional:**
- concorrência por workflow e PR/ref, cancelando execuções obsoletas da mesma PR;
- `timeout-minutes` em todos os jobs;
- runners nomeados explicitamente;
- sem `continue-on-error`;
- cache de pip do `setup-python`, com chave por plataforma e por
  `requirements/*.lock`. Os hashes continuam sendo verificados, e a ausência de
  cache só torna a execução mais lenta.

| Job | Ambiente | O que verifica |
|---|---|---|
| A — Qualidade e regressão | ubuntu-24.04, Python 3.12 | instalação por hashes, `pip check`, suíte completa, YAML/contratos/janelas/continuidade (`verificacao_ci.py configuracao`) |
| B — Portabilidade e instalação | matriz ubuntu-24.04 e windows-2025, Python 3.12 | build do wheel com dependências de build fixadas; instalação convencional em venv limpo; execução fora do checkout com `BRIEFING_CONFIG_DIR` (7/25/33, janelas de segunda e de terça a sexta, erro sem configuração, fallback `tzdata` com `PYTHONTZPATH` vazio); `test_portabilidade.py` com o pacote instalado; suíte completa na plataforma |
| C — Integridade e segurança | ubuntu-24.04, Python 3.12 | `docs/` idêntico à baseline 9d2f2d9 (SHA-256 completos e `git diff`); segurança estrutural do workflow; consistência dos locks; higiene do repositório (sem caches nem credenciais reais); `git diff --check` em relação à base |

**Interpretação dos resultados:**
- O CI só está aprovado quando **todos** os jobs, incluindo as duas entradas da
  matriz, terminam com *success* **no SHA do HEAD atual** da PR. Para eventos
  `pull_request`, o GitHub testa a referência sintética de merge
  (`refs/pull/N/merge`).
- Jobs pendentes, cancelados ou ignorados **não** contam como aprovação.
- Testes locais e testes do GitHub Actions são evidências distintas: só os do
  Actions comprovam o comportamento no Windows.

**Diagnóstico de falhas:**
- Abra a execução do job que falhou e leia o passo em vermelho.
  - Falha de hash na instalação: lock adulterado, desatualizado ou com pacote
    sem hash; regenere com o script e revise.
  - Falha de `pip check`: incompatibilidade entre as versões fixadas.
  - Falha de teste: o nome do teste e o `-ra` mostram a causa.
  - Falha em `verificar_docs_baseline.py`: algo mudou em `docs/`.
  - Falha nos testes de workflow: uma regra de segurança foi violada.
- Reproduza localmente com os mesmos comandos dos passos (instalação por
  hashes).
- Reexecute apenas depois de entender a causa.

**Política de `docs/`:** nesta fase, nenhuma alteração em `docs/` é aceita. Na
Fase 6, a política deverá evoluir para permitir, de forma controlada, novas
edições, atualização autorizada do manifesto e do portal e revisões
históricas aprovadas. Isso ainda não está implementado.

## Regras temporais aprovadas

- Fuso: `America/Sao_Paulo` (IANA via `zoneinfo`; nunca offset fixo UTC−3).
- Execução: segunda a sexta. Sábado e domingo são rejeitados com erro explícito.
- Intervalo **semiaberto**: `início <= publicação < fim_exclusivo`, com fim
  exclusivo às **08:00:00** do dia de execução. Para o leitor, a janela termina
  às **07h59** (o limite inclusivo 07:59:59 não é usado internamente porque
  excluiria frações de segundo).

| Edição | Início | Fim exclusivo | Duração nominal |
|---|---|---|---|
| Segunda | sexta anterior 08:00:00 | segunda 08:00:00 | 72 h |
| Terça a sexta | dia anterior 00:00:00 | dia 08:00:00 | 32 h |

Exemplos:

| Edição | Início | Fim exclusivo |
|---|---|---|
| Sexta 27/11/2026 | 26/11/2026 00:00:00 | 27/11/2026 08:00:00 |
| Segunda 30/11/2026 | 27/11/2026 08:00:00 | 30/11/2026 08:00:00 |
| Terça 01/12/2026 | 30/11/2026 00:00:00 | 01/12/2026 08:00:00 |

Continuidade: o fim de sexta coincide exatamente com o início de segunda (sem
lacuna e sem sobreposição); entre segunda e sexta há **sobreposição intencional
de 8 horas** entre edições consecutivas, a ser tratada pela deduplicação
(Fase 2, ainda não implementada).

**Feriados:** política inicial — feriados de segunda a sexta seguem as regras
normais. Não há calendário de feriados; a política pode ser revista.

Os parâmetros ficam em `config/regras.yaml` (`janela_temporal`), que é a fonte
única de verdade; `janela.py` não contém constantes de horário.

### Registro de decisão — janela de segunda-feira

| | |
|---|---|
| Data da decisão | 27/11/2026 |
| Regra anterior | segunda-feira começava na sexta-feira às 18h00 |
| Regra aprovada | segunda-feira começa na sexta-feira às 08h00 |
| Justificativa | eliminar a lacuna semanal de dez horas (sexta 08h00–18h00) sem cobertura |

As edições de 24, 25 e 26/11/2026 foram produzidas sob a regra vigente naquelas
datas e não são classificadas como incorretas por não atenderem à nova regra.

## Tratamento de metadados incompletos

Três níveis são mantidos separados:

1. **Metadado recebido** — `RawPublicationMetadata`: valores brutos (ISO 8601
   nesta fase), possivelmente inválidos ou contraditórios.
2. **Metadado validado** — `Article`: só aceita combinações coerentes; nunca
   aceita datetime sem fuso, nunca cria meia-noite fictícia para datas sem
   horário e nunca usa `retrieved_at` ou `updated_at` como data de publicação.
3. **Decisão temporal** — `TemporalDecision`, com estado explícito:

| Estado | Quando | Elegível? |
|---|---|---|
| `ELIGIBLE_CONFIRMED` | instante com fuso dentro da janela | sim |
| `ELIGIBLE_DATE_ONLY` | sem horário; dia civil inteiro dentro da janela | sim |
| `PENDING_TIME_CONFIRMATION` | sem horário; dia civil parcialmente na janela | não |
| `PENDING_TIMEZONE_CONFIRMATION` | horário/data sem fuso comprovado | não |
| `PENDING_PUBLICATION_DATE` | sem data original (mesmo com data de atualização) | não |
| `REJECTED_OUTSIDE_WINDOW` | fora da janela | não |
| `REJECTED_INVALID_METADATA` | valor inválido, fuso IANA inválido ou contradição | não |

O dia civil é `[00:00 local, 00:00 local do dia seguinte)` no fuso **documentado**
da publicação; o fuso nunca é inferido do idioma ou país do veículo.
Elegibilidade temporal **não** é aprovação editorial nem confirmação independente.

## Contratos de dados (`src/briefing/models.py`)

- `SourceConfig` — fonte cadastrada. *Cadastrada ≠ habilitada*: só pode ser
  habilitada com cadastro completo, URL válida, método de coleta com a URL que o
  método exige e acesso verificado. *Obrigatória ≠ disponível*.
- `ThemeConfig` — tema editorial.
- `PublicationEvidence` — evidência estruturada (RSS, JSON-LD `datePublished`,
  metadado HTML, data visível, API, outro, desconhecido). Os tipos são
  possibilidades futuras, não coletores implementados.
- `RawPublicationMetadata` — metadado recebido.
- `Article` — notícia validada; `article_id` = SHA-256 de `source_id` + URL
  original (estável, sem aleatoriedade).
  - `source_excerpt` — **exclusivamente** um trecho/descrição fornecido pela
    fonte, normalizado; **não** é análise editorial nem resumo executivo (estes
    terão modelo próprio na Fase 3, ainda não implementado).
  - `excerpt_truncated` — `True` quando o trecho foi reduzido ao limite;
    `True` sem `source_excerpt` é rejeitado.
  - Limite técnico em `config/regras.yaml` → `limites.source_excerpt_max_chars`
    (600, **provisório**; não é garantia jurídica de conformidade com direitos
    autorais). Fonte única: nenhuma constante no código.
  - Caminho para descrições recebidas: `Article.criar_a_partir_da_fonte(
    trecho_bruto=..., limite_trecho=cfg.regras.source_excerpt_max_chars, ...)`.
    Uma descrição extensa é truncada e sinalizada, **nunca** causa a perda do
    artigo, e não altera ID, título, URL, autor, datas, evidência ou
    elegibilidade temporal. `verificar_limite_trecho()` confere artigos já
    construídos. O modelo não carrega YAML.
  - `normalizar_trecho(texto, limite)` → `(trecho, truncado)`: `None` ou texto
    vazio/só espaços → `(None, False)`; aplica Unicode NFC, colapsa espaços e
    remove bordas; corta por caractere (nunca por byte), sem separar marcas
    combinantes e sem reticências. Determinístico, sem IA e sem rede.
  - Alteração de contrato (Fase 1A.1): `summary` foi substituído por
    `source_excerpt` + `excerpt_truncated`. Não havia dados persistidos, logo
    não há migração; `from_dict` rejeita o campo antigo.
- `PublicationWindow` — janela semiaberta.
- `TemporalDecision` — decisão com `status`, `reason_code`, descrição, janela e
  `eligible` explícito.
- `CollectionResult` — contrato de resultado de **uma** operação de coleta
  (coletores não implementados). `coleta_vazia_confirmada` é `True` somente
  para `success_empty`: a operação terminou corretamente e retornou zero itens.
  Falha técnica, acesso restrito, coleta parcial ou fonte não consultada nunca
  são coleta vazia.
  - **Coleta vazia ≠ ausência temática.** A propriedade não indica ausência de
    notícias em um tema, no período, em todos os canais da fonte, nem
    cobertura suficiente ou confirmação editorial. Concluir ausência temática
    cabe à futura auditoria de cobertura (Fase 2), que exigirá três famílias de
    fontes efetivamente consultadas. (A propriedade antiga `confirma_ausencia`
    foi removida na Fase 1A.1, sem alias.)

`from_dict` é estrito (rejeita campos desconhecidos); `to_dict` gera JSON puro.

## Configuração

- **Temas** (`config/temas.yaml`): `politica`, `economia`,
  `mercados_financeiros`, `inteligencia_artificial`, `inovacoes_florestais`
  (nacional e internacional); `mercado_aco`, `setor_florestal` (somente Brasil).
  Meta editorial de 3 a 5 notícias quando disponíveis — não obriga preenchimento.
  Palavras-chave ainda não são usadas (sem classificação automática).
- **Fontes** (`config/fontes.yaml`): 25 fontes, **nenhuma habilitada**, todas
  com acesso `unverified`, sem `rss_url` e sem método de coleta (nada foi testado
  nesta fase). `url_provenance` indica a origem de cada URL: 5 fornecidas pelo
  responsável, 7 observadas nas edições históricas, 10 de conhecimento público
  (não verificadas operacionalmente) e 3 pendentes de URL (ministérios, em
  cadastro **pendente**, endereço específico não verificado). As cinco fontes
  florestais obrigatórias (Mais Floresta, Malinovski, Imaflora, Embrapa
  Florestas, Forests News) usam as URLs fornecidas pelo responsável como pontos
  de partida, não como feeds confirmados.
- **Famílias de fontes**: `news_agency`, `general_press`, `financial_press`,
  `government`, `scientific_institution`, `industry_association`,
  `specialized_press`, `corporate_primary`, `civil_society`. Critério: natureza
  editorial predominante do emissor. A família **não comprova independência**:
  duas republicações da mesma agência, ou notícias que reproduzem apenas o
  mesmo comunicado primário, não são confirmações independentes (regra a
  implementar na Fase 2).
- **Regras** (`config/regras.yaml`): cada regra tem `status` —
  `implementada`, `declarada_fase_futura`, `politica_proposta` (ex.: execução
  08h03, publicação até 08h30) ou `decisao_pendente`. Aparecer no YAML **não**
  torna a regra operacional; um teste garante que as regras marcadas como
  implementadas coincidem com as implementadas no código.

A validação rejeita IDs e chaves YAML duplicados, tags YAML inseguras, temas
inexistentes, famílias e enums inválidos, escopos incompatíveis, campos ausentes
ou de tipo incorreto, URLs malformadas, não-HTTPS ou com credenciais (mensagens
redigem credenciais), fontes habilitadas sem método/URL/acesso verificado e
incoerências entre `mandatory` e `mandatory_themes`. A validação de URL é
**exclusivamente sintática** — não comprova que a URL existe.

## Limitações conhecidas

- Nenhuma URL de fonte foi testada; endereços com `public_knowledge_untested`
  podem estar desatualizados.
- Grupos econômicos (`ownership_group`), agências de origem e fusos das fontes
  não foram registrados (pendentes de verificação).
- Apenas ISO 8601 é interpretado em metadados brutos; RFC 2822 (feeds RSS)
  fica para a Fase 1B.
- Horários locais ambíguos/inexistentes em transições de horário de verão
  (inexistentes hoje em `America/Sao_Paulo`) usam a regra padrão do `zoneinfo`.
- Fora do checkout (instalação convencional), a configuração precisa ser
  fornecida por argumento ou `BRIEFING_CONFIG_DIR`; os YAML não vão no wheel.
- Reprodutibilidade: as dependências externas e de build estão fixadas e
  verificadas por hash, mas a versão do próprio `pip` (e a patch do Python 3.12
  no CI) não é fixada; o build do wheel **não** foi comprovado como
  reproduzível byte a byte.
- Não há ferramenta de cobertura de código nem de análise de vulnerabilidades
  (ex.: pip-audit). Testes aprovados **não** comprovam ausência de
  vulnerabilidades nem cobertura integral.
- Os testes de segurança do workflow são estruturais (padrões no YAML) e
  complementam, mas não substituem, revisão humana.
- O limite de 600 caracteres do trecho é técnico e provisório; truncamento
  respeita caracteres e marcas combinantes, mas não clusters de grafemas
  complexos (ex.: emojis compostos por ZWJ) — em geral inofensivo, porém
  documentado.
- O CI não tem proteção de branch associada (configuração administrativa fora
  do escopo): um check com falha não bloqueia o merge automaticamente.
- Proteção contra SSRF não existe porque ainda não há requisições HTTP; é
  **requisito obrigatório da Fase 1B**.

## Roadmap

| Fase | Escopo | Estado |
|---|---|---|
| 0 | Diagnóstico e baseline | concluída |
| 1A | Fundação técnica e regras temporais | em revisão (PR #1) |
| 1A.1 | Correções de contratos, portabilidade e configuração | em revisão (PR #1) |
| 1A.2 | CI e reprodutibilidade | **etapa atual** |
| 1B | Coletores RSS e fontes diretas (com proteção SSRF) | não iniciada |
| 1C | Workflow experimental de coleta | não iniciada |
| 1D | Avaliação e integração de API de pesquisa | não iniciada |
| 2 | Validação, deduplicação e auditoria | não iniciada |
| 3 | Integração de inteligência artificial | não iniciada |
| 4 | Indicadores econômicos | não iniciada |
| 5 | Geração padronizada do HTML | não iniciada |
| 6 | Publicação automatizada | não iniciada |
| 7 | Testes integrados e monitoramento | não iniciada |
| 8 | Entrada em produção | não iniciada |
