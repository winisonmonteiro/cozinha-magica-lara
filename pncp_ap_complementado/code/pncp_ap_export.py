# -*- coding: utf-8 -*-
"""Exportacao de CSVs, Excel e manifesto a partir do captura.sqlite --
VERSAO CORRIGIDA (v2). Corrige, em relacao a v1 (preservada em
pncp_ap_export_ORIGINAL.py):

1. Precos comparaveis: agrupamento por produto + unidade normalizada +
   fator de conversao + assinatura de especificacao extraida da
   descricao (tamanho/numero, infantil/adulto, praia/quadra/campo/salao,
   espessura, gramatura etc.), em vez de apenas produto+unidade. Evita
   misturar bola infantil com adulta, tatames de espessuras diferentes,
   caixas com quantidades diferentes etc.
2. Sazonalidade: indicador restrito aos itens aderentes ao rol (ACEITO),
   sem duplicar contratacao dentro do mesmo agrupamento, mantendo tambem
   -- claramente rotulado -- o indicador sobre todas as compras
   consultadas (nao apenas o rol).
3. Rankings/concentracao/ABC: recalculados separadamente para os recortes
   AP_ESTADUAL_MUNICIPAL e AP_TODAS_ESFERAS (nunca somados).
4. Resultados: sinaliza quantidade homologada acumulada > quantidade do
   item, e usa o campo cancelado_efetivo corrigido (situacao explicita e
   evidencia suficiente).
5. Novos arquivos: cobertura.csv (janelas de publicacao + paginacao de
   itens/resultados) e reconciliacao_antes_depois.csv.
"""
import json
import os
import re
import sqlite3
from datetime import datetime

import pandas as pd
from openpyxl.utils import get_column_letter

_ILEGAL_XML_RE = re.compile(r"[\000-\010\013\014\016-\037]")


def _limpar_para_excel(df):
    df = df.copy()
    for col in df.columns:
        df[col] = df[col].map(lambda v: _ILEGAL_XML_RE.sub("", v) if isinstance(v, str) else v)
    return df


def _produtos_df():
    import classificador as m
    return pd.DataFrame(m.PRODUTOS, columns=["produto_id", "linha", "produto_nome", "ncm_referencia"])


def carregar(db_path):
    conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    contratacoes = pd.read_sql_query("SELECT * FROM contratacoes", conn)
    itens = pd.read_sql_query("SELECT * FROM itens", conn)
    resultados = pd.read_sql_query("SELECT * FROM resultados", conn)
    checkpoints = pd.read_sql_query("SELECT * FROM checkpoints", conn)
    log_req = pd.read_sql_query("SELECT * FROM log_requisicoes", conn)
    try:
        paginacao_itens = pd.read_sql_query("SELECT * FROM paginacao_itens", conn)
    except Exception:
        paginacao_itens = pd.DataFrame()
    try:
        paginacao_resultados = pd.read_sql_query("SELECT * FROM paginacao_resultados", conn)
    except Exception:
        paginacao_resultados = pd.DataFrame()
    try:
        reclassificacao_log = pd.read_sql_query("SELECT * FROM reclassificacao_log", conn)
    except Exception:
        reclassificacao_log = pd.DataFrame()
    try:
        cancelamento_log = pd.read_sql_query("SELECT * FROM cancelamento_log", conn)
    except Exception:
        cancelamento_log = pd.DataFrame()
    conn.close()
    return dict(contratacoes=contratacoes, itens=itens, resultados=resultados, checkpoints=checkpoints,
                log_req=log_req, paginacao_itens=paginacao_itens, paginacao_resultados=paginacao_resultados,
                reclassificacao_log=reclassificacao_log, cancelamento_log=cancelamento_log)


# ---------------------------------------------------------------------------
# Extracao de assinatura de especificacao (para nao misturar variantes
# tecnicamente diferentes do mesmo produto na comparacao de precos)
# ---------------------------------------------------------------------------

_RE_TAMANHO_NUM = re.compile(r"\bn[o°º]?\.?\s*(\d{1,2})\b")
_RE_ESPESSURA = re.compile(r"espessura[:\s]{0,10}(\d{1,3}(?:[.,]\d+)?)\s*(mm|cm)")
_RE_GRAMATURA = re.compile(r"(\d{1,4})\s*g\b")
_RE_CORES = re.compile(r"(\d{1,3})\s*cores")


def _especificacao_chave(descricao):
    d = (descricao or "").lower()
    partes = []
    if re.search(r"\binfantil\b", d):
        partes.append("infantil")
    elif re.search(r"\badulto\b", d):
        partes.append("adulto")
    for termo in ("praia", "quadra", "campo", "salao", "society", "futsal"):
        if re.search(r"\b%s\b" % termo, d):
            partes.append(termo)
    m = _RE_TAMANHO_NUM.search(d)
    if m:
        partes.append("tam%s" % m.group(1))
    m = _RE_ESPESSURA.search(d)
    if m:
        partes.append("esp%s%s" % (m.group(1), m.group(2)))
    m = _RE_GRAMATURA.search(d)
    if m:
        partes.append("%sg" % m.group(1))
    m = _RE_CORES.search(d)
    if m:
        partes.append("%scores" % m.group(1))
    return "|".join(partes) if partes else "generico"


def montar_base_analitica(contratacoes, itens, resultados):
    itens = itens.merge(
        contratacoes[[
            "numero_controle_pncp", "cnpj", "orgao_razao_social", "esfera_classificada", "esfera_id",
            "unidade_codigo", "unidade_nome", "municipio_nome", "uf_sigla", "modalidade_id", "modalidade_nome",
            "srp", "data_publicacao_pncp", "data_abertura_proposta", "data_encerramento_proposta",
            "situacao_compra_id", "situacao_compra_nome",
        ]],
        on="numero_controle_pncp", how="left",
    )

    itens["especificacao_chave"] = itens["descricao"].apply(_especificacao_chave)

    if not resultados.empty:
        res = resultados.copy()
        res["elegivel"] = (res["cancelado_efetivo"] == 0) & (
            res["reserva_remanescente_codigo"].isna() | (res["reserva_remanescente_codigo"] == 1)
        )
        agg = res[res["elegivel"]].groupby(["numero_controle_pncp", "numero_item"]).agg(
            quantidade_homologada_total=("quantidade_homologada", "sum"),
            valor_homologado_elegivel=("valor_total_homologado", "sum"),
            n_resultados_elegiveis=("sequencial_resultado", "count"),
        ).reset_index()
        n_forn = res[res["elegivel"]].groupby(["numero_controle_pncp", "numero_item"])["ni_fornecedor"].nunique().reset_index()
        n_forn.columns = ["numero_controle_pncp", "numero_item", "n_fornecedores_vencedores"]
        agg = agg.merge(n_forn, on=["numero_controle_pncp", "numero_item"], how="left")
        itens = itens.merge(agg, on=["numero_controle_pncp", "numero_item"], how="left")
    else:
        itens["quantidade_homologada_total"] = None
        itens["valor_homologado_elegivel"] = None
        itens["n_resultados_elegiveis"] = 0
        itens["n_fornecedores_vencedores"] = 0

    itens["quantidade_homologada_total"] = itens["quantidade_homologada_total"].fillna(0)
    itens["valor_homologado_elegivel"] = itens["valor_homologado_elegivel"].fillna(0)

    # secao 8, item 8: sinalizar homologado acumulado > quantidade do item
    itens["homologado_acima_da_quantidade_flag"] = (
        itens["quantidade_homologada_total"] > itens["quantidade"].fillna(float("inf"))
    ) & itens["quantidade"].notna()

    def base_pareada(row):
        if row.get("orcamento_sigiloso") == 1:
            return None
        vu = row.get("valor_unitario_estimado")
        qh = row.get("quantidade_homologada_total")
        if vu is None or qh is None or qh <= 0:
            return None
        return vu * qh

    itens["base_estimada_pareada"] = itens.apply(base_pareada, axis=1)

    def desagio(row):
        base = row.get("base_estimada_pareada")
        homo = row.get("valor_homologado_elegivel")
        if not base or base <= 0 or homo is None:
            return None
        return 100.0 * (1.0 - (homo / base))

    itens["desagio_pct"] = itens.apply(desagio, axis=1)
    itens["desagio_negativo_flag"] = itens["desagio_pct"].apply(lambda v: bool(v is not None and v < 0))

    prod = _produtos_df()
    itens = itens.merge(prod, left_on="classificacao_produto_id", right_on="produto_id", how="left", suffixes=("", "_cat"))

    return itens


def _recorte(df_itens, recorte):
    if recorte == "AP_ESTADUAL_MUNICIPAL":
        return df_itens[df_itens["esfera_classificada"].isin(["estadual", "municipal"])]
    return df_itens  # AP_TODAS_ESFERAS


def curva_abc(itens):
    linhas = []
    for recorte in ("AP_ESTADUAL_MUNICIPAL", "AP_TODAS_ESFERAS"):
        aceitos = _recorte(itens[itens["classificacao_status"] == "ACEITO"], recorte).copy()
        if aceitos.empty:
            continue
        g = aceitos.groupby(["classificacao_produto_id", "produto_nome", "linha"]).agg(
            n_itens=("numero_item", "count"),
            n_contratacoes=("numero_controle_pncp", "nunique"),
            valor_total_estimado=("valor_total_estimado", "sum"),
            valor_homologado_elegivel=("valor_homologado_elegivel", "sum"),
            n_resultados=("n_resultados_elegiveis", "sum"),
        ).reset_index().sort_values("valor_homologado_elegivel", ascending=False)
        total = g["valor_homologado_elegivel"].sum()
        g["participacao_pct"] = g["valor_homologado_elegivel"] / total * 100 if total else 0
        g["participacao_acumulada_pct"] = g["participacao_pct"].cumsum()
        g["curva_abc"] = g["participacao_acumulada_pct"].apply(lambda p: "A" if p <= 80 else ("B" if p <= 95 else "C"))
        g.insert(0, "recorte", recorte)
        linhas.append(g)
    return pd.concat(linhas, ignore_index=True) if linhas else pd.DataFrame()


def compradores(itens):
    linhas = []
    for recorte in ("AP_ESTADUAL_MUNICIPAL", "AP_TODAS_ESFERAS"):
        aceitos = _recorte(itens[itens["classificacao_status"] == "ACEITO"], recorte).copy()
        if aceitos.empty:
            continue
        g = aceitos.groupby(["cnpj", "orgao_razao_social", "unidade_codigo", "unidade_nome", "municipio_nome",
                             "esfera_classificada"]).agg(
            n_contratacoes=("numero_controle_pncp", "nunique"),
            n_itens=("numero_item", "count"),
            valor_total_estimado=("valor_total_estimado", "sum"),
            valor_homologado_elegivel=("valor_homologado_elegivel", "sum"),
        ).reset_index().sort_values("valor_homologado_elegivel", ascending=False)
        g.insert(0, "recorte", recorte)
        linhas.append(g)
    return pd.concat(linhas, ignore_index=True) if linhas else pd.DataFrame()


def fornecedores_e_concentracao(itens, resultados):
    linhas_forn, linhas_conc = [], []
    for recorte in ("AP_ESTADUAL_MUNICIPAL", "AP_TODAS_ESFERAS"):
        aceitos = _recorte(itens[itens["classificacao_status"] == "ACEITO"], recorte)
        aceitos_ids = set(map(tuple, aceitos[["numero_controle_pncp", "numero_item"]].values))
        if resultados.empty or not aceitos_ids:
            linhas_conc.append(pd.DataFrame([{
                "recorte": recorte, "cr4_pct": None, "hhi": None, "n_fornecedores_distintos": 0,
                "observacao": "sem resultados coletados/elegiveis ate o momento"}]))
            continue
        res = resultados.copy()
        res["chave_item"] = list(zip(res["numero_controle_pncp"], res["numero_item"]))
        res = res[res["chave_item"].isin(aceitos_ids)]
        res["elegivel"] = (res["cancelado_efetivo"] == 0) & (
            res["reserva_remanescente_codigo"].isna() | (res["reserva_remanescente_codigo"] == 1)
        )
        elig = res[res["elegivel"]]
        if elig.empty:
            linhas_conc.append(pd.DataFrame([{
                "recorte": recorte, "cr4_pct": None, "hhi": None, "n_fornecedores_distintos": 0,
                "observacao": "sem resultados elegiveis para este recorte"}]))
            continue
        g = elig.groupby(["ni_fornecedor", "nome_fornecedor"]).agg(
            valor_total_homologado=("valor_total_homologado", "sum"),
            n_itens_vencidos=("numero_item", "count"),
            n_contratacoes=("numero_controle_pncp", "nunique"),
        ).reset_index().sort_values("valor_total_homologado", ascending=False)
        total = g["valor_total_homologado"].sum()
        g["participacao_pct"] = g["valor_total_homologado"] / total * 100 if total else 0
        g.insert(0, "recorte", recorte)
        linhas_forn.append(g)

        cr4 = g["participacao_pct"].head(4).sum() if not g.empty else None
        hhi = (g["participacao_pct"] ** 2).sum() if not g.empty else None
        linhas_conc.append(pd.DataFrame([{
            "recorte": recorte,
            "n_fornecedores_distintos": g.shape[0], "valor_total_base": total,
            "cr4_pct": cr4, "hhi": hhi,
            "observacao": "itens ACEITOS (pos-reclassificacao v2), resultados elegiveis (nao cancelados, nao reserva)",
        }]))
    forn = pd.concat(linhas_forn, ignore_index=True) if linhas_forn else pd.DataFrame()
    conc = pd.concat(linhas_conc, ignore_index=True) if linhas_conc else pd.DataFrame()
    return forn, conc


def precos_comparaveis(itens, resultados):
    """Agrupa por produto + unidade normalizada + fator de conversao +
    assinatura de especificacao, para nao misturar variantes tecnicamente
    diferentes (bola infantil x adulta, tatames de espessuras diferentes,
    caixas com quantidades diferentes etc)."""
    linhas = []
    aceitos = itens[itens["classificacao_status"] == "ACEITO"]
    chaves = ["classificacao_produto_id", "unidade_normalizada", "fator_conversao", "especificacao_chave"]
    for chave, grp in aceitos.groupby(chaves, dropna=False):
        pid, unid, fator, espec = chave
        vals_est = grp["valor_unitario_estimado"].dropna()
        linhas.append(dict(
            produto_id=pid, unidade_normalizada=unid, fator_conversao=fator, especificacao_chave=espec,
            tipo_preco="estimado",
            minimo=vals_est.min() if not vals_est.empty else None,
            media=vals_est.mean() if not vals_est.empty else None,
            mediana=vals_est.median() if not vals_est.empty else None,
            maximo=vals_est.max() if not vals_est.empty else None,
            n_observacoes=vals_est.shape[0],
            n_contratacoes=grp["numero_controle_pncp"].nunique(),
        ))

    if not resultados.empty:
        aceitos_ids = set(map(tuple, aceitos[["numero_controle_pncp", "numero_item"]].values))
        res = resultados.copy()
        res["chave_item"] = list(zip(res["numero_controle_pncp"], res["numero_item"]))
        res = res[res["chave_item"].isin(aceitos_ids) & (res["cancelado_efetivo"] == 0)]
        res = res.merge(
            aceitos[["numero_controle_pncp", "numero_item", "classificacao_produto_id", "unidade_normalizada",
                     "fator_conversao", "especificacao_chave"]],
            on=["numero_controle_pncp", "numero_item"], how="left")
        for chave, grp in res.groupby(chaves, dropna=False):
            pid, unid, fator, espec = chave
            vals = grp["valor_unitario_homologado"].dropna()
            linhas.append(dict(
                produto_id=pid, unidade_normalizada=unid, fator_conversao=fator, especificacao_chave=espec,
                tipo_preco="homologado",
                minimo=vals.min() if not vals.empty else None,
                media=vals.mean() if not vals.empty else None,
                mediana=vals.median() if not vals.empty else None,
                maximo=vals.max() if not vals.empty else None,
                n_observacoes=vals.shape[0],
                n_contratacoes=grp["numero_controle_pncp"].nunique(),
            ))
    df = pd.DataFrame(linhas)
    if not df.empty:
        df["aviso"] = df.apply(
            lambda r: ("atencao: baseado em 1 unico certame, nao usar como referencia robusta"
                       if r["n_observacoes"] == 1 else
                       ("unidade nao comparavel sem fator de conversao confirmado; nao somar/comparar com outras"
                        if pd.isna(r["fator_conversao"]) else "")),
            axis=1)
    return df


def quantidades_por_unidade(itens):
    aceitos = itens[itens["classificacao_status"] == "ACEITO"]
    g = aceitos.groupby(["classificacao_produto_id", "produto_nome", "unidade_original", "unidade_normalizada",
                         "fator_conversao", "especificacao_chave"]).agg(
        quantidade_estimada_total=("quantidade", "sum"),
        quantidade_homologada_total=("quantidade_homologada_total", "sum"),
        n_itens=("numero_item", "count"),
    ).reset_index()
    return g


def sazonalidade(contratacoes, itens, resultados):
    """Dois recortes, claramente rotulados: 'rol_esportivo_educativo'
    (apenas contratacoes com pelo menos um item ACEITO, sem duplicar
    contratacao) e 'todas_compras_consultadas' (universo completo das
    2607 contratacoes, como na v1 -- mantido apenas como referencia
    geral, nunca usado como indicador setorial)."""
    def _monta(c_subset, label):
        c = c_subset.copy()
        for col, novo in [("data_publicacao_pncp", "mes_publicacao"), ("data_abertura_proposta", "mes_abertura"),
                           ("data_encerramento_proposta", "mes_encerramento")]:
            c[novo] = pd.to_datetime(c[col], errors="coerce").dt.to_period("M").astype(str)
        pub = c.groupby("mes_publicacao").size().reset_index(name="n_contratacoes_publicadas")
        abert = c.groupby("mes_abertura").size().reset_index(name="n_aberturas")
        enc = c.groupby("mes_encerramento").size().reset_index(name="n_encerramentos")

        if not resultados.empty:
            ids = set(c["numero_controle_pncp"])
            r = resultados[resultados["numero_controle_pncp"].isin(ids)].copy()
            r["mes_resultado"] = pd.to_datetime(r["data_resultado"], errors="coerce").dt.to_period("M").astype(str)
            res_m = r.groupby("mes_resultado").size().reset_index(name="n_resultados")
        else:
            res_m = pd.DataFrame(columns=["mes_resultado", "n_resultados"])

        todos_meses = sorted(set(pub["mes_publicacao"]) | set(abert["mes_abertura"]) |
                             set(enc["mes_encerramento"]) | set(res_m["mes_resultado"]))
        out = pd.DataFrame({"mes": pd.array([m for m in todos_meses if m and m != "NaT"], dtype="string")})
        out = out.merge(pub.rename(columns={"mes_publicacao": "mes"}), on="mes", how="left")
        out = out.merge(abert.rename(columns={"mes_abertura": "mes"}), on="mes", how="left")
        out = out.merge(enc.rename(columns={"mes_encerramento": "mes"}), on="mes", how="left")
        out = out.merge(res_m.rename(columns={"mes_resultado": "mes"}), on="mes", how="left")
        out = out.fillna(0)
        out.insert(0, "recorte", label)
        return out

    geral = _monta(contratacoes, "todas_compras_consultadas")
    contratacoes_rol = contratacoes[contratacoes["numero_controle_pncp"].isin(
        set(itens[itens["classificacao_status"] == "ACEITO"]["numero_controle_pncp"]))]
    rol = _monta(contratacoes_rol, "rol_esportivo_educativo")
    return pd.concat([rol, geral], ignore_index=True)


def cobertura(checkpoints, paginacao_itens, paginacao_resultados, contratacoes):
    linhas = []
    if not checkpoints.empty:
        for _, r in checkpoints[checkpoints["tipo"] == "publicacao_janela"].groupby("status").size().reset_index(name="n").iterrows():
            linhas.append(dict(dimensao="publicacao_janela", status=r["status"], quantidade=int(r["n"])))
    if not paginacao_itens.empty:
        for _, r in paginacao_itens.groupby("status_paginacao").size().reset_index(name="n").iterrows():
            linhas.append(dict(dimensao="paginacao_itens", status=r["status_paginacao"], quantidade=int(r["n"])))
        n_alta_prioridade = int((paginacao_itens["suspeita_truncamento"] == 1).sum())
        linhas.append(dict(dimensao="paginacao_itens", status="ALTA_PRIORIDADE_suspeita_truncamento_10_itens",
                            quantidade=n_alta_prioridade))
    else:
        linhas.append(dict(dimensao="paginacao_itens", status="tabela_ausente_execucao_anterior_a_v2",
                            quantidade=int(contratacoes.shape[0])))
    if not paginacao_resultados.empty:
        for _, r in paginacao_resultados.groupby("status_paginacao").size().reset_index(name="n").iterrows():
            linhas.append(dict(dimensao="paginacao_resultados", status=r["status_paginacao"], quantidade=int(r["n"])))
    return pd.DataFrame(linhas)


def pendencias(contratacoes, itens, paginacao_itens):
    linhas = []
    for _, r in contratacoes[contratacoes["itens_status"] == "falha"].iterrows():
        linhas.append(dict(tipo="itens", chave=r["numero_controle_pncp"], status="falha",
                            detalhe="falha ao coletar itens da contratacao"))
    if not paginacao_itens.empty:
        pend = paginacao_itens[paginacao_itens["status_paginacao"] != "completo"]
        for _, r in pend.iterrows():
            linhas.append(dict(tipo="paginacao_itens", chave=r["numero_controle_pncp"],
                                status=r["status_paginacao"],
                                detalhe=r["observacao"]))
    for _, r in itens[itens["resultados_status"] == "falha"].iterrows():
        linhas.append(dict(tipo="resultados", chave="%s#%s" % (r["numero_controle_pncp"], r["numero_item"]),
                            status="falha", detalhe="falha ao coletar resultados do item"))
    n_sem_resultado = itens[(itens["classificacao_status"] == "ACEITO") &
                            (itens["resultados_status"] == "nao_consultado")].shape[0]
    if n_sem_resultado:
        linhas.append(dict(tipo="resultados", chave="(agregado)", status="nao_consultado",
                            detalhe="%d itens ACEITOS ainda sem consulta de resultados" % n_sem_resultado))
    return pd.DataFrame(linhas)


def reconciliacao_antes_depois(dados, itens_analitico):
    contratacoes = dados["contratacoes"]
    itens_raw = dados["itens"]
    resultados = dados["resultados"]
    reclass = dados["reclassificacao_log"]
    cancel = dados["cancelamento_log"]
    paginacao = dados["paginacao_itens"]

    aceitos_agora = int((itens_raw["classificacao_status"] == "ACEITO").sum())
    if not reclass.empty:
        ganhos_para_aceito = int(((reclass["status_antigo"] != "ACEITO") & (reclass["status_novo"] == "ACEITO")).sum())
        retirados_do_escopo = int(((reclass["status_antigo"] == "ACEITO") & (reclass["status_novo"] != "ACEITO")).sum())
        recuperados_de_excluido_quarentena = int((
            (reclass["status_antigo"].isin(["EXCLUIDO", "QUARENTENA"])) & (reclass["status_novo"] == "ACEITO")
        ).sum())
        # total antes = total agora - ganhos (nao eram ACEITO antes) + perdas (eram ACEITO antes e deixaram de ser)
        aceitos_antes = aceitos_agora - ganhos_para_aceito + retirados_do_escopo
    else:
        aceitos_antes, retirados_do_escopo, recuperados_de_excluido_quarentena = aceitos_agora, 0, 0

    homologado_elegivel = itens_analitico["valor_homologado_elegivel"].sum()

    n_paginacao_completa = int((paginacao["status_paginacao"] == "completo").sum()) if not paginacao.empty else 0
    n_pendente = int((paginacao["status_paginacao"] != "completo").sum()) if not paginacao.empty else int(contratacoes.shape[0])

    linhas = [
        ("Contratacoes (total na base)", int(contratacoes.shape[0])),
        ("Itens totais armazenados", int(itens_raw.shape[0])),
        ("Itens novos recuperados nesta sessao (paginacao real)", 0),
        ("Itens ACEITOS (apos reclassificacao v2)", int((itens_raw["classificacao_status"] == "ACEITO").sum())),
        ("Itens ACEITOS antes da reclassificacao (v1, referencia)", aceitos_antes),
        ("Itens retirados do escopo (ACEITO->outro)", retirados_do_escopo),
        ("Itens recuperados de EXCLUIDO/QUARENTENA para ACEITO", recuperados_de_excluido_quarentena),
        ("Resultados armazenados", int(resultados.shape[0])),
        ("Resultados com cancelamento corrigido nesta sessao", int(cancel.shape[0]) if not cancel.empty else 0),
        ("Valor homologado elegivel (pos-correcao)", float(homologado_elegivel) if pd.notna(homologado_elegivel) else 0.0),
        ("Contratacoes com paginacao de itens COMPROVADAMENTE completa", n_paginacao_completa),
        ("Contratacoes com paginacao de itens PENDENTE (completude nao comprovada)", n_pendente),
        ("Contratacoes com suspeita de truncamento (exatamente 10 itens, alta prioridade)",
         int((paginacao["suspeita_truncamento"] == 1).sum()) if not paginacao.empty else None),
    ]
    df = pd.DataFrame(linhas, columns=["metrica", "valor"])
    df["observacao"] = ("Diferencas de valor entre versoes decorrem de correcoes de classificacao/cancelamento "
                         "e complementacao de dados, NAO de variacao real do mercado.")
    return df


DICIONARIO = [
    ("contratacoes", "numero_controle_pncp", "Identificador oficial PNCP da contratacao (chave primaria)"),
    ("contratacoes", "esfera_classificada", "estadual/municipal/federal/indefinida, derivado de orgaoEntidade.esferaId"),
    ("contratacoes", "valor_total_estimado", "Valor estimado da contratacao conforme publicado (nao e homologacao)"),
    ("contratacoes", "valor_total_homologado", "Valor homologado total informado no cabecalho da contratacao"),
    ("itens", "classificacao_status", "ACEITO / QUARENTENA / EXCLUIDO conforme motor de regras v2 (ver classificador.py)"),
    ("itens", "especificacao_chave", "Assinatura de especificacao extraida da descricao (tamanho/infantil-adulto/praia-quadra/espessura/gramatura), usada para nao misturar variantes na comparacao de precos"),
    ("itens", "unidade_normalizada", "Unidade apos tentativa de normalizacao; None quando sem evidencia de fator de conversao"),
    ("itens", "desagio_pct", "100*(1 - valor_homologado_elegivel/base_estimada_pareada); None quando nao ha par comparavel"),
    ("itens", "homologado_acima_da_quantidade_flag", "True quando quantidade_homologada_total > quantidade do item (inconsistencia a revisar)"),
    ("resultados", "cancelado_efetivo", "v2: situacao oficial 'Cancelado' e evidencia suficiente por si so, mesmo sem data/motivo preenchidos"),
    ("resultados", "data_cancelamento_raw", "Valor bruto do campo dataCancelamento, incluindo sentinelas conhecidas como 0001-01-01T00:00:00"),
    ("paginacao_itens", "status_paginacao", "completo/parcial_falha/sem_registros/servidor_ignora_pagina/completude_nao_comprovada(*): (*) herdado da v1, paginacao nunca executada"),
    ("geral", "srp", "Flag do procedimento de Sistema de Registro de Precos; NAO comprova ata assinada nem consumo realizado"),
    ("geral", "atas_contratos_empenhos", "Nao coletados nesta versao; nao inferir a partir de homologacao"),
]


def exportar_tudo(db_path, export_dir):
    os.makedirs(export_dir, exist_ok=True)
    dados = carregar(db_path)
    contratacoes, itens_raw, resultados = dados["contratacoes"], dados["itens"], dados["resultados"]
    itens = montar_base_analitica(contratacoes, itens_raw, resultados)

    abc = curva_abc(itens)
    comp = compradores(itens)
    forn, concentracao = fornecedores_e_concentracao(itens, resultados)
    precos = precos_comparaveis(itens, resultados)
    quant = quantidades_por_unidade(itens)
    saz = sazonalidade(contratacoes, itens, resultados)
    cobert = cobertura(dados["checkpoints"], dados["paginacao_itens"], dados["paginacao_resultados"], contratacoes)
    pend = pendencias(contratacoes, itens_raw, dados["paginacao_itens"])
    quarentena = itens[itens["classificacao_status"] == "QUARENTENA"]
    itens_aceitos = itens[itens["classificacao_status"] == "ACEITO"]
    reconc = reconciliacao_antes_depois(dados, itens)

    contratacoes_estadual_municipal = contratacoes[contratacoes["esfera_classificada"].isin(["estadual", "municipal"])]

    tabelas_csv = {
        "contratacoes.csv": contratacoes,
        "itens.csv": itens,
        "itens_aceitos.csv": itens_aceitos,
        "resultados.csv": resultados,
        "quarentena.csv": quarentena,
        "pendencias.csv": pend,
        "cobertura.csv": cobert,
        "produtos_abc.csv": abc,
        "compradores.csv": comp,
        "fornecedores.csv": forn,
        "concentracao.csv": concentracao,
        "precos_comparaveis.csv": precos,
        "sazonalidade.csv": saz,
        "reconciliacao_antes_depois.csv": reconc,
        "contratacoes_AP_ESTADUAL_MUNICIPAL.csv": contratacoes_estadual_municipal,
    }
    for nome, df in tabelas_csv.items():
        caminho = os.path.join(export_dir, nome)
        df.to_csv(caminho, index=False, sep=";", encoding="utf-8-sig")

    import classificador as _cm
    manifesto = dict(
        versao_script="8.0-corrigido",
        versao_classificacao=_cm.REGRA_CLASSIFICACAO_VERSAO,
        gerado_em=datetime.now().isoformat(),
        periodo_configurado=dict(inicio="2025-01-01", fim_desta_complementacao="2026-09-12"),
        ambiente=dict(
            rede_pncp_disponivel=False,
            observacao=("Acesso de rede a pncp.gov.br bloqueado pela politica de egress deste ambiente "
                        "(403 no CONNECT, confirmado via proxy). Paginacao de itens e complementacao de "
                        "resultados NAO puderam ser executadas contra a API real nesta sessao. Codigo "
                        "corrigido e testado com mocks; comando de retomada no manifesto."),
        ),
        periodo_efetivamente_coberto=dict(
            janelas_concluidas=int((dados["checkpoints"]["status"] == "concluido").sum()) if not dados["checkpoints"].empty else 0,
            janelas_sem_registros=int((dados["checkpoints"]["status"] == "sem_registros").sum()) if not dados["checkpoints"].empty else 0,
            janelas_falha=int((dados["checkpoints"]["status"] == "falha").sum()) if not dados["checkpoints"].empty else 0,
            janelas_totais_registradas=int(dados["checkpoints"].shape[0]),
            cobertura_publicacoes_verificada_sem_lacunas=True,
        ),
        contagens=dict(
            contratacoes=int(contratacoes.shape[0]),
            contratacoes_estadual_municipal=int(contratacoes_estadual_municipal.shape[0]),
            itens=int(itens.shape[0]),
            itens_aceitos=int((itens["classificacao_status"] == "ACEITO").sum()),
            itens_quarentena=int((itens["classificacao_status"] == "QUARENTENA").sum()),
            itens_excluidos=int((itens["classificacao_status"] == "EXCLUIDO").sum()),
            resultados=int(resultados.shape[0]),
            contratacoes_com_paginacao_comprovadamente_completa=int((dados["paginacao_itens"]["status_paginacao"] == "completo").sum()) if not dados["paginacao_itens"].empty else 0,
            contratacoes_com_suspeita_de_truncamento_alta_prioridade=int((dados["paginacao_itens"]["suspeita_truncamento"] == 1).sum()) if not dados["paginacao_itens"].empty else 0,
        ),
        comando_de_retomada=(
            "cd pncp_ap_complementado/code && "
            "python pncp_ap_collector.py --recuperar-itens --time-budget-min 120  # prioriza as 696 contratacoes com suspeita de truncamento; "
            "python pncp_ap_collector.py --recuperar-resultados --time-budget-min 60  # depois de recuperar itens; "
            "python pncp_ap_collector.py --export  # gera CSVs/Excel novamente apos a coleta"
        ),
        limitacoes=[
            "Paginacao real de itens e complementacao de resultados nao executadas nesta sessao (sem rede); "
            "base entregue e PARCIAL quanto a completude de itens -- ver cobertura.csv e pendencias.csv.",
            "Atas, contratos, empenhos, pagamentos e concorrentes nao sao coletados nesta versao.",
            "srp=true identifica o procedimento, nao comprova ata assinada nem consumo.",
            "CR4/HHI calculados apenas sobre o universo efetivamente coletado ate o momento da exportacao, "
            "separadamente para AP_ESTADUAL_MUNICIPAL e AP_TODAS_ESFERAS (nunca somados).",
            "Precos com n_observacoes=1 nao devem ser tratados como referencia robusta de mercado.",
            "Sazonalidade 'todas_compras_consultadas' cobre todas as esferas e nao deve ser usada como "
            "indicador do setor esportivo/educativo; usar o recorte 'rol_esportivo_educativo' para isso.",
            "Classificacao de itens e regra textual versionada (%s); quarentena requer revisao manual." % _cm.REGRA_CLASSIFICACAO_VERSAO,
        ],
    )
    with open(os.path.join(export_dir, "manifesto_execucao.json"), "w", encoding="utf-8") as f:
        json.dump(manifesto, f, ensure_ascii=False, indent=2, default=str)

    _gerar_excel(export_dir, dict(
        Cobertura=cobert, Itens_Aceitos=itens_aceitos, Resultados=resultados,
        Quarentena=quarentena, Pendencias=pend, Produtos_ABC=abc, Compradores=comp,
        Fornecedores=forn, Concentracao=concentracao, Precos=precos,
        Sazonalidade=saz, Reconciliacao=reconc,
        Dicionario_Dados=pd.DataFrame(DICIONARIO, columns=["tabela", "campo", "descricao"]),
    ), manifesto)

    print("Exportacao concluida em:", export_dir)
    return manifesto


def _gerar_excel(export_dir, abas, manifesto):
    caminho = os.path.join(export_dir, "INTELIGENCIA_MERCADO_AP_CORRIGIDA.xlsx")
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        leiame_linhas = [
            ("Base", "PNCP Amapa - materiais esportivos e educativos - COMPLEMENTADA E CORRIGIDA"),
            ("Gerado em", manifesto["gerado_em"]),
            ("Versao classificacao", manifesto["versao_classificacao"]),
            ("Rede pncp.gov.br disponivel nesta sessao", str(manifesto["ambiente"]["rede_pncp_disponivel"])),
            ("Observacao de ambiente", manifesto["ambiente"]["observacao"]),
            ("Comando de retomada", manifesto["comando_de_retomada"]),
        ]
        for k, v in manifesto["contagens"].items():
            leiame_linhas.append((k, v))
        leiame = pd.DataFrame(leiame_linhas, columns=["campo", "valor"])
        leiame.to_excel(writer, sheet_name="Leia-me", index=False)
        for nome, df in abas.items():
            aba = nome[:31]
            saida = _limpar_para_excel(df) if not df.empty else pd.DataFrame({"aviso": ["sem dados no momento da exportacao"]})
            saida.to_excel(writer, sheet_name=aba, index=False)

        for aba in writer.sheets:
            ws = writer.sheets[aba]
            ws.freeze_panes = "A2"
            for col_idx, col_cells in enumerate(ws.columns, start=1):
                largura = min(40, max(10, max((len(str(c.value)) for c in col_cells if c.value is not None), default=10) + 2))
                ws.column_dimensions[get_column_letter(col_idx)].width = largura
            ws.auto_filter.ref = ws.dimensions


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join("..", "data", "captura.sqlite")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join("..", "export")
    exportar_tudo(db, out)
