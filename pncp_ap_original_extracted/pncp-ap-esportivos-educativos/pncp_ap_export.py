# -*- coding: utf-8 -*-
"""Exportacao de CSVs, Excel e manifesto a partir do captura.sqlite."""
import json
import os
import sqlite3
from datetime import datetime, date

import re

import pandas as pd
from openpyxl.utils import get_column_letter

_ILEGAL_XML_RE = re.compile(r"[\000-\010\013\014\016-\037]")


def _limpar_para_excel(df):
    # dtype pode ser object (numpy) ou StringDtype (pandas>=2 backend "str"/"pyarrow");
    # aplica em todas as colunas e filtra por isinstance, em vez de depender do dtype exato.
    df = df.copy()
    for col in df.columns:
        df[col] = df[col].map(lambda v: _ILEGAL_XML_RE.sub("", v) if isinstance(v, str) else v)
    return df

PRODUTOS_LISTA = None  # importado sob demanda para evitar ciclo


def _produtos_df():
    import pncp_ap_collector as m
    return pd.DataFrame(m.PRODUTOS, columns=["produto_id", "linha", "produto_nome", "ncm_referencia"])


def carregar(db_path):
    conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    contratacoes = pd.read_sql_query("SELECT * FROM contratacoes", conn)
    itens = pd.read_sql_query("SELECT * FROM itens", conn)
    resultados = pd.read_sql_query("SELECT * FROM resultados", conn)
    checkpoints = pd.read_sql_query("SELECT * FROM checkpoints", conn)
    log_req = pd.read_sql_query("SELECT * FROM log_requisicoes", conn)
    conn.close()
    return contratacoes, itens, resultados, checkpoints, log_req


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


def curva_abc(itens):
    aceitos = itens[itens["classificacao_status"] == "ACEITO"].copy()
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

    def classe(pct_acum):
        if pct_acum <= 80:
            return "A"
        if pct_acum <= 95:
            return "B"
        return "C"

    g["curva_abc"] = g["participacao_acumulada_pct"].apply(classe)
    return g


def compradores(itens):
    aceitos = itens[itens["classificacao_status"] == "ACEITO"].copy()
    g = aceitos.groupby(["cnpj", "orgao_razao_social", "unidade_codigo", "unidade_nome", "municipio_nome", "esfera_classificada"]).agg(
        n_contratacoes=("numero_controle_pncp", "nunique"),
        n_itens=("numero_item", "count"),
        valor_total_estimado=("valor_total_estimado", "sum"),
        valor_homologado_elegivel=("valor_homologado_elegivel", "sum"),
    ).reset_index().sort_values("valor_homologado_elegivel", ascending=False)
    return g


def fornecedores_e_concentracao(itens, resultados):
    aceitos_ids = set(map(tuple, itens[itens["classificacao_status"] == "ACEITO"][["numero_controle_pncp", "numero_item"]].values))
    vazio_concentracao = pd.DataFrame([{"cr4_pct": None, "hhi": None, "n_fornecedores_distintos": 0, "observacao": "sem resultados coletados/elegiveis ate o momento"}])
    if resultados.empty:
        return pd.DataFrame(), pd.DataFrame(), vazio_concentracao

    res = resultados.copy()
    res["chave_item"] = list(zip(res["numero_controle_pncp"], res["numero_item"]))
    res = res[res["chave_item"].isin(aceitos_ids)]
    res["elegivel"] = (res["cancelado_efetivo"] == 0) & (
        res["reserva_remanescente_codigo"].isna() | (res["reserva_remanescente_codigo"] == 1)
    )
    elig = res[res["elegivel"]]
    if elig.empty:
        return pd.DataFrame(), pd.DataFrame(), vazio_concentracao

    g = elig.groupby(["ni_fornecedor", "nome_fornecedor"]).agg(
        valor_total_homologado=("valor_total_homologado", "sum"),
        n_itens_vencidos=("numero_item", "count"),
        n_contratacoes=("numero_controle_pncp", "nunique"),
    ).reset_index().sort_values("valor_total_homologado", ascending=False)
    total = g["valor_total_homologado"].sum()
    g["participacao_pct"] = g["valor_total_homologado"] / total * 100 if total else 0

    cr4 = g["participacao_pct"].head(4).sum() if not g.empty else None
    hhi = (g["participacao_pct"] ** 2).sum() if not g.empty else None

    concentracao = pd.DataFrame([{
        "recorte": "itens ACEITOS, resultados elegiveis (nao cancelados, nao reserva)",
        "n_fornecedores_distintos": g.shape[0],
        "valor_total_base": total,
        "cr4_pct": cr4,
        "hhi": hhi,
        "limitacao": "HHI/CR4 calculados sobre o universo efetivamente coletado; nao representa necessariamente todo o mercado do Amapa, pois depende da cobertura de janelas/resultados no momento da exportacao.",
    }])
    return g, elig, concentracao


def precos_comparaveis(itens, resultados):
    linhas = []
    aceitos = itens[itens["classificacao_status"] == "ACEITO"]
    for (pid, unid), grp in aceitos.groupby(["classificacao_produto_id", "unidade_normalizada"]):
        vals_est = grp["valor_unitario_estimado"].dropna()
        linhas.append(dict(
            produto_id=pid, unidade_normalizada=unid, tipo_preco="estimado",
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
        res = res.merge(aceitos[["numero_controle_pncp", "numero_item", "classificacao_produto_id", "unidade_normalizada"]],
                         on=["numero_controle_pncp", "numero_item"], how="left")
        for (pid, unid), grp in res.groupby(["classificacao_produto_id", "unidade_normalizada"]):
            vals = grp["valor_unitario_homologado"].dropna()
            linhas.append(dict(
                produto_id=pid, unidade_normalizada=unid, tipo_preco="homologado",
                minimo=vals.min() if not vals.empty else None,
                media=vals.mean() if not vals.empty else None,
                mediana=vals.median() if not vals.empty else None,
                maximo=vals.max() if not vals.empty else None,
                n_observacoes=vals.shape[0],
                n_contratacoes=grp["numero_controle_pncp"].nunique(),
            ))
    df = pd.DataFrame(linhas)
    if not df.empty:
        df["aviso"] = df["n_observacoes"].apply(
            lambda n: "atencao: baseado em 1 unico certame, nao usar como referencia robusta" if n == 1 else "")
    return df


def quantidades_por_unidade(itens):
    aceitos = itens[itens["classificacao_status"] == "ACEITO"]
    g = aceitos.groupby(["classificacao_produto_id", "produto_nome", "unidade_original", "unidade_normalizada", "fator_conversao"]).agg(
        quantidade_estimada_total=("quantidade", "sum"),
        quantidade_homologada_total=("quantidade_homologada_total", "sum"),
        n_itens=("numero_item", "count"),
    ).reset_index()
    return g


def sazonalidade(contratacoes, resultados):
    c = contratacoes.copy()
    for col, novo in [("data_publicacao_pncp", "mes_publicacao"), ("data_abertura_proposta", "mes_abertura"),
                       ("data_encerramento_proposta", "mes_encerramento")]:
        c[novo] = pd.to_datetime(c[col], errors="coerce").dt.to_period("M").astype(str)

    pub = c.groupby("mes_publicacao").size().reset_index(name="n_contratacoes_publicadas")
    abert = c.groupby("mes_abertura").size().reset_index(name="n_aberturas")
    enc = c.groupby("mes_encerramento").size().reset_index(name="n_encerramentos")

    if not resultados.empty:
        r = resultados.copy()
        r["mes_resultado"] = pd.to_datetime(r["data_resultado"], errors="coerce").dt.to_period("M").astype(str)
        res_m = r.groupby("mes_resultado").size().reset_index(name="n_resultados")
    else:
        res_m = pd.DataFrame(columns=["mes_resultado", "n_resultados"])

    todos_meses = sorted(set(pub["mes_publicacao"]) | set(abert["mes_abertura"]) | set(enc["mes_encerramento"]) | set(res_m["mes_resultado"]))
    out = pd.DataFrame({"mes": pd.array([m for m in todos_meses if m and m != "NaT"], dtype="string")})
    out = out.merge(pub.rename(columns={"mes_publicacao": "mes"}), on="mes", how="left")
    out = out.merge(abert.rename(columns={"mes_abertura": "mes"}), on="mes", how="left")
    out = out.merge(enc.rename(columns={"mes_encerramento": "mes"}), on="mes", how="left")
    out = out.merge(res_m.rename(columns={"mes_resultado": "mes"}), on="mes", how="left")
    return out.fillna(0)


def pendencias(contratacoes, itens):
    linhas = []
    for _, r in contratacoes[contratacoes["itens_status"] == "falha"].iterrows():
        linhas.append(dict(tipo="itens", chave=r["numero_controle_pncp"], status="falha",
                            detalhe="falha ao coletar itens da contratacao"))
    for _, r in contratacoes[contratacoes["detalhe_status"] == "pendente"].iterrows():
        linhas.append(dict(tipo="detalhe", chave=r["numero_controle_pncp"], status="pendente",
                            detalhe="atualizacao de detalhe pendente; dado anterior preservado"))
    for _, r in itens[itens["resultados_status"] == "falha"].iterrows():
        linhas.append(dict(tipo="resultados", chave="%s#%s" % (r["numero_controle_pncp"], r["numero_item"]),
                            status="falha", detalhe="falha ao coletar resultados do item"))
    return pd.DataFrame(linhas)


DICIONARIO = [
    ("contratacoes", "numero_controle_pncp", "Identificador oficial PNCP da contratacao (chave primaria)"),
    ("contratacoes", "esfera_classificada", "estadual/municipal/federal/indefinida, derivado de orgaoEntidade.esferaId"),
    ("contratacoes", "valor_total_estimado", "Valor estimado da contratacao conforme publicado (nao e homologacao)"),
    ("contratacoes", "valor_total_homologado", "Valor homologado total informado no cabecalho da contratacao (nao confundir com soma de resultados por item)"),
    ("itens", "classificacao_status", "ACEITO / QUARENTENA / EXCLUIDO conforme motor de regras versionado"),
    ("itens", "unidade_normalizada", "Unidade apos tentativa de normalizacao; None quando sem evidencia de fator de conversao"),
    ("itens", "desagio_pct", "100*(1 - valor_homologado_elegivel/base_estimada_pareada); None quando nao ha par comparavel"),
    ("resultados", "cancelado_efetivo", "Avaliacao explicita de cancelamento (situacao+data valida+motivo), nao apenas presenca de string em dataCancelamento"),
    ("resultados", "data_cancelamento_raw", "Valor bruto do campo dataCancelamento, incluindo sentinelas conhecidas como 0001-01-01T00:00:00"),
    ("geral", "srp", "Flag do procedimento de Sistema de Registro de Precos; NAO comprova ata assinada nem consumo realizado"),
    ("geral", "atas_contratos_empenhos", "Nao coletados nesta versao; nao inferir a partir de homologacao"),
]


def exportar_tudo(db_path, export_dir):
    os.makedirs(export_dir, exist_ok=True)
    contratacoes, itens_raw, resultados, checkpoints, log_req = carregar(db_path)
    itens = montar_base_analitica(contratacoes, itens_raw, resultados)

    abc = curva_abc(itens)
    comp = compradores(itens)
    forn, res_eleg, concentracao = fornecedores_e_concentracao(itens, resultados)
    precos = precos_comparaveis(itens, resultados)
    quant = quantidades_por_unidade(itens)
    saz = sazonalidade(contratacoes, resultados)
    pend = pendencias(contratacoes, itens_raw)
    quarentena = itens[itens["classificacao_status"] == "QUARENTENA"]

    # separacao dos recortes de esfera
    contratacoes_estadual_municipal = contratacoes[contratacoes["esfera_classificada"].isin(["estadual", "municipal"])]

    tabelas_csv = {
        "contratacoes.csv": contratacoes,
        "itens.csv": itens,
        "resultados.csv": resultados,
        "quarentena.csv": quarentena,
        "pendencias.csv": pend,
        "cobertura_janelas.csv": checkpoints,
        "produtos_abc.csv": abc,
        "compradores.csv": comp,
        "fornecedores.csv": forn,
        "precos_comparaveis.csv": precos,
        "sazonalidade.csv": saz,
        "contratacoes_AP_ESTADUAL_MUNICIPAL.csv": contratacoes_estadual_municipal,
    }
    for nome, df in tabelas_csv.items():
        caminho = os.path.join(export_dir, nome)
        df.to_csv(caminho, index=False, sep=";", encoding="utf-8-sig")

    manifesto = dict(
        versao_script=__import__("pncp_ap_collector").VERSAO_SCRIPT,
        gerado_em=datetime.now().isoformat(),
        periodo_configurado=dict(inicio="2025-01-01", fim_limite="2026-12-31"),
        periodo_efetivamente_coberto=dict(
            janelas_concluidas=int((checkpoints["status"] == "concluido").sum()) if not checkpoints.empty else 0,
            janelas_sem_registros=int((checkpoints["status"] == "sem_registros").sum()) if not checkpoints.empty else 0,
            janelas_falha=int((checkpoints["status"] == "falha").sum()) if not checkpoints.empty else 0,
            janelas_totais_registradas=int(checkpoints.shape[0]),
        ),
        contagens=dict(
            contratacoes=int(contratacoes.shape[0]),
            contratacoes_estadual_municipal=int(contratacoes_estadual_municipal.shape[0]),
            itens=int(itens.shape[0]),
            itens_aceitos=int((itens["classificacao_status"] == "ACEITO").sum()),
            itens_quarentena=int((itens["classificacao_status"] == "QUARENTENA").sum()),
            itens_excluidos=int((itens["classificacao_status"] == "EXCLUIDO").sum()),
            resultados=int(resultados.shape[0]),
        ),
        limitacoes=[
            "Atas, contratos, empenhos, pagamentos e concorrentes nao sao coletados nesta versao.",
            "srp=true identifica o procedimento, nao comprova ata assinada nem consumo.",
            "CR4/HHI calculados apenas sobre o universo efetivamente coletado ate o momento da exportacao.",
            "Precos com n_observacoes=1 nao devem ser tratados como referencia robusta de mercado.",
            "Classificacao de itens e regra textual versionada (%s), sujeita a revisao manual da quarentena." % __import__("pncp_ap_collector").REGRA_CLASSIFICACAO_VERSAO,
        ],
    )
    with open(os.path.join(export_dir, "manifesto_execucao.json"), "w", encoding="utf-8") as f:
        json.dump(manifesto, f, ensure_ascii=False, indent=2, default=str)

    _gerar_excel(export_dir, dict(
        Cobertura=checkpoints, Contratacoes=contratacoes, Itens=itens, Resultados=resultados,
        Quarentena=quarentena, Pendencias=pend, Produtos_ABC=abc, Compradores=comp,
        Fornecedores=forn, Concentracao=concentracao, Precos_Comparaveis=precos,
        Quantidades=quant, Sazonalidade=saz,
        Dicionario_Dados=pd.DataFrame(DICIONARIO, columns=["tabela", "campo", "descricao"]),
    ), manifesto)

    print("Exportacao concluida em:", export_dir)
    return manifesto


def _gerar_excel(export_dir, abas, manifesto):
    caminho = os.path.join(export_dir, "INTELIGENCIA_MERCADO_AP.xlsx")
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        leiame = pd.DataFrame({
            "campo": list(manifesto.keys()),
            "valor": [json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, (str, int, float)) else v
                      for v in manifesto.values()],
        })
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
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "captura.sqlite")
    out = sys.argv[2] if len(sys.argv) > 2 else "export"
    exportar_tudo(db, out)
