# Instruções de retomada — PNCP AP esportivos/educativos (complementação)

## Situação entregue nesta sessão

Esta base é uma **entrega parcial, quantificada** (não uma extração completa).
O motivo é ambiental, não de escopo: este ambiente de execução bloqueia
acesso de rede a `pncp.gov.br` (política de egress da organização — 403 no
CONNECT, confirmado via `/root/.ccr/README.md` e o status do proxy). Todas
as correções de **código e de dados já armazenados** foram implementadas,
testadas e executadas por completo. O que falta é **coleta de rede nova**
(páginas de itens além da primeira, e resultados ainda não consultados).

### O que foi feito (sem depender de rede)

- Auditoria confirmada: 2607 contratações, 1472 estadual/municipal (923+549),
  11221 itens, 267 janelas de publicação cobrindo integralmente
  2025-01-01–2026-09-12 sem lacunas (não precisa reconsulta).
- Classificador de produtos reescrito (`code/classificador.py`, v2) e testado
  (`code/testes_classificador.py`, 40/40 casos). Corrige os falsos
  positivos/negativos documentados na auditoria (EVA/micro-ônibus, xadrez,
  medalha/medalhão, apito, papel sulfite, apontador etc.) e mais alguns
  equivalentes encontrados durante a reconciliação completa.
- Regra de cancelamento corrigida (`code/regras_negocio.py`) e testada
  (`code/testes_regras_negocio.py`, 6/6 casos). Corrige o caso real do banco
  (situação oficial "Cancelado" sem data/motivo era tratada como não
  cancelado).
- TODOS os 11221 itens armazenados foram reprocessados com o classificador
  v2 (`code/reprocessar_base.py`) — não apenas os 268 antes aceitos.
- Lógica de paginação real implementada (`code/paginacao_itens.py`) e
  testada com 21 casos simulados (mock), incluindo os dois números reais
  da auditoria (211 e 257 itens) e cenários patológicos (servidor ignorando
  o parâmetro `pagina`, falha no meio da paginação, 404/429, deduplicação).
- Camada analítica corrigida (`code/pncp_ap_export.py`): preços agrupados
  por produto+unidade+fator de conversão+especificação; sazonalidade
  restrita ao rol (com o indicador geral mantido, claramente rotulado);
  rankings/concentração recalculados separadamente para
  AP_ESTADUAL_MUNICIPAL e AP_TODAS_ESFERAS (nunca somados).

### O que está pendente (requer rede liberada para pncp.gov.br)

- **696 contratações com exatamente 10 itens armazenados** (alta prioridade
  — suspeita de truncamento na primeira página do bug da v1).
- **1911 contratações restantes** com itens armazenados por chamada única
  sem paginação (completude não comprovada, mas podem estar corretas se a
  lista real tiver ≤10 itens).
- Resultados de itens ACEITOS ainda sem consulta (`resultados_status =
  'nao_consultado'`).

Esses números estão em `export/cobertura.csv` e `export/pendencias.csv`.

## Como retomar (em ambiente com rede liberada para pncp.gov.br)

```bash
cd pncp_ap_complementado/code

# 1) Validação minima contra a API real (poucas requisicoes) —
#    confirma que os dois casos conhecidos da auditoria (211 e 257 itens)
#    são recuperados corretamente pela paginação real antes de rodar tudo.
python pncp_ap_collector.py --validate

# 2) Recuperação de itens com paginação real, priorizando as 696
#    contratações com suspeita de truncamento.
python pncp_ap_collector.py --recuperar-itens --time-budget-min 120

# (repita o comando acima quantas vezes forem necessárias — ele retoma
#  de onde parou, usando a tabela paginacao_itens como checkpoint)

# 3) Depois que os itens estiverem recuperados, complementar resultados
#    dos itens ACEITOS (a lista de aceitos muda conforme os novos itens
#    chegam, então rode isso DEPOIS do passo 2):
python pncp_ap_collector.py --recuperar-resultados --time-budget-min 60

# 4) Gerar novamente os CSVs/Excel com os dados completos:
python pncp_ap_collector.py --export
# (equivalente a: python pncp_ap_export.py ../data/captura.sqlite ../export)

# 5) Acompanhar progresso em qualquer momento:
python pncp_ap_collector.py --status
```

O banco `data/captura.sqlite` já contém todas as correções de classificação
e cancelamento; a retomada de rede só *adiciona* itens/resultados novos por
upsert — nenhum dado existente é substituído ou perdido.

## Estrutura entregue

```
pncp_ap_complementado/
├── data/captura.sqlite          # banco complementado (reclassificado, cancelamento corrigido)
├── code/                        # código corrigido + testes + código original preservado (_ORIGINAL.py)
├── export/                      # CSVs, Excel e manifesto desta entrega
├── logs/                        # logs originais preservados
└── INSTRUCOES_RETOMADA.md       # este arquivo
```

Backups do ZIP original e da extração intacta ficam em `../backup_original/`.
