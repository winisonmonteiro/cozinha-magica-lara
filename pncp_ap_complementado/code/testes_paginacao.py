# -*- coding: utf-8 -*-
"""Testes SIMULADOS (mock) da logica de paginacao. Nao fazem nenhuma
chamada de rede -- este ambiente bloqueia acesso a pncp.gov.br. Os dois
casos de tamanho real (211 e 257 itens) reproduzem exatamente os numeros
comprovados na auditoria para 23066905000160-1-000003/2025 e
05995766000177-1-000081/2025."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paginacao_itens import paginar_itens


def _item(n):
    return {"numeroItem": n, "descricao": "item %d" % n}


def _paginador_fixo(total_itens, tamanho_pagina=50, ignora_pagina=False, falha_na_pagina=None,
                    status_falha=500):
    """Fabrica um get_fn simulando um servidor com N itens reais."""
    todos = [_item(n) for n in range(1, total_itens + 1)]

    def get_fn(pagina, tam):
        if falha_na_pagina is not None and pagina == falha_na_pagina:
            return status_falha, None, "falha simulada na pagina %d" % pagina
        if ignora_pagina:
            inicio, fim = 0, min(tam, total_itens)
        else:
            inicio = (pagina - 1) * tam
            fim = min(inicio + tam, total_itens)
        pagina_dados = todos[inicio:fim]
        return 200, pagina_dados, None

    return get_fn


def main():
    falhas = 0

    def check(nome, cond):
        nonlocal falhas
        print("[%s] %s" % ("OK  " if cond else "FALHA", nome))
        if not cond:
            falhas += 1

    # Caso real 1: 211 itens (23066905000160-1-000003/2025)
    r = paginar_itens(_paginador_fixo(211), tamanho_pagina=50)
    check("211 itens reais -> status completo", r["status"] == "completo")
    check("211 itens reais -> todos os 211 recuperados", len(r["itens"]) == 211)
    check("211 itens reais -> 5 paginas lidas (50*4+11)", r["paginas_lidas"] == 5)

    # Caso real 2: 257 itens (05995766000177-1-000081/2025)
    r = paginar_itens(_paginador_fixo(257), tamanho_pagina=50)
    check("257 itens reais -> status completo", r["status"] == "completo")
    check("257 itens reais -> todos os 257 recuperados", len(r["itens"]) == 257)
    check("257 itens reais -> 6 paginas lidas (50*5+7)", r["paginas_lidas"] == 6)

    # Caso: contratacao com exatamente 10 itens reais (as 696 da base) -- deve
    # concluir na primeira pagina, sem precisar de segunda chamada.
    r = paginar_itens(_paginador_fixo(10), tamanho_pagina=50)
    check("10 itens reais -> status completo", r["status"] == "completo")
    check("10 itens reais -> todos os 10 recuperados", len(r["itens"]) == 10)
    check("10 itens reais -> 1 pagina lida", r["paginas_lidas"] == 1)

    # Caso: sem itens (lista vazia na primeira pagina)
    r = paginar_itens(_paginador_fixo(0), tamanho_pagina=50)
    check("0 itens -> status sem_registros", r["status"] == "sem_registros")

    # Caso critico: servidor IGNORA o parametro 'pagina' e sempre devolve a pagina 1
    r = paginar_itens(_paginador_fixo(500, ignora_pagina=True), tamanho_pagina=50)
    check("servidor ignora pagina -> detectado (nao completo, nao trava em loop)",
          r["status"] == "servidor_ignora_pagina")
    check("servidor ignora pagina -> nao inventa itens que nao existem realmente",
          len(r["itens"]) == 50)
    check("servidor ignora pagina -> para rapido (nao itera 100 paginas)", r["paginas_lidas"] <= 2)

    # Caso critico: pagina 3 de 5 falha (erro 500) -- NAO deve marcar como completo,
    # e deve preservar os itens das paginas 1 e 2 ja lidas.
    r = paginar_itens(_paginador_fixo(250, falha_na_pagina=3), tamanho_pagina=50)
    check("falha no meio da paginacao -> status parcial_falha (nunca completo)",
          r["status"] == "parcial_falha")
    check("falha no meio da paginacao -> preserva itens das paginas 1 e 2 (100 itens)",
          len(r["itens"]) == 100)

    # Caso: 404 na primeira pagina -> sem_registros (nao e falha)
    r = paginar_itens(_paginador_fixo(0, falha_na_pagina=1, status_falha=404), tamanho_pagina=50)
    check("404 na pagina 1 -> sem_registros (nao falha)", r["status"] == "sem_registros")

    # Caso: 404 na pagina 2 depois de 50 itens na pagina 1 -> completo (fim da lista)
    r = paginar_itens(_paginador_fixo(50, falha_na_pagina=2, status_falha=404), tamanho_pagina=50)
    check("404 na pagina 2 apos dados -> completo, preserva os 50 itens", r["status"] == "completo")
    check("404 na pagina 2 apos dados -> 50 itens preservados", len(r["itens"]) == 50)

    # Caso: 429 (rate limit) na pagina 2 -> parcial_falha, preserva pagina 1, permite retomada
    r = paginar_itens(_paginador_fixo(150, falha_na_pagina=2, status_falha=429), tamanho_pagina=50)
    check("429 na pagina 2 -> parcial_falha (retomar depois, nao falha definitiva)",
          r["status"] == "parcial_falha")
    check("429 na pagina 2 -> preserva os 50 itens da pagina 1", len(r["itens"]) == 50)

    # Deduplicacao: pagina repetida com itens ja vistos nao deve duplicar
    def get_fn_duplicado(pagina, tam):
        if pagina == 1:
            return 200, [_item(1), _item(2)], None
        if pagina == 2:
            return 200, [_item(2), _item(3)], None  # item 2 repetido
        return 200, [], None

    r = paginar_itens(get_fn_duplicado, tamanho_pagina=2)
    check("deduplicacao por numeroItem entre paginas", len(r["itens"]) == 3)

    print("\n=== %d checks falharam (0 = tudo ok) ===" % falhas)
    if falhas:
        sys.exit(1)


if __name__ == "__main__":
    main()
