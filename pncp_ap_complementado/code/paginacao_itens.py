# -*- coding: utf-8 -*-
"""
Paginacao real do endpoint de itens do PNCP:

  GET /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencialCompra}/itens
      ?pagina=N&tamanhoPagina=M

BUG CORRIGIDO (v1): o coletor original fazia uma UNICA chamada, sem
nenhum parametro de pagina/tamanhoPagina, e tratava a resposta (ate 10
itens) como o total da contratacao. Isso foi comprovado com dois casos
reais fornecidos na auditoria:
  - 23066905000160-1-000003/2025: 211 itens reais, 10 armazenados
  - 05995766000177-1-000081/2025: 257 itens reais, 10 armazenados

Este modulo isola a LOGICA de paginacao em uma funcao pura,
`paginar_itens`, que recebe uma funcao `get_fn(params) -> (status, data,
erro)` injetavel. Isso permite testar exaustivamente o comportamento de
paginacao (incluindo casos em que o servidor ignora o parametro `pagina`)
sem depender de rede -- indispensavel neste ambiente, onde o acesso a
pncp.gov.br esta bloqueado pela politica de egress (ver manifesto de
execucao para detalhes). A mesma funcao e usada tanto pelos testes com
mock quanto pela execucao real (via `PncpClient.get`), garantindo que o
codigo testado e o codigo executado sejam o mesmo.
"""
from datetime import datetime

TAMANHO_PAGINA_ITENS = 50
MAX_PAGINAS_SEGURANCA = 100  # 100 * 50 = 5000 itens; acima disso, algo esta errado


def _chave_item(item):
    return item.get("numeroItem")


def _chave_resultado(item):
    return item.get("sequencialResultado")


def paginar_itens(get_fn, tamanho_pagina=TAMANHO_PAGINA_ITENS, max_paginas=MAX_PAGINAS_SEGURANCA,
                   chave_fn=_chave_item):
    """Percorre todas as paginas de itens de UMA contratacao.

    get_fn(pagina, tamanho_pagina) -> (status_code, data_ou_None, erro_ou_None)
      status_code: 200 (data e lista), 404, 429, None (falha de rede) ou outro inteiro.
      data: lista de itens (dict) quando status==200.

    Retorna dict:
      status: 'completo' | 'parcial_falha' | 'sem_registros' | 'servidor_ignora_pagina'
      itens: lista de itens deduplicados por numeroItem (ordem de primeira ocorrencia)
      paginas_lidas: int
      motivo: str (detalhe para log/auditoria)
    """
    itens_por_chave = {}
    ordem_chaves = []
    pagina = 1
    paginas_lidas = 0
    primeira_pagina_chaves = None

    while pagina <= max_paginas:
        status, data, erro = get_fn(pagina, tamanho_pagina)

        if status == 429:
            # rate limit: nao e falha real, o chamador deve tentar novamente depois.
            return dict(status="parcial_falha", itens=list(itens_por_chave.values()),
                        paginas_lidas=paginas_lidas,
                        motivo="rate limited (429) na pagina %d; retomar depois, itens ja lidos preservados" % pagina)

        if status == 404:
            if pagina == 1:
                return dict(status="sem_registros", itens=[], paginas_lidas=0,
                            motivo="404 na primeira pagina: sem itens cadastrados (nao e falha)")
            # 404 em pagina > 1 apos ja ter itens: fim de paginacao (comportamento observado
            # em alguns endpoints do PNCP para pagina fora do intervalo). Nao e falha.
            return dict(status="completo", itens=list(itens_por_chave.values()),
                        paginas_lidas=paginas_lidas,
                        motivo="404 na pagina %d apos %d pagina(s) com dados; interpretado como fim da lista" % (
                            pagina, paginas_lidas))

        if status != 200 or data is None or not isinstance(data, list):
            # Qualquer outra falha (timeout, erro de conexao, JSON invalido, status inesperado):
            # NAO assumir ausencia de itens nem completude. Preserva o que ja foi lido.
            return dict(status="parcial_falha", itens=list(itens_por_chave.values()),
                        paginas_lidas=paginas_lidas,
                        motivo="falha na pagina %d: %s; contratacao NAO marcada como completa" % (pagina, erro))

        chaves_pagina = [chave_fn(it) for it in data]

        if pagina == 1:
            primeira_pagina_chaves = chaves_pagina
            if not data:
                return dict(status="sem_registros", itens=[], paginas_lidas=1,
                            motivo="pagina 1 vazia: sem itens cadastrados")
        else:
            # Deteccao de servidor ignorando o parametro 'pagina': pagina N (N>1) devolve
            # exatamente as mesmas chaves da pagina 1 -> o parametro nao teve efeito.
            if chaves_pagina and chaves_pagina == primeira_pagina_chaves:
                return dict(status="servidor_ignora_pagina", itens=list(itens_por_chave.values()),
                            paginas_lidas=paginas_lidas,
                            motivo=("pagina %d devolveu identico a pagina 1; servidor parece ignorar o "
                                    "parametro 'pagina' para este endpoint/contratacao. Requer estrategia "
                                    "alternativa (ex.: endpoint de detalhe da compra) para obter o total "
                                    "real de itens.") % pagina)

        novos_nesta_pagina = 0
        for it in data:
            k = chave_fn(it)
            if k not in itens_por_chave:
                ordem_chaves.append(k)
                novos_nesta_pagina += 1
            itens_por_chave[k] = it  # upsert: mantem a versao mais recente lida
        paginas_lidas += 1

        if len(data) < tamanho_pagina:
            # pagina parcial: ultima pagina da lista.
            return dict(status="completo", itens=[itens_por_chave[k] for k in ordem_chaves],
                        paginas_lidas=paginas_lidas,
                        motivo="pagina %d devolveu %d < tamanhoPagina=%d; fim da lista" % (
                            pagina, len(data), tamanho_pagina))

        if novos_nesta_pagina == 0:
            # pagina cheia mas sem nenhum item novo (nem igual a pagina 1, ja tratado acima):
            # situacao anomala -- para em vez de repetir indefinidamente.
            return dict(status="servidor_ignora_pagina", itens=[itens_por_chave[k] for k in ordem_chaves],
                        paginas_lidas=paginas_lidas,
                        motivo="pagina %d nao trouxe nenhum item novo; interrompendo para evitar loop" % pagina)

        pagina += 1

    return dict(status="parcial_falha", itens=[itens_por_chave[k] for k in ordem_chaves],
                paginas_lidas=paginas_lidas,
                motivo="limite de seguranca de %d paginas atingido sem terminar a lista" % max_paginas)
