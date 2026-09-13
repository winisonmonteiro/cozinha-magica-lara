#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Coletor PNCP - Amapa - Materiais esportivos e educativos - VERSAO CORRIGIDA.

Corrige, em relacao ao coletor original (VERSAO_SCRIPT="7.0", preservado
em pncp_ap_collector_ORIGINAL.py):

1. Paginacao real do endpoint de itens (bug critico: v1 fazia 1 chamada
   sem parametros de pagina e assumia completude). Ver paginacao_itens.py.
2. Classificador de produtos v2 (falsos positivos/negativos corrigidos,
   variantes fora de especificacao em quarentena). Ver classificador.py.
3. Avaliacao de cancelamento v2 (situacao oficial "Cancelado" e evidencia
   suficiente por si so). Ver regras_negocio.py.
4. Priorizacao das contratacoes com suspeita de truncamento (exatamente
   10 itens armazenados pela v1) na recuperacao de itens.
5. Paginacao tambem aplicada ao endpoint de resultados, por seguranca.

Uso:
  python pncp_ap_collector.py --validate
  python pncp_ap_collector.py --recuperar-itens --time-budget-min 60
  python pncp_ap_collector.py --recuperar-resultados --time-budget-min 60
  python pncp_ap_collector.py --run --time-budget-min 60   (publicacoes + itens + resultados)
  python pncp_ap_collector.py --status
  python pncp_ap_collector.py --export

IMPORTANTE: neste ambiente de desenvolvimento, o acesso de rede a
pncp.gov.br esta bloqueado pela politica de egress (403 no CONNECT).
Este script foi validado com testes de logica (mocks, ver
testes_paginacao.py, testes_classificador.py, testes_regras_negocio.py)
mas NAO foi executado contra a API real nesta sessao. Execute em um
ambiente com rede liberada para completar a coleta.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, date, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classificador import classificar_item, REGRA_CLASSIFICACAO_VERSAO, PRODUTOS
from regras_negocio import avaliar_cancelamento, classificar_esfera, strip_acentos
from paginacao_itens import paginar_itens, _chave_resultado, TAMANHO_PAGINA_ITENS

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

VERSAO_SCRIPT = "8.0-corrigido"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "data", "captura.sqlite")
EXPORT_DIR = os.path.join(BASE_DIR, "..", "export")
LOG_DIR = os.path.join(BASE_DIR, "..", "logs")

UF_ALVO = "AP"
MODALIDADES = [6, 7, 8]  # pregao eletronico, pregao presencial, dispensa
DATA_INICIAL = date(2025, 1, 1)
DATA_FINAL_LIMITE = date(2026, 9, 12)  # corte desta complementacao (secao 2 do escopo)
WINDOW_DAYS = 7
PAGE_SIZE_PUBLICACAO = 50

PUB_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
ITENS_URL_TPL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
RESULTADOS_URL_TPL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{item}/resultados"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_TENTATIVAS = 3

PRODUTOS_MAP = {p[0]: p for p in PRODUTOS}

# ---------------------------------------------------------------------------
# Normalizacao de unidades (inalterada da v1 -- nao apontada como buggy)
# ---------------------------------------------------------------------------

UNIDADES_DIRETAS = {
    "unidade": "unidade", "und": "unidade", "un": "unidade", "peca": "unidade", "pecas": "unidade",
    "resma": "resma", "par": "par", "pares": "par", "kg": "kg", "quilograma": "kg", "quilogramas": "kg",
    "litro": "litro", "litros": "litro", "metro": "metro", "metros": "metro",
    "conjunto": "conjunto", "jogo": "jogo", "kit": "kit", "caixa": "caixa", "pacote": "pacote",
    "folha": "folha", "folhas": "folha", "bloco": "bloco", "rolo": "rolo", "frasco": "frasco",
    "tubo": "tubo", "grama": "grama", "gramas": "grama",
}


def normalizar_unidade(unidade_original, descricao):
    import re
    u_norm = strip_acentos(unidade_original or "").strip()
    base = UNIDADES_DIRETAS.get(u_norm)
    if base in ("unidade", "resma", "par", "kg", "litro", "metro", "folha", "grama"):
        return base, 1.0, "unidade original ja comparavel (sem conversao)"
    desc_norm = strip_acentos(descricao or "")
    m = re.search(r"(caixa|pacote|fardo)[^\d]{0,15}(\d{1,4})\s*(resmas?|unidades?|folhas?|pecas?)", desc_norm)
    if m and base in ("caixa", "pacote"):
        qtd = float(m.group(2))
        alvo = m.group(3)
        alvo_norm = "resma" if "resma" in alvo else ("folha" if "folha" in alvo else "unidade")
        return alvo_norm, qtd, "fator extraido da descricao: '%s'" % m.group(0)
    if base:
        return base, None, "unidade preservada sem fator de conversao confirmado (nao somar com outras unidades)"
    return (unidade_original or "NAO_INFORMADA"), None, "unidade nao reconhecida no dicionario; preservada como original"


# ---------------------------------------------------------------------------
# Schema (identico a v1 + tabelas novas de bookkeeping de paginacao/log)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contratacoes (
    numero_controle_pncp TEXT PRIMARY KEY,
    cnpj TEXT, ano_compra INTEGER, sequencial_compra INTEGER,
    orgao_razao_social TEXT, esfera_id TEXT, poder_id TEXT, esfera_classificada TEXT,
    unidade_codigo TEXT, unidade_nome TEXT, municipio_nome TEXT, codigo_ibge TEXT, uf_sigla TEXT,
    modalidade_id INTEGER, modalidade_nome TEXT, srp INTEGER,
    numero_compra TEXT, processo TEXT, objeto_compra TEXT,
    data_publicacao_pncp TEXT, data_inclusao TEXT, data_atualizacao TEXT, data_atualizacao_global TEXT,
    data_abertura_proposta TEXT, data_encerramento_proposta TEXT,
    valor_total_estimado REAL, valor_total_homologado REAL,
    orcamento_sigiloso_codigo INTEGER, orcamento_sigiloso_descricao TEXT,
    situacao_compra_id INTEGER, situacao_compra_nome TEXT,
    link_sistema_origem TEXT,
    itens_status TEXT DEFAULT 'nao_consultado',
    detalhe_status TEXT DEFAULT 'nao_consultado',
    detalhe_atualizado_em TEXT,
    raw_json TEXT,
    captured_at TEXT
);

CREATE TABLE IF NOT EXISTS itens (
    numero_controle_pncp TEXT, numero_item INTEGER,
    descricao TEXT, material_ou_servico TEXT,
    quantidade REAL, unidade_original TEXT, unidade_normalizada TEXT, fator_conversao REAL, evidencia_conversao TEXT,
    valor_unitario_estimado REAL, valor_total_estimado REAL, orcamento_sigiloso INTEGER,
    situacao_compra_item_id INTEGER, situacao_compra_item_nome TEXT, tem_resultado INTEGER,
    ncm_nbs_codigo TEXT, catalogo_codigo_item TEXT, categoria_item_catalogo TEXT,
    data_inclusao TEXT, data_atualizacao TEXT,
    classificacao_status TEXT, classificacao_produto_id TEXT, classificacao_linha TEXT,
    classificacao_motivo TEXT, classificacao_regra_versao TEXT,
    resultados_status TEXT DEFAULT 'nao_consultado',
    raw_json TEXT, captured_at TEXT,
    PRIMARY KEY (numero_controle_pncp, numero_item)
);

CREATE TABLE IF NOT EXISTS resultados (
    numero_controle_pncp TEXT, numero_item INTEGER, sequencial_resultado INTEGER,
    ni_fornecedor TEXT, nome_fornecedor TEXT, tipo_pessoa TEXT, porte_fornecedor TEXT,
    quantidade_homologada REAL, valor_unitario_homologado REAL, valor_total_homologado REAL,
    situacao_resultado_id INTEGER, situacao_resultado_nome TEXT,
    data_resultado TEXT, data_cancelamento_raw TEXT, cancelado_efetivo INTEGER, motivo_avaliacao_cancelamento TEXT,
    motivo_cancelamento_raw TEXT, reserva_remanescente_codigo INTEGER, reserva_remanescente_nome TEXT,
    ordem_classificacao_srp INTEGER,
    raw_json TEXT, captured_at TEXT,
    PRIMARY KEY (numero_controle_pncp, numero_item, sequencial_resultado)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    tipo TEXT, chave TEXT, status TEXT, detalhe TEXT, atualizado_em TEXT,
    PRIMARY KEY (tipo, chave)
);

CREATE TABLE IF NOT EXISTS ritmo (
    id INTEGER PRIMARY KEY CHECK (id=1),
    delay_atual_seg REAL, pausa_global_ate TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS log_requisicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, kind TEXT, url TEXT, status INTEGER, tempo_seg REAL, tentativa INTEGER, erro TEXT, veio_cache INTEGER
);

CREATE TABLE IF NOT EXISTS execucoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT, fim TEXT, versao_script TEXT, data_inicial TEXT, data_final TEXT, observacoes TEXT
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

CREATE TABLE IF NOT EXISTS paginacao_resultados (
    numero_controle_pncp TEXT, numero_item INTEGER,
    status_paginacao TEXT, paginas_lidas INTEGER, tamanho_pagina INTEGER,
    versao_coleta TEXT, atualizado_em TEXT, observacao TEXT,
    PRIMARY KEY (numero_controle_pncp, numero_item)
);

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
"""


def conectar_db(path=DB_PATH, somente_leitura=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if somente_leitura:
        uri = "file:%s?mode=ro" % path.replace("\\", "/")
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _sql_safe(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def get_checkpoint(db, tipo, chave):
    cur = db.execute("SELECT * FROM checkpoints WHERE tipo=? AND chave=?", (tipo, chave))
    return cur.fetchone()


def set_checkpoint(db, tipo, chave, status, detalhe=""):
    db.execute(
        "INSERT INTO checkpoints (tipo,chave,status,detalhe,atualizado_em) VALUES (?,?,?,?,?) "
        "ON CONFLICT(tipo,chave) DO UPDATE SET status=excluded.status, detalhe=excluded.detalhe, atualizado_em=excluded.atualizado_em",
        (tipo, chave, status, detalhe, datetime.now().isoformat()),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Cliente HTTP com ritmo adaptativo (logica inalterada da v1; ja atendia
# aos requisitos de sessao reutilizavel, Retry-After e pausa global em 429)
# ---------------------------------------------------------------------------

class Ritmo:
    def __init__(self, db):
        self.db = db
        row = db.execute("SELECT * FROM ritmo WHERE id=1").fetchone()
        if row:
            self.delay = row["delay_atual_seg"] or 1.5
            self.pausa_global_ate = row["pausa_global_ate"]
        else:
            self.delay = 1.5
            self.pausa_global_ate = None
            db.execute("INSERT INTO ritmo (id,delay_atual_seg,pausa_global_ate,updated_at) VALUES (1,?,?,?)",
                       (self.delay, None, datetime.now().isoformat()))
            db.commit()
        self.delay_min = 0.8
        self.delay_max = 30.0
        self.sucessos_seguidos = 0

    def salvar(self):
        self.db.execute("UPDATE ritmo SET delay_atual_seg=?, pausa_global_ate=?, updated_at=? WHERE id=1",
                         (self.delay, self.pausa_global_ate, datetime.now().isoformat()))
        self.db.commit()

    def respeitar_pausa_global(self):
        if self.pausa_global_ate:
            ate = datetime.fromisoformat(self.pausa_global_ate)
            agora = datetime.now()
            if agora < ate:
                espera = (ate - agora).total_seconds()
                print("  [ritmo] pausa global ativa por 429; aguardando %.0fs" % espera)
                time.sleep(espera)
            self.pausa_global_ate = None
            self.salvar()

    def registrar_sucesso(self):
        self.sucessos_seguidos += 1
        if self.sucessos_seguidos >= 8 and self.delay > self.delay_min:
            self.delay = max(self.delay_min, self.delay * 0.85)
            self.sucessos_seguidos = 0
            self.salvar()

    def registrar_429(self, retry_after_seg):
        self.sucessos_seguidos = 0
        self.delay = min(self.delay_max, max(self.delay * 2, 5.0))
        pausa = retry_after_seg if retry_after_seg else 60
        self.pausa_global_ate = (datetime.now() + timedelta(seconds=pausa)).isoformat()
        self.salvar()

    def esperar(self):
        time.sleep(self.delay)


class PncpClient:
    def __init__(self, db):
        self.db = db
        self.ritmo = Ritmo(db)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "pncp-ap-esportivos-educativos/%s" % VERSAO_SCRIPT})
        self.contagem = {"publicacao": 0, "itens": 0, "resultados": 0, "detalhe": 0}

    def _log(self, kind, url, status, tempo, tentativa, erro):
        self.db.execute(
            "INSERT INTO log_requisicoes (timestamp,kind,url,status,tempo_seg,tentativa,erro,veio_cache) VALUES (?,?,?,?,?,?,?,0)",
            (datetime.now().isoformat(), kind, url, status, tempo, tentativa, erro),
        )
        self.db.commit()

    def get(self, kind, url, params=None):
        """Retorna (status_code, json_ou_none, erro_ou_none)."""
        self.ritmo.respeitar_pausa_global()
        ultimo_erro = None
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            self.ritmo.esperar()
            t0 = time.time()
            try:
                resp = self.session.get(url, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=False)
                tempo = time.time() - t0
                self.contagem[kind] = self.contagem.get(kind, 0) + 1
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    retry_after_seg = float(retry_after) if retry_after and retry_after.isdigit() else None
                    self._log(kind, url, 429, tempo, tentativa, "rate limited")
                    self.ritmo.registrar_429(retry_after_seg)
                    return 429, None, "rate limited (429); pausa global aplicada, repetir depois"
                if resp.status_code == 204:
                    self._log(kind, url, 204, tempo, tentativa, None)
                    self.ritmo.registrar_sucesso()
                    corpo_vazio = {"data": [], "totalRegistros": 0, "totalPaginas": 1} if kind == "publicacao" else []
                    return 200, corpo_vazio, None
                if resp.status_code == 200:
                    self._log(kind, url, 200, tempo, tentativa, None)
                    self.ritmo.registrar_sucesso()
                    try:
                        return 200, resp.json(), None
                    except Exception as e:
                        return 200, None, "erro parse json: %s" % e
                if resp.status_code == 404:
                    self._log(kind, url, 404, tempo, tentativa, "not found")
                    return 404, None, "404"
                self._log(kind, url, resp.status_code, tempo, tentativa, "status inesperado")
                ultimo_erro = "status %s" % resp.status_code
            except requests.exceptions.ReadTimeout:
                tempo = time.time() - t0
                self._log(kind, url, None, tempo, tentativa, "read timeout")
                ultimo_erro = "read timeout"
            except requests.exceptions.ConnectionError as e:
                tempo = time.time() - t0
                self._log(kind, url, None, tempo, tentativa, "connection error: %s" % e)
                ultimo_erro = "connection error"
            except Exception as e:
                tempo = time.time() - t0
                self._log(kind, url, None, tempo, tentativa, "erro: %s" % e)
                ultimo_erro = str(e)
        return None, None, ultimo_erro


# ---------------------------------------------------------------------------
# Coleta: publicacoes (logica de paginacao ja estava correta na v1;
# verificado localmente que as 267 janelas cobrem 2025-01-01..2026-09-12
# sem lacunas -- nao alterado, apenas preservado)
# ---------------------------------------------------------------------------

def iter_janelas(data_ini, data_fim, dias=WINDOW_DAYS):
    cur = data_ini
    while cur <= data_fim:
        fim = min(cur + timedelta(days=dias - 1), data_fim)
        yield cur, fim
        cur = fim + timedelta(days=1)


def upsert_contratacao(db, r, modalidade_fallback=None):
    orgao = r.get("orgaoEntidade") or {}
    unidade = r.get("unidadeOrgao") or {}
    numero_controle = r.get("numeroControlePNCP")
    if not numero_controle:
        return
    esfera_id = orgao.get("esferaId")
    poder_id = orgao.get("poderId")
    row = dict(
        numero_controle_pncp=numero_controle,
        cnpj=orgao.get("cnpj"),
        ano_compra=r.get("anoCompra"),
        sequencial_compra=r.get("sequencialCompra"),
        orgao_razao_social=orgao.get("razaoSocial"),
        esfera_id=esfera_id,
        poder_id=poder_id,
        esfera_classificada=classificar_esfera(esfera_id, poder_id),
        unidade_codigo=unidade.get("codigoUnidade"),
        unidade_nome=unidade.get("nomeUnidade"),
        municipio_nome=unidade.get("municipioNome"),
        codigo_ibge=unidade.get("codigoIbge"),
        uf_sigla=unidade.get("ufSigla"),
        modalidade_id=r.get("modalidadeId", modalidade_fallback),
        modalidade_nome=r.get("modalidadeNome"),
        srp=1 if r.get("srp") else 0,
        numero_compra=r.get("numeroCompra"),
        processo=r.get("processo"),
        objeto_compra=r.get("objetoCompra"),
        data_publicacao_pncp=r.get("dataPublicacaoPncp"),
        data_inclusao=r.get("dataInclusao"),
        data_atualizacao=r.get("dataAtualizacao"),
        data_atualizacao_global=r.get("dataAtualizacaoGlobal"),
        data_abertura_proposta=r.get("dataAberturaProposta"),
        data_encerramento_proposta=r.get("dataEncerramentoProposta"),
        valor_total_estimado=r.get("valorTotalEstimado"),
        valor_total_homologado=r.get("valorTotalHomologado"),
        orcamento_sigiloso_codigo=r.get("orcamentoSigilosoCodigo"),
        orcamento_sigiloso_descricao=r.get("orcamentoSigilosoDescricao"),
        situacao_compra_id=r.get("situacaoCompraId"),
        situacao_compra_nome=r.get("situacaoCompraNome"),
        link_sistema_origem=r.get("linkSistemaOrigem"),
        raw_json=json.dumps(r, ensure_ascii=False),
        captured_at=datetime.now().isoformat(),
    )
    if row["uf_sigla"] and row["uf_sigla"] != UF_ALVO:
        return
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    update_cols = ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "numero_controle_pncp")
    sql = "INSERT INTO contratacoes (%s) VALUES (%s) ON CONFLICT(numero_controle_pncp) DO UPDATE SET %s" % (
        ",".join(cols), placeholders, update_cols,
    )
    db.execute(sql, [_sql_safe(row[c]) for c in cols])


def coletar_publicacoes(client, db, data_final_atual, log_progresso=True):
    for modalidade in MODALIDADES:
        for ini, fim in iter_janelas(DATA_INICIAL, data_final_atual):
            chave = "%s|%s|%s" % (modalidade, ini.isoformat(), fim.isoformat())
            cp = get_checkpoint(db, "publicacao_janela", chave)
            if cp and cp["status"] in ("concluido", "sem_registros"):
                continue
            pagina = 1
            total_paginas = 1
            total_registros_janela = 0
            falhou = False
            while pagina <= total_paginas:
                params = {
                    "dataInicial": ini.strftime("%Y%m%d"),
                    "dataFinal": fim.strftime("%Y%m%d"),
                    "codigoModalidadeContratacao": modalidade,
                    "uf": UF_ALVO,
                    "pagina": pagina,
                    "tamanhoPagina": PAGE_SIZE_PUBLICACAO,
                }
                status, data, erro = client.get("publicacao", PUB_URL, params=params)
                if status == 429:
                    falhou = True
                    break
                if status != 200 or data is None:
                    set_checkpoint(db, "publicacao_janela", chave, "falha", "pagina %s: %s" % (pagina, erro))
                    falhou = True
                    break
                registros = data.get("data", []) or []
                for r in registros:
                    upsert_contratacao(db, r, modalidade)
                db.commit()
                total_registros_janela += len(registros)
                total_paginas = data.get("totalPaginas", 1) or 1
                if log_progresso:
                    print("  [publicacoes] mod=%s janela=%s..%s pagina=%s/%s registros_pagina=%s" % (
                        modalidade, ini, fim, pagina, total_paginas, len(registros)))
                pagina += 1
            if not falhou:
                status_final = "concluido" if total_registros_janela > 0 else "sem_registros"
                set_checkpoint(db, "publicacao_janela", chave, status_final, "total=%s" % total_registros_janela)
            yield


# ---------------------------------------------------------------------------
# Coleta: itens -- CORRIGIDA com paginacao real
# ---------------------------------------------------------------------------

def upsert_item(db, numero_controle, r):
    numero_item = r.get("numeroItem")
    unidade_norm, fator, evidencia = normalizar_unidade(r.get("unidadeMedida"), r.get("descricao"))
    cls = classificar_item(r.get("descricao"), material_ou_servico=r.get("materialOuServico"))
    row = dict(
        numero_controle_pncp=numero_controle,
        numero_item=numero_item,
        descricao=r.get("descricao"),
        material_ou_servico=r.get("materialOuServico"),
        quantidade=r.get("quantidade"),
        unidade_original=r.get("unidadeMedida"),
        unidade_normalizada=unidade_norm,
        fator_conversao=fator,
        evidencia_conversao=evidencia,
        valor_unitario_estimado=r.get("valorUnitarioEstimado"),
        valor_total_estimado=r.get("valorTotal"),
        orcamento_sigiloso=1 if r.get("orcamentoSigiloso") else 0,
        situacao_compra_item_id=r.get("situacaoCompraItem"),
        situacao_compra_item_nome=r.get("situacaoCompraItemNome"),
        tem_resultado=1 if r.get("temResultado") else 0,
        ncm_nbs_codigo=r.get("ncmNbsCodigo"),
        catalogo_codigo_item=r.get("catalogoCodigoItem"),
        categoria_item_catalogo=r.get("categoriaItemCatalogo"),
        data_inclusao=r.get("dataInclusao"),
        data_atualizacao=r.get("dataAtualizacao"),
        classificacao_status=cls["status"],
        classificacao_produto_id=cls["produto_id"],
        classificacao_linha=cls["linha"],
        classificacao_motivo=cls["motivo"],
        classificacao_regra_versao=cls["regra_versao"],
        raw_json=json.dumps(r, ensure_ascii=False),
        captured_at=datetime.now().isoformat(),
    )
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    update_cols = ",".join("%s=excluded.%s" % (c, c) for c in cols if c not in ("numero_controle_pncp", "numero_item"))
    sql = "INSERT INTO itens (%s) VALUES (%s) ON CONFLICT(numero_controle_pncp,numero_item) DO UPDATE SET %s" % (
        ",".join(cols), placeholders, update_cols,
    )
    db.execute(sql, [_sql_safe(row[c]) for c in cols])
    return cls["status"]


def _fila_priorizada_itens(db, limite=None):
    """Contratacoes ordenadas por prioridade de recuperacao: primeiro as com
    suspeita_truncamento=1 (exatamente 10 itens armazenados pela v1), depois
    as demais ainda sem paginacao comprovadamente completa."""
    sql = """
        SELECT c.numero_controle_pncp, c.cnpj, c.ano_compra, c.sequencial_compra,
               COALESCE(p.suspeita_truncamento, 0) suspeita_truncamento,
               COALESCE(p.status_paginacao, 'nunca_verificado') status_paginacao
        FROM contratacoes c
        LEFT JOIN paginacao_itens p ON p.numero_controle_pncp = c.numero_controle_pncp
        WHERE COALESCE(p.status_paginacao, '') NOT IN ('completo')
        ORDER BY suspeita_truncamento DESC, c.numero_controle_pncp
    """
    if limite:
        sql += " LIMIT %d" % limite
    return db.execute(sql).fetchall()


def coletar_itens_paginado(client, db, limite=None, tamanho_pagina=TAMANHO_PAGINA_ITENS):
    """Recupera itens com paginacao real, priorizando as contratacoes com
    suspeita de truncamento. Nunca marca como completa uma contratacao cuja
    paginacao falhou parcialmente; sempre preserva itens ja salvos."""
    linhas = _fila_priorizada_itens(db, limite)
    for row in linhas:
        numero_controle = row["numero_controle_pncp"]
        cnpj, ano, seq = row["cnpj"], row["ano_compra"], row["sequencial_compra"]
        url = ITENS_URL_TPL.format(cnpj=cnpj, ano=ano, seq=seq)

        def get_fn(pagina, tam, _url=url):
            status, data, erro = client.get("itens", _url, params={"pagina": pagina, "tamanhoPagina": tam})
            return status, data, erro

        resultado = paginar_itens(get_fn, tamanho_pagina=tamanho_pagina)

        aceitos = quarentena = excluidos = 0
        for it in resultado["itens"]:
            st = upsert_item(db, numero_controle, it)
            if st == "ACEITO":
                aceitos += 1
            elif st == "QUARENTENA":
                quarentena += 1
            else:
                excluidos += 1

        itens_status_map = {
            "completo": "ok", "sem_registros": "sem_registros",
            "parcial_falha": "falha", "servidor_ignora_pagina": "falha",
        }
        db.execute("UPDATE contratacoes SET itens_status=? WHERE numero_controle_pncp=?",
                   (itens_status_map.get(resultado["status"], "falha"), numero_controle))
        db.execute(
            "INSERT INTO paginacao_itens (numero_controle_pncp,n_itens_armazenados,suspeita_truncamento,"
            "status_paginacao,estrategia,tamanho_pagina,versao_coleta,atualizado_em,observacao) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(numero_controle_pncp) DO UPDATE SET n_itens_armazenados=excluded.n_itens_armazenados,"
            "suspeita_truncamento=excluded.suspeita_truncamento, status_paginacao=excluded.status_paginacao,"
            "estrategia=excluded.estrategia, tamanho_pagina=excluded.tamanho_pagina,"
            "versao_coleta=excluded.versao_coleta, atualizado_em=excluded.atualizado_em,"
            "observacao=excluded.observacao",
            (numero_controle, len(resultado["itens"]), 0, resultado["status"],
             "v2_paginacao_real", str(tamanho_pagina), VERSAO_SCRIPT, datetime.now().isoformat(),
             resultado["motivo"]),
        )
        db.commit()
        print("  [itens] %s -> status=%s paginas=%s itens=%s (aceitos=%s quarentena=%s excluidos=%s) | %s" % (
            numero_controle, resultado["status"], resultado["paginas_lidas"], len(resultado["itens"]),
            aceitos, quarentena, excluidos, resultado["motivo"]))
        yield resultado


# ---------------------------------------------------------------------------
# Coleta: resultados -- paginacao aplicada por seguranca (secao 8, item 5)
# ---------------------------------------------------------------------------

def upsert_resultado(db, numero_controle, numero_item, r):
    cancelado, motivo_avaliacao = avaliar_cancelamento(r)
    reserva = r.get("reservaRemanescente") or {}
    row = dict(
        numero_controle_pncp=numero_controle,
        numero_item=numero_item,
        sequencial_resultado=r.get("sequencialResultado"),
        ni_fornecedor=r.get("niFornecedor"),
        nome_fornecedor=r.get("nomeRazaoSocialFornecedor"),
        tipo_pessoa=r.get("tipoPessoa"),
        porte_fornecedor=r.get("porteFornecedorNome"),
        quantidade_homologada=r.get("quantidadeHomologada"),
        valor_unitario_homologado=r.get("valorUnitarioHomologado"),
        valor_total_homologado=r.get("valorTotalHomologado"),
        situacao_resultado_id=r.get("situacaoCompraItemResultadoId"),
        situacao_resultado_nome=r.get("situacaoCompraItemResultadoNome"),
        data_resultado=r.get("dataResultado"),
        data_cancelamento_raw=r.get("dataCancelamento"),
        cancelado_efetivo=1 if cancelado else 0,
        motivo_avaliacao_cancelamento=motivo_avaliacao,
        motivo_cancelamento_raw=r.get("motivoCancelamento"),
        reserva_remanescente_codigo=reserva.get("codigo"),
        reserva_remanescente_nome=reserva.get("nome"),
        ordem_classificacao_srp=r.get("ordemClassificacaoSrp"),
        raw_json=json.dumps(r, ensure_ascii=False),
        captured_at=datetime.now().isoformat(),
    )
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    update_cols = ",".join("%s=excluded.%s" % (c, c) for c in cols if c not in ("numero_controle_pncp", "numero_item", "sequencial_resultado"))
    sql = "INSERT INTO resultados (%s) VALUES (%s) ON CONFLICT(numero_controle_pncp,numero_item,sequencial_resultado) DO UPDATE SET %s" % (
        ",".join(cols), placeholders, update_cols,
    )
    db.execute(sql, [_sql_safe(row[c]) for c in cols])


def coletar_resultados_paginado(client, db, limite=None, tamanho_pagina=TAMANHO_PAGINA_ITENS):
    """So consulta resultados de itens ACEITOS (apos reclassificacao v2) que
    ainda nao foram consultados ou cuja consulta anterior falhou."""
    sql = (
        "SELECT i.numero_controle_pncp, i.numero_item, c.cnpj, c.ano_compra, c.sequencial_compra "
        "FROM itens i JOIN contratacoes c ON c.numero_controle_pncp = i.numero_controle_pncp "
        "WHERE i.classificacao_status='ACEITO' AND i.resultados_status IN ('nao_consultado','falha') "
    )
    if limite:
        sql += "LIMIT %d" % limite
    linhas = db.execute(sql).fetchall()
    for row in linhas:
        numero_controle, numero_item = row["numero_controle_pncp"], row["numero_item"]
        url = RESULTADOS_URL_TPL.format(cnpj=row["cnpj"], ano=row["ano_compra"], seq=row["sequencial_compra"], item=numero_item)

        def get_fn(pagina, tam, _url=url):
            status, data, erro = client.get("resultados", _url, params={"pagina": pagina, "tamanhoPagina": tam})
            return status, data, erro

        resultado = paginar_itens(get_fn, tamanho_pagina=tamanho_pagina, chave_fn=_chave_resultado)

        for r in resultado["itens"]:
            upsert_resultado(db, numero_controle, numero_item, r)

        status_map = {"completo": "ok", "sem_registros": "sem_registros",
                      "parcial_falha": "falha", "servidor_ignora_pagina": "falha"}
        db.execute("UPDATE itens SET resultados_status=? WHERE numero_controle_pncp=? AND numero_item=?",
                   (status_map.get(resultado["status"], "falha"), numero_controle, numero_item))
        db.execute(
            "INSERT INTO paginacao_resultados (numero_controle_pncp,numero_item,status_paginacao,paginas_lidas,"
            "tamanho_pagina,versao_coleta,atualizado_em,observacao) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(numero_controle_pncp,numero_item) DO UPDATE SET status_paginacao=excluded.status_paginacao,"
            "paginas_lidas=excluded.paginas_lidas, atualizado_em=excluded.atualizado_em, observacao=excluded.observacao",
            (numero_controle, numero_item, resultado["status"], resultado["paginas_lidas"], tamanho_pagina,
             VERSAO_SCRIPT, datetime.now().isoformat(), resultado["motivo"]),
        )
        db.commit()
        yield resultado


# ---------------------------------------------------------------------------
# Orquestracao e CLI
# ---------------------------------------------------------------------------

def imprimir_status(db=None):
    fechar = False
    if db is None:
        db = conectar_db()
        fechar = True
    print("\n--- STATUS ---")
    total_contr = db.execute("SELECT COUNT(*) n FROM contratacoes").fetchone()["n"]
    print("  contratacoes:", total_contr)
    for row in db.execute("SELECT status_paginacao, COUNT(*) n FROM paginacao_itens GROUP BY 1"):
        print("    paginacao_itens.status=%s: %s" % (row["status_paginacao"], row["n"]))
    total_itens = db.execute("SELECT COUNT(*) n FROM itens").fetchone()["n"]
    print("  itens:", total_itens)
    for row in db.execute("SELECT classificacao_status, COUNT(*) n FROM itens GROUP BY classificacao_status"):
        print("    classificacao=%s: %s" % (row["classificacao_status"], row["n"]))
    total_res = db.execute("SELECT COUNT(*) n FROM resultados").fetchone()["n"]
    print("  resultados:", total_res)
    if fechar:
        db.close()


def validar():
    """Validacao minima contra a API real (poucas requisicoes). Requer rede
    liberada para pncp.gov.br -- bloqueada neste ambiente de desenvolvimento."""
    db = conectar_db()
    client = PncpClient(db)
    print("1) publicacoes - amostra pequena")
    status, data, erro = client.get("publicacao", PUB_URL, params={
        "dataInicial": "20250101", "dataFinal": "20250107", "codigoModalidadeContratacao": 6,
        "uf": "AP", "pagina": 1, "tamanhoPagina": 5})
    print("   status=%s erro=%s" % (status, erro))
    print("2) paginacao real de itens - contratacoes conhecidas da auditoria")
    for cnpj, ano, seq, esperado in [("23066905000160", 2025, 3, 211), ("05995766000177", 2025, 81, 257)]:
        url = ITENS_URL_TPL.format(cnpj=cnpj, ano=ano, seq=seq)

        def get_fn(pagina, tam, _url=url):
            return client.get("itens", _url, params={"pagina": pagina, "tamanhoPagina": tam})

        r = paginar_itens(get_fn)
        print("   cnpj=%s seq=%s esperado=%s obtido=%s status=%s paginas=%s" % (
            cnpj, seq, esperado, len(r["itens"]), r["status"], r["paginas_lidas"]))
    print("Total de requisicoes reais nesta validacao:", sum(client.contagem.values()))
    db.close()


def rodar_recuperacao_itens(time_budget_min=None, limite=None):
    db = conectar_db()
    client = PncpClient(db)
    inicio = datetime.now()
    limite_seg = time_budget_min * 60 if time_budget_min else None
    print("=== Recuperacao de itens com paginacao real (priorizando suspeita de truncamento) ===")
    for _ in coletar_itens_paginado(client, db, limite=limite):
        if limite_seg and (datetime.now() - inicio).total_seconds() > limite_seg:
            print("Orcamento de tempo esgotado.")
            break
    imprimir_status(db)
    db.close()


def rodar_recuperacao_resultados(time_budget_min=None, limite=None):
    db = conectar_db()
    client = PncpClient(db)
    inicio = datetime.now()
    limite_seg = time_budget_min * 60 if time_budget_min else None
    print("=== Recuperacao de resultados dos itens ACEITOS ===")
    for _ in coletar_resultados_paginado(client, db, limite=limite):
        if limite_seg and (datetime.now() - inicio).total_seconds() > limite_seg:
            print("Orcamento de tempo esgotado.")
            break
    imprimir_status(db)
    db.close()


def rodar_completo(time_budget_min=None):
    db = conectar_db()
    client = PncpClient(db)
    inicio = datetime.now()
    data_final_atual = min(date.today(), DATA_FINAL_LIMITE)
    db.execute("INSERT INTO execucoes (inicio, versao_script, data_inicial, data_final) VALUES (?,?,?,?)",
               (inicio.isoformat(), VERSAO_SCRIPT, DATA_INICIAL.isoformat(), data_final_atual.isoformat()))
    db.commit()
    limite_seg = time_budget_min * 60 if time_budget_min else None

    def orcamento_estourado():
        return limite_seg is not None and (datetime.now() - inicio).total_seconds() > limite_seg

    print("=== Fase 1: publicacoes ===")
    for _ in coletar_publicacoes(client, db, data_final_atual):
        if orcamento_estourado():
            print("Orcamento esgotado em publicacoes."); db.close(); return
    print("=== Fase 2: itens (paginacao real) ===")
    for _ in coletar_itens_paginado(client, db):
        if orcamento_estourado():
            print("Orcamento esgotado em itens."); db.close(); return
    print("=== Fase 3: resultados ===")
    for _ in coletar_resultados_paginado(client, db):
        if orcamento_estourado():
            print("Orcamento esgotado em resultados."); db.close(); return
    db.execute("UPDATE execucoes SET fim=? WHERE id=(SELECT MAX(id) FROM execucoes)", (datetime.now().isoformat(),))
    db.commit()
    imprimir_status(db)
    db.close()


def exportar():
    from pncp_ap_export import exportar_tudo
    exportar_tudo(DB_PATH, EXPORT_DIR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--run", action="store_true", help="publicacoes + itens (paginado) + resultados")
    ap.add_argument("--recuperar-itens", action="store_true", help="so recupera itens com paginacao real")
    ap.add_argument("--recuperar-resultados", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--time-budget-min", type=float, default=None)
    ap.add_argument("--limite", type=int, default=None)
    args = ap.parse_args()

    if args.validate:
        validar()
    elif args.recuperar_itens:
        rodar_recuperacao_itens(time_budget_min=args.time_budget_min, limite=args.limite)
    elif args.recuperar_resultados:
        rodar_recuperacao_resultados(time_budget_min=args.time_budget_min, limite=args.limite)
    elif args.run:
        rodar_completo(time_budget_min=args.time_budget_min)
    elif args.status:
        imprimir_status()
    elif args.export:
        exportar()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
