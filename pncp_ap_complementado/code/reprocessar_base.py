# -*- coding: utf-8 -*-
"""
Reprocessa TODOS os itens e resultados ja armazenados no captura.sqlite
com o classificador v2 e a regra de cancelamento v2, preservando os dados
originais (raw_json, quantidades, valores, datas). Nao faz nenhuma
chamada de rede: opera inteiramente sobre o que ja foi capturado.

Tambem cria a tabela de bookkeeping `paginacao_itens`, marcando
explicitamente que a paginacao de itens da v1 NAO comprova completude
(bug de paginacao ausente), com prioridade para as contratacoes com
exatamente 10 itens (suspeita de truncamento na primeira pagina).

Uso:
  python reprocessar_base.py <caminho_db>
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classificador import classificar_item, REGRA_CLASSIFICACAO_VERSAO
from regras_negocio import avaliar_cancelamento

VERSAO_RECLASSIFICACAO = "reprocessamento_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS reclassificacao_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_controle_pncp TEXT, numero_item INTEGER,
        status_antigo TEXT, produto_id_antigo TEXT,
        status_novo TEXT, produto_id_novo TEXT,
        motivo_novo TEXT, processado_em TEXT
    );
    CREATE TABLE IF NOT EXISTS cancelamento_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_controle_pncp TEXT, numero_item INTEGER, sequencial_resultado INTEGER,
        cancelado_antigo INTEGER, cancelado_novo INTEGER,
        motivo_novo TEXT, processado_em TEXT
    );
    CREATE TABLE IF NOT EXISTS paginacao_itens (
        numero_controle_pncp TEXT PRIMARY KEY,
        n_itens_armazenados INTEGER,
        suspeita_truncamento INTEGER,
        status_paginacao TEXT,
        estrategia TEXT,
        tamanho_pagina TEXT,
        versao_coleta TEXT,
        atualizado_em TEXT,
        observacao TEXT
    );
    """)
    db.commit()


def reprocessar_itens(db):
    agora = datetime.now().isoformat()
    cur = db.execute("SELECT numero_controle_pncp, numero_item, descricao, material_ou_servico, "
                      "classificacao_status, classificacao_produto_id FROM itens")
    linhas = cur.fetchall()
    contadores_antes = {}
    contadores_depois = {}
    mudancas = 0
    for row in linhas:
        contadores_antes[row["classificacao_status"]] = contadores_antes.get(row["classificacao_status"], 0) + 1
        novo = classificar_item(row["descricao"], material_ou_servico=row["material_ou_servico"])
        contadores_depois[novo["status"]] = contadores_depois.get(novo["status"], 0) + 1
        mudou = (novo["status"] != row["classificacao_status"]) or (novo["produto_id"] != row["classificacao_produto_id"])
        if mudou:
            mudancas += 1
            db.execute(
                "INSERT INTO reclassificacao_log (numero_controle_pncp,numero_item,status_antigo,produto_id_antigo,"
                "status_novo,produto_id_novo,motivo_novo,processado_em) VALUES (?,?,?,?,?,?,?,?)",
                (row["numero_controle_pncp"], row["numero_item"], row["classificacao_status"],
                 row["classificacao_produto_id"], novo["status"], novo["produto_id"], novo["motivo"], agora),
            )
        db.execute(
            "UPDATE itens SET classificacao_status=?, classificacao_produto_id=?, classificacao_linha=?, "
            "classificacao_motivo=?, classificacao_regra_versao=? WHERE numero_controle_pncp=? AND numero_item=?",
            (novo["status"], novo["produto_id"], novo["linha"], novo["motivo"], REGRA_CLASSIFICACAO_VERSAO,
             row["numero_controle_pncp"], row["numero_item"]),
        )
    db.commit()
    return len(linhas), mudancas, contadores_antes, contadores_depois


def reprocessar_resultados(db):
    agora = datetime.now().isoformat()
    cur = db.execute("SELECT numero_controle_pncp, numero_item, sequencial_resultado, raw_json, cancelado_efetivo "
                      "FROM resultados")
    linhas = cur.fetchall()
    mudancas = 0
    for row in linhas:
        try:
            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        except (TypeError, ValueError):
            raw = {}
        cancelado, motivo = avaliar_cancelamento(raw)
        novo_valor = 1 if cancelado else 0
        if novo_valor != row["cancelado_efetivo"]:
            mudancas += 1
            db.execute(
                "INSERT INTO cancelamento_log (numero_controle_pncp,numero_item,sequencial_resultado,"
                "cancelado_antigo,cancelado_novo,motivo_novo,processado_em) VALUES (?,?,?,?,?,?,?)",
                (row["numero_controle_pncp"], row["numero_item"], row["sequencial_resultado"],
                 row["cancelado_efetivo"], novo_valor, motivo, agora),
            )
        db.execute(
            "UPDATE resultados SET cancelado_efetivo=?, motivo_avaliacao_cancelamento=? "
            "WHERE numero_controle_pncp=? AND numero_item=? AND sequencial_resultado=?",
            (novo_valor, motivo, row["numero_controle_pncp"], row["numero_item"], row["sequencial_resultado"]),
        )
    db.commit()
    return len(linhas), mudancas


def popular_paginacao_itens(db):
    """Registra, para cada contratacao, que a paginacao de itens da v1
    (bug: uma unica chamada sem parametros de pagina) NAO comprova
    completude. As contratacoes com exatamente 10 itens sao marcadas
    como alta prioridade de recuperacao (suspeita de truncamento na
    primeira pagina, ja que 10 e o tamanho tipico de pagina do PNCP)."""
    agora = datetime.now().isoformat()
    cur = db.execute("""
        SELECT c.numero_controle_pncp, c.itens_status, COUNT(i.numero_item) n_itens
        FROM contratacoes c LEFT JOIN itens i ON i.numero_controle_pncp = c.numero_controle_pncp
        GROUP BY c.numero_controle_pncp
    """)
    linhas = cur.fetchall()
    n_suspeitas = 0
    for row in linhas:
        n_itens = row["n_itens"] or 0
        suspeita = 1 if n_itens == 10 else 0
        if suspeita:
            n_suspeitas += 1
        if n_itens == 0:
            status = "sem_registros_ou_nao_verificado"
            obs = "contratacao sem itens armazenados; paginacao real nunca foi executada (bug v1)"
        elif suspeita:
            status = "completude_nao_comprovada_alta_prioridade"
            obs = ("exatamente 10 itens armazenados; tamanho tipico de 1a pagina do endpoint. "
                   "Bug v1: coletor fez 1 chamada sem parametros pagina/tamanhoPagina e assumiu completude. "
                   "Requer nova consulta paginada real (pagina=1,2,3,... ate esgotar) para confirmar total.")
        else:
            status = "completude_nao_comprovada"
            obs = ("%d itens armazenados via chamada unica sem paginacao (bug v1); pode estar completo "
                   "(lista curta) ou truncado. Nao ha evidencia suficiente para afirmar completude "
                   "sem repetir a consulta com paginacao real.") % n_itens
        db.execute(
            "INSERT INTO paginacao_itens (numero_controle_pncp,n_itens_armazenados,suspeita_truncamento,"
            "status_paginacao,estrategia,tamanho_pagina,versao_coleta,atualizado_em,observacao) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(numero_controle_pncp) DO UPDATE SET n_itens_armazenados=excluded.n_itens_armazenados,"
            "suspeita_truncamento=excluded.suspeita_truncamento, status_paginacao=excluded.status_paginacao,"
            "observacao=excluded.observacao, atualizado_em=excluded.atualizado_em",
            (row["numero_controle_pncp"], n_itens, suspeita, status,
             "v1_chamada_unica_sem_paginacao", "desconhecido_provavelmente_10", "v1", agora, obs),
        )
    db.commit()
    return len(linhas), n_suspeitas


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "captura.sqlite")
    db_path = os.path.abspath(db_path)
    print("Reprocessando base:", db_path)
    db = sqlite3.connect(db_path, timeout=30)
    db.row_factory = sqlite3.Row
    _ensure_schema(db)

    print("\n=== Reclassificando itens (v1 -> %s) ===" % REGRA_CLASSIFICACAO_VERSAO)
    total_itens, mud_itens, antes, depois = reprocessar_itens(db)
    print("total itens processados:", total_itens)
    print("itens com mudanca de classificacao:", mud_itens)
    print("distribuicao ANTES:", antes)
    print("distribuicao DEPOIS:", depois)

    print("\n=== Reavaliando cancelamento de resultados ===")
    total_res, mud_res = reprocessar_resultados(db)
    print("total resultados processados:", total_res)
    print("resultados com mudanca de cancelado_efetivo:", mud_res)

    print("\n=== Registrando bookkeeping de paginacao (sem chamadas de rede) ===")
    total_contr, n_suspeitas = popular_paginacao_itens(db)
    print("total contratacoes:", total_contr)
    print("contratacoes com suspeita de truncamento (exatamente 10 itens):", n_suspeitas)

    db.close()
    print("\nReprocessamento concluido.")


if __name__ == "__main__":
    main()
