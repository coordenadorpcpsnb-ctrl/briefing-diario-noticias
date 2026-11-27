# briefing-diario-noticias

Automação do **briefing executivo diário de notícias** para a gestão de empresas
dos setores florestal e siderúrgico: pesquisa, validação, análise, geração e
publicação em <https://coordenadorpcpsnb-ctrl.github.io/briefing-diario-noticias/>,
sem dependência operacional de conversas ou tarefas agendadas de assistentes de IA.

> **Estado atual (Fase 1A):** existe apenas a **fundação técnica** — contratos de
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
constraints-dev.txt       # versões exatas usadas nos testes da Fase 1A
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
- Dependências de execução: `PyYAML`. Desenvolvimento: `pytest`.

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -c constraints-dev.txt -e ".[dev]"
```

A instalação editável dispensa ajustes de `PYTHONPATH`. O arquivo
`constraints-dev.txt` fixa as versões efetivamente usadas nos testes.

## Execução dos testes

```bash
.venv/bin/python -m pytest
```

Os testes são determinísticos: não acessam internet, APIs pagas ou o relógio do
sistema (a data de execução é sempre argumento explícito).

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
  original (estável, sem aleatoriedade); `summary` limitado a 600 caracteres
  (texto integral não é armazenado).
- `PublicationWindow` — janela semiaberta.
- `TemporalDecision` — decisão com `status`, `reason_code`, descrição, janela e
  `eligible` explícito.
- `CollectionResult` — contrato de resultado de coleta (coletores não
  implementados). Somente `success_empty` confirma ausência; falha técnica,
  acesso restrito, coleta parcial ou fonte não consultada **nunca** equivalem a
  ausência confirmada.

`from_dict` é estrito (rejeita campos desconhecidos); `to_dict` gera JSON puro.

## Configuração

- **Temas** (`config/temas.yaml`): `politica`, `economia`,
  `mercados_financeiros`, `inteligencia_artificial`, `inovacoes_florestais`
  (nacional e internacional); `mercado_aco`, `setor_florestal` (somente Brasil).
  Meta editorial de 3 a 5 notícias quando disponíveis — não obriga preenchimento.
  Palavras-chave ainda não são usadas (sem classificação automática).
- **Fontes** (`config/fontes.yaml`): 25 fontes, **nenhuma habilitada**, todas
  com acesso `unverified`, sem `rss_url` e sem método de coleta (nada foi testado
  nesta fase). `url_provenance` indica a origem de cada URL. Ministérios estão em
  cadastro **pendente** (endereço específico não verificado). As cinco fontes
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
- `diretorio_config_padrao()` pressupõe instalação editável a partir do
  repositório.
- Proteção contra SSRF não existe porque ainda não há requisições HTTP; é
  **requisito obrigatório da Fase 1B**.

## Roadmap

| Fase | Escopo | Estado |
|---|---|---|
| 0 | Diagnóstico e baseline | concluída |
| 1A | Fundação técnica e regras temporais | **etapa atual** |
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
