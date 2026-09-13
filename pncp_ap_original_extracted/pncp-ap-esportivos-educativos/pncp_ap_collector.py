#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Coletor PNCP - Amapa - Materiais esportivos e educativos.

Uso:
  python pncp_ap_collector.py --validate
  python pncp_ap_collector.py --run --time-budget-min 60
  python pncp_ap_collector.py --status
  python pncp_ap_collector.py --export

Executavel tambem no Google Colab (ajustar DB_PATH se necessario).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, date, timedelta

import requests

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

VERSAO_SCRIPT = "7.0"
REGRA_CLASSIFICACAO_VERSAO = "v1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "captura.sqlite")
EXPORT_DIR = os.path.join(BASE_DIR, "export")
LOG_DIR = os.path.join(BASE_DIR, "logs")

UF_ALVO = "AP"
MODALIDADES = [6, 7, 8]  # pregao eletronico, pregao presencial, dispensa
DATA_INICIAL = date(2025, 1, 1)
DATA_FINAL_LIMITE = date(2026, 12, 31)
WINDOW_DAYS = 7
PAGE_SIZE = 50

PUB_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
ITENS_URL_TPL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens"
RESULTADOS_URL_TPL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{item}/resultados"
DETALHE_URL_TPL = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
DETALHE_URL_OLD_TPL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_TENTATIVAS = 3

CANCEL_SENTINELAS = {None, "", "0001-01-01T00:00:00"}

# ---------------------------------------------------------------------------
# Catalogo de produtos prioritarios
# ---------------------------------------------------------------------------

PRODUTOS = [
    ("E01", "esportivo", "Bola de futebol de campo", "9506.62.00"),
    ("E02", "esportivo", "Bola de futsal", "9506.62.00"),
    ("E03", "esportivo", "Bola de volei", "9506.62.00"),
    ("E04", "esportivo", "Bola de basquete", "9506.62.00"),
    ("E05", "esportivo", "Bola de handebol", "9506.62.00"),
    ("E06", "esportivo", "Rede para trave de futsal/futebol", "9506.99.00"),
    ("E07", "esportivo", "Rede de volei", "9506.99.00"),
    ("E08", "esportivo", "Rede de basquete", "9506.99.00"),
    ("E09", "esportivo", "Colete de treinamento esportivo", "6114.30.00"),
    ("E10", "esportivo", "Cone esportivo de sinalizacao", "3926.90.90"),
    ("E11", "esportivo", "Prato demarcatorio/chapeu chines", "3926.90.90"),
    ("E12", "esportivo", "Apito de arbitro", "9208.90.00"),
    ("E13", "esportivo", "Cronometro digital profissional", "9106.90.00"),
    ("E14", "esportivo", "Bomba manual de ar para bolas", "8414.20.00"),
    ("E15", "esportivo", "Colchonete de ginastica", "3921.19.00"),
    ("E16", "esportivo", "Corda de pular", "9506.91.00"),
    ("E17", "esportivo", "Placa de tatame em EVA", "3921.19.00"),
    ("E18", "esportivo", "Raquete de tenis de mesa", "9506.40.00"),
    ("E19", "esportivo", "Bola de tenis de mesa", "9506.40.00"),
    ("E20", "esportivo", "Mesa de tenis de mesa", "9506.40.00"),
    ("E21", "esportivo", "Jogo de xadrez completo", "9504.90.10"),
    ("E22", "esportivo", "Trofeu", "3926.40.00"),
    ("E23", "esportivo", "Medalha de premiacao", "8306.29.00"),
    ("E24", "esportivo", "Bambole", "9506.99.00"),
    ("E25", "esportivo", "Medicine ball/bola de peso", "9506.91.00"),
    ("P01", "educativo", "Papel sulfite A4, resma de 500 folhas", "4802.56.10"),
    ("P02", "educativo", "Caderno universitario, capa dura, 10+ materias", "4820.20.00"),
    ("P03", "educativo", "Caderno brochura/brochurao", "4820.20.00"),
    ("P04", "educativo", "Lapis de cor, caixa de 12 cores", "9609.10.00"),
    ("P05", "educativo", "Lapis grafite numero 2/HB", "9609.10.00"),
    ("P06", "educativo", "Caneta esferografica azul/preta/vermelha", "9608.10.00"),
    ("P07", "educativo", "Borracha escolar", "4016.92.00"),
    ("P08", "educativo", "Apontador de lapis", "8214.10.00"),
    ("P09", "educativo", "Massa de modelar, cores sortidas", "3407.00.10"),
    ("P10", "educativo", "Tinta guache, conjunto de seis cores", "3213.10.00"),
    ("P11", "educativo", "Pincel escolar", "9603.30.00"),
    ("P12", "educativo", "Cola branca liquida, 90g ou 1kg", "3506.10.90"),
    ("P13", "educativo", "Tesoura escolar sem ponta", "8213.00.00"),
    ("P14", "educativo", "Regua plastica de 30cm", "9017.80.10"),
    ("P15", "educativo", "Cartolina", "4802.58.99"),
    ("P16", "educativo", "Placa/folha de EVA para artesanato", "3921.19.00"),
    ("P17", "educativo", "Giz de cera, caixa de 12 cores", "9609.90.00"),
    ("P18", "educativo", "Giz para quadro/lousa", "9609.90.00"),
    ("P19", "educativo", "Apagador para quadro/lousa", "9603.90.00"),
    ("P20", "educativo", "Mochila escolar", "4202.92.00"),
    ("P21", "educativo", "Blocos logicos", "9503.00.99"),
    ("P22", "educativo", "Material dourado", "9503.00.99"),
    ("P23", "educativo", "Alfabeto movel", "9503.00.99"),
    ("P24", "educativo", "Abaco escolar", "9017.20.00"),
    ("P25", "educativo", "Quebra-cabeca educativo infantil", "9503.00.60"),
]
PRODUTOS_MAP = {p[0]: p for p in PRODUTOS}


def strip_acentos(txt):
    if txt is None:
        return ""
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c)).lower()


# Regras de classificacao: (incluir[any], excluir[any-desqualifica])
# Escritas sobre texto sem acento, minusculo.
REGRAS = {
    "E01": (["bola de futebol de campo", "bola futebol de campo", "bola de futebol", "bola.*futebol.*campo"],
            ["futsal", "salao", "society"]),
    "E02": (["futsal"], ["campo"]),
    "E03": (["bola.*volei", "bola de voleibol"], []),
    "E04": (["bola.*basquete", "bola.*basquetebol"], []),
    "E05": (["bola.*handebol"], []),
    "E06": (["rede.*(trave|gol|futsal|futebol)"], ["volei", "basquete"]),
    "E07": (["rede.*volei"], []),
    "E08": (["rede.*basquete", "rede.*cesta"], []),
    "E09": (["colete.*(esport|treina|futebol|volei|basquete|time|atlet|jogo)", "colete de treinamento"],
            ["salva.?vidas", "balistic", "reflet.*(viari|transito|obra)", "seguranca do trabalho", "epi\\b"]),
    "E10": (["cone.*(esport|sinaliza|treino|agilidade)", "cone de treinamento"],
            ["transito", "obra", "sinalizacao viaria", "trafego"]),
    "E11": (["prato demarcat", "chapeu chines", "disco demarcat"], []),
    "E12": (["apito"], ["panela", "trem", "bombeiro", "seguranca patrimonial"]),
    "E13": (["cronometro"], []),
    "E14": (["bomba.*(ar|inflar).*bola", "inflador de bola", "bomba de encher bola"], ["bicicleta", "pneu"]),
    "E15": (["colchonete"], ["hospitalar", "cama", "solteiro", "berco"]),
    "E16": (["corda de pular"], []),
    "E17": (["tatame"], ["alfabeto", "numero", "infantil.*quebra"]),
    "E18": (["raquete.*(tenis de mesa|ping.?pong)"], ["tenis de campo", "tenis \\(esporte\\) individual"]),
    "E19": (["bola.*(tenis de mesa|ping.?pong)"], []),
    "E20": (["mesa.*(tenis de mesa|ping.?pong)"], []),
    "E21": (["xadrez"], []),
    "E22": (["trofeu"], []),
    "E23": (["medalha"], []),
    "E24": (["bambole"], []),
    "E25": (["medicine ball", "bola de peso", "bola medicinal"], []),
    "P01": (["papel sulfite", "papel a4", "papel.*resma"], ["cartolina", "cartao", "fotografico"]),
    "P02": (["caderno universitario", "caderno.*capa dura"], ["brochura", "brochurao"]),
    "P03": (["caderno brochura", "caderno brochurao", "caderno.*(48|96|100).*folhas.*brochura"],
            ["capa dura", "universitario"]),
    "P04": (["lapis de cor"], ["kit escolar completo", "estojo completo"]),
    "P05": (["lapis grafite", "lapis preto", "lapis.*(n.?2|hb)\\b"], ["lapis de cor"]),
    "P06": (["caneta esferografica", "caneta.*(azul|preta|vermelha)\\b"], ["corretiva", "corretivo", "marca.?texto", "hidrografica"]),
    "P07": (["borracha escolar", "borracha.*apagar"], ["borracha.*(vedacao|silicone industrial|pneu)"]),
    "P08": (["apontador de lapis", "apontador escolar"], ["kit"]),
    "P09": (["massa de modelar"], []),
    "P10": (["tinta guache", "guache"], []),
    "P11": (["pincel escolar", "pincel.*(pintura|arte).*escolar", "pincel chato", "pincel redondo"], ["maquiagem", "cosmetico"]),
    "P12": (["cola branca", "cola escolar liquida"], ["cola quente", "cola de contato", "cola para madeira", "cola industrial", "cola de silicone", "cola instantanea", "super ?bonder", "cola isopor"]),
    "P13": (["tesoura escolar", "tesoura sem ponta"], ["tesoura de poda", "tesoura industrial", "tesoura de cabelo"]),
    "P14": (["regua.*(30 ?cm|plastica|escolar)"], ["metalica industrial", "de pedreiro", "nivel"]),
    "P15": (["cartolina"], ["pasta.*cartolina", "papel sulfite"]),
    "P16": (["folha de eva", "placa de eva", "eva.*(artesanato|escolar|folha|placa)"],
            ["cola quente", "espuma industrial", "vedacao", "solado"]),
    "P17": (["giz de cera"], []),
    "P18": (["giz.*(quadro|lousa|escolar)", "giz branco escolar"], ["giz de cera", "giz de alfaiate"]),
    "P19": (["apagador.*(quadro|lousa)"], []),
    "P20": (["mochila escolar"], ["mochila.*(costas adulto executiv|camping|motoqueiro)"]),
    "P21": (["blocos logicos", "bloco logico"], []),
    "P22": (["material dourado"], []),
    "P23": (["alfabeto movel"], ["tatame", "tapete"]),
    "P24": (["abaco escolar", "abaco pedagogico"], []),
    "P25": (["quebra.?cabeca"], []),
}

# Padroes de exclusao genericos fortes (independem do produto)
EXCLUSAO_GENERICA = [
    r"\bservico[s]? de\b",
    r"\blocacao de\b",
    r"\bmanutencao de\b",
]

# Dominancia: quando o produto-chave (identidade central do item) casa junto com
# produtos subordinados citados apenas como material/uso/acessorio na descricao,
# o subordinado e descartado em vez de gerar quarentena por ambiguidade.
# Ex.: "apontador ... contem lapis grafite e lapis de cor" -> identidade e apontador (P08),
# a mencao a lapis e acessoria, nao um segundo produto a vender.
DOMINANCIA = {
    "P08": ["P04", "P05"],   # apontador domina mencao a lapis
    "P07": ["P04", "P05"],   # borracha domina mencao a grafite/lapis
    "P10": ["P15", "P16"],   # guache domina mencao a cartolina/EVA (superficie de aplicacao)
    "P12": ["P16"],          # cola branca domina mencao a EVA (uso em artesanato)
    "E09": ["E01", "E02", "E03", "E04", "E05"],  # colete domina mencao a esporte/bola no contexto
}


def classificar_item(descricao):
    """Retorna dict com status, produto_id, linha, motivo, regra_versao."""
    desc_norm = strip_acentos(descricao or "")
    if not desc_norm.strip():
        return dict(status="EXCLUIDO", produto_id=None, linha=None,
                     motivo="descricao vazia", regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    candidatos = []
    for pid, (inclui, exclui) in REGRAS.items():
        inc_hit = any(re.search(pat, desc_norm) for pat in inclui)
        if not inc_hit:
            continue
        exc_hit = any(re.search(pat, desc_norm) for pat in exclui)
        if exc_hit:
            continue
        candidatos.append(pid)

    if not candidatos:
        return dict(status="EXCLUIDO", produto_id=None, linha=None,
                     motivo="nenhuma regra de identidade de produto casou",
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    # dominancia: remove subordinados citados apenas como material/uso/acessorio
    candidatos_set = set(candidatos)
    for dominante, subordinados in DOMINANCIA.items():
        if dominante in candidatos_set:
            candidatos_set -= set(subordinados)
    candidatos = sorted(candidatos_set)

    # heterogeneidade: casou com produtos de linhas diferentes ou muitos produtos distintos -> kit heterogeneo
    linhas = {PRODUTOS_MAP[c][1] for c in candidatos}
    tem_kit_palavra = bool(re.search(r"\bkit\b|\bconjunto\b", desc_norm)) and len(candidatos) > 1
    if len(candidatos) > 1 and (len(linhas) > 1 or tem_kit_palavra):
        return dict(status="QUARENTENA", produto_id=None, linha=None,
                     motivo="possivel kit heterogeneo: casou com %s" % ",".join(sorted(candidatos)),
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    if len(candidatos) > 1:
        # mesma linha, produtos distintos mas proximos (ex: bola de futebol x futsal) -> quarentena p/ revisao manual
        return dict(status="QUARENTENA", produto_id=None, linha=linhas.pop(),
                     motivo="ambiguidade entre produtos proximos: %s" % ",".join(sorted(candidatos)),
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    pid = candidatos[0]
    linha = PRODUTOS_MAP[pid][1]

    if any(re.search(p, desc_norm) for p in EXCLUSAO_GENERICA):
        return dict(status="QUARENTENA", produto_id=pid, linha=linha,
                     motivo="descricao sugere servico/locacao, nao aquisicao de material",
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    return dict(status="ACEITO", produto_id=pid, linha=linha,
                motivo="casou regra de identidade do produto %s" % pid,
                regra_versao=REGRA_CLASSIFICACAO_VERSAO)


# ---------------------------------------------------------------------------
# Normalizacao de unidades
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
    """Retorna (unidade_normalizada, fator_conversao, evidencia)."""
    u_norm = strip_acentos(unidade_original or "").strip()
    base = UNIDADES_DIRETAS.get(u_norm)
    if base in ("unidade", "resma", "par", "kg", "litro", "metro", "folha", "grama"):
        return base, 1.0, "unidade original ja comparavel (sem conversao)"

    desc_norm = strip_acentos(descricao or "")
    # tenta extrair fator explicito tipo "caixa com 10 resmas" / "caixa c/ 12 unidades"
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
# Cancelamento / resultados
# ---------------------------------------------------------------------------

def avaliar_cancelamento(resultado):
    data_cnl = resultado.get("dataCancelamento")
    motivo_cnl = resultado.get("motivoCancelamento")
    situacao_nome = strip_acentos(resultado.get("situacaoCompraItemResultadoNome") or "")

    data_valida = data_cnl not in CANCEL_SENTINELAS
    tem_motivo = bool(motivo_cnl and str(motivo_cnl).strip())
    situacao_indica_cancelado = "cancelad" in situacao_nome

    if situacao_indica_cancelado and (data_valida or tem_motivo):
        return True, "situacao=%s + evidencia de cancelamento" % resultado.get("situacaoCompraItemResultadoNome")
    if data_valida and tem_motivo:
        return True, "data de cancelamento valida + motivo preenchido"
    if data_cnl in CANCEL_SENTINELAS and not tem_motivo and not situacao_indica_cancelado:
        return False, "sentinela/ausente sem motivo nem situacao de cancelamento -> nao cancelado"
    # data preenchida mas sem motivo e situacao nao indica cancelamento: indeterminado, nao assumir cancelado
    return False, "evidencia insuficiente para cancelamento; tratado como nao cancelado (revisar manualmente)"


def classificar_esfera(esfera_id, poder_id):
    """estadual / municipal / federal / indefinida -- a partir de orgaoEntidade.esferaId."""
    e = (esfera_id or "").upper()
    if e == "E":
        return "estadual"
    if e == "M":
        return "municipal"
    if e == "F":
        return "federal"
    return "indefinida"


# ---------------------------------------------------------------------------
# Banco de dados
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
    """SQLite so aceita tipos escalares; a API do PNCP as vezes devolve objeto/lista
    onde documentacao/amostras sugeriam string (ex.: categoriaItemCatalogo). Serializa
    esses casos em vez de falhar o insert, preservando o dado bruto."""
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
# Cliente HTTP com ritmo adaptativo
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

    def get(self, kind, url, params=None, allow_301_body=False):
        """Retorna (status_code, json_ou_none, texto_ou_none, erro_ou_none)."""
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
                    # Nao insistir contra o mesmo bloqueio: aplica a pausa global e devolve
                    # o controle ao chamador, que deve tentar novamente depois (nao e falha real).
                    return 429, None, None, "rate limited (429); pausa global aplicada, repetir depois"
                if resp.status_code == 301:
                    self._log(kind, url, 301, tempo, tentativa, "moved permanently")
                    if allow_301_body:
                        try:
                            return 301, resp.json(), resp.text, None
                        except Exception:
                            return 301, None, resp.text, None
                    return 301, None, None, "301 nao resolvido"
                if resp.status_code == 204:
                    # Sem conteudo = sucesso com zero registros (nao e erro; nao insistir)
                    self._log(kind, url, 204, tempo, tentativa, None)
                    self.ritmo.registrar_sucesso()
                    corpo_vazio = {"data": [], "totalRegistros": 0, "totalPaginas": 1} if kind == "publicacao" else []
                    return 200, corpo_vazio, "", None
                if resp.status_code == 200:
                    self._log(kind, url, 200, tempo, tentativa, None)
                    self.ritmo.registrar_sucesso()
                    try:
                        return 200, resp.json(), resp.text, None
                    except Exception as e:
                        return 200, None, resp.text, "erro parse json: %s" % e
                if resp.status_code == 404:
                    self._log(kind, url, 404, tempo, tentativa, "not found")
                    return 404, None, None, "404"
                self._log(kind, url, resp.status_code, tempo, tentativa, "status inesperado")
                ultimo_erro = "status %s" % resp.status_code
            except requests.exceptions.ReadTimeout as e:
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
        return None, None, None, ultimo_erro


# ---------------------------------------------------------------------------
# Coleta: publicacoes
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
        return  # cinto de seguranca: descarta qualquer registro fora de AP

    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    update_cols = ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "numero_controle_pncp")
    sql = "INSERT INTO contratacoes (%s) VALUES (%s) ON CONFLICT(numero_controle_pncp) DO UPDATE SET %s" % (
        ",".join(cols), placeholders, update_cols,
    )
    db.execute(sql, [_sql_safe(row[c]) for c in cols])


def coletar_publicacoes(client, db, log_progresso=True):
    for modalidade in MODALIDADES:
        for ini, fim in iter_janelas(DATA_INICIAL, DATA_FINAL_LIMITE if DATA_FINAL_ATUAL is None else DATA_FINAL_ATUAL):
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
                    "tamanhoPagina": PAGE_SIZE,
                }
                status, data, _, erro = client.get("publicacao", PUB_URL, params=params)
                if status == 429:
                    # rate limitado: nao e falha real, apenas adia esta janela para a proxima passada
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
            yield  # ponto de controle para orcamento de tempo


# ---------------------------------------------------------------------------
# Coleta: itens
# ---------------------------------------------------------------------------

def upsert_item(db, numero_controle, r):
    numero_item = r.get("numeroItem")
    unidade_norm, fator, evidencia = normalizar_unidade(r.get("unidadeMedida"), r.get("descricao"))
    cls = classificar_item(r.get("descricao"))
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


def coletar_itens(client, db, limite=None):
    cur = db.execute(
        "SELECT numero_controle_pncp, cnpj, ano_compra, sequencial_compra FROM contratacoes "
        "WHERE itens_status IN ('nao_consultado','falha') "
        + ("LIMIT %d" % limite if limite else "")
    )
    linhas = cur.fetchall()
    for row in linhas:
        numero_controle = row["numero_controle_pncp"]
        url = ITENS_URL_TPL.format(cnpj=row["cnpj"], ano=row["ano_compra"], seq=row["sequencial_compra"])
        status, data, _, erro = client.get("itens", url)
        if status == 200 and isinstance(data, list):
            aceitos = quarentena = excluidos = 0
            for it in data:
                st = upsert_item(db, numero_controle, it)
                if st == "ACEITO":
                    aceitos += 1
                elif st == "QUARENTENA":
                    quarentena += 1
                else:
                    excluidos += 1
            db.execute("UPDATE contratacoes SET itens_status=? WHERE numero_controle_pncp=?",
                       ("ok" if data else "sem_registros", numero_controle))
            db.commit()
            print("  [itens] %s -> %s itens (aceitos=%s quarentena=%s excluidos=%s)" % (
                numero_controle, len(data), aceitos, quarentena, excluidos))
        elif status == 404:
            db.execute("UPDATE contratacoes SET itens_status=? WHERE numero_controle_pncp=?",
                       ("sem_registros", numero_controle))
            db.commit()
        else:
            db.execute("UPDATE contratacoes SET itens_status=? WHERE numero_controle_pncp=?",
                       ("falha", numero_controle))
            db.commit()
        yield


# ---------------------------------------------------------------------------
# Coleta: resultados
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


def coletar_resultados(client, db, limite=None):
    cur = db.execute(
        "SELECT i.numero_controle_pncp, i.numero_item, c.cnpj, c.ano_compra, c.sequencial_compra "
        "FROM itens i JOIN contratacoes c ON c.numero_controle_pncp = i.numero_controle_pncp "
        "WHERE i.classificacao_status='ACEITO' AND i.resultados_status IN ('nao_consultado','falha') "
        + ("LIMIT %d" % limite if limite else "")
    )
    linhas = cur.fetchall()
    for row in linhas:
        url = RESULTADOS_URL_TPL.format(cnpj=row["cnpj"], ano=row["ano_compra"], seq=row["sequencial_compra"], item=row["numero_item"])
        status, data, _, erro = client.get("resultados", url)
        if status == 200 and isinstance(data, list):
            for r in data:
                upsert_resultado(db, row["numero_controle_pncp"], row["numero_item"], r)
            db.execute("UPDATE itens SET resultados_status=? WHERE numero_controle_pncp=? AND numero_item=?",
                       ("ok" if data else "sem_registros", row["numero_controle_pncp"], row["numero_item"]))
            db.commit()
        elif status == 404:
            db.execute("UPDATE itens SET resultados_status=? WHERE numero_controle_pncp=? AND numero_item=?",
                       ("sem_registros", row["numero_controle_pncp"], row["numero_item"]))
            db.commit()
        else:
            db.execute("UPDATE itens SET resultados_status=? WHERE numero_controle_pncp=? AND numero_item=?",
                       ("falha", row["numero_controle_pncp"], row["numero_item"]))
            db.commit()
        yield


# ---------------------------------------------------------------------------
# Orquestracao principal com orcamento de tempo
# ---------------------------------------------------------------------------

DATA_FINAL_ATUAL = None


def rodar(time_budget_min=None):
    global DATA_FINAL_ATUAL
    DATA_FINAL_ATUAL = min(date.today(), DATA_FINAL_LIMITE)
    db = conectar_db()
    client = PncpClient(db)
    inicio = datetime.now()
    db.execute("INSERT INTO execucoes (inicio, versao_script, data_inicial, data_final) VALUES (?,?,?,?)",
               (inicio.isoformat(), VERSAO_SCRIPT, DATA_INICIAL.isoformat(), DATA_FINAL_ATUAL.isoformat()))
    db.commit()

    limite_seg = time_budget_min * 60 if time_budget_min else None

    def orcamento_estourado():
        if limite_seg is None:
            return False
        return (datetime.now() - inicio).total_seconds() > limite_seg

    print("=== Fase 1: publicacoes (%s a %s, modalidades %s) ===" % (DATA_INICIAL, DATA_FINAL_ATUAL, MODALIDADES))
    for _ in coletar_publicacoes(client, db):
        if orcamento_estourado():
            print("Orcamento de tempo esgotado durante publicacoes.")
            _fechar(db, inicio)
            return

    print("=== Fase 2: itens ===")
    for _ in coletar_itens(client, db):
        if orcamento_estourado():
            print("Orcamento de tempo esgotado durante itens.")
            _fechar(db, inicio)
            return

    print("=== Fase 3: resultados (itens ACEITOS) ===")
    for _ in coletar_resultados(client, db):
        if orcamento_estourado():
            print("Orcamento de tempo esgotado durante resultados.")
            _fechar(db, inicio)
            return

    print("=== Coleta completa para o escopo configurado ===")
    _fechar(db, inicio)


def _fechar(db, inicio):
    db.execute("UPDATE execucoes SET fim=? WHERE id=(SELECT MAX(id) FROM execucoes)", (datetime.now().isoformat(),))
    db.commit()
    imprimir_status(db)
    db.close()


def imprimir_status(db=None):
    fechar = False
    if db is None:
        db = conectar_db()
        fechar = True
    print("\n--- STATUS ---")
    for row in db.execute("SELECT tipo, status, COUNT(*) n FROM checkpoints GROUP BY tipo, status ORDER BY tipo, status"):
        print("  checkpoint %s / %s: %s" % (row["tipo"], row["status"], row["n"]))
    total_contr = db.execute("SELECT COUNT(*) n FROM contratacoes").fetchone()["n"]
    print("  contratacoes: %s" % total_contr)
    for row in db.execute("SELECT itens_status, COUNT(*) n FROM contratacoes GROUP BY itens_status"):
        print("    itens_status=%s: %s" % (row["itens_status"], row["n"]))
    total_itens = db.execute("SELECT COUNT(*) n FROM itens").fetchone()["n"]
    print("  itens: %s" % total_itens)
    for row in db.execute("SELECT classificacao_status, COUNT(*) n FROM itens GROUP BY classificacao_status"):
        print("    classificacao=%s: %s" % (row["classificacao_status"], row["n"]))
    total_res = db.execute("SELECT COUNT(*) n FROM resultados").fetchone()["n"]
    print("  resultados: %s" % total_res)
    if fechar:
        db.close()


# ---------------------------------------------------------------------------
# Validacao minima (poucas requisicoes reais)
# ---------------------------------------------------------------------------

def validar():
    db = conectar_db()
    client = PncpClient(db)
    print("1) publicacoes - amostra pequena")
    status, data, _, erro = client.get("publicacao", PUB_URL, params={
        "dataInicial": "20250101", "dataFinal": "20250107", "codigoModalidadeContratacao": 6,
        "uf": "AP", "pagina": 1, "tamanhoPagina": 5})
    print("   status=%s totalRegistros=%s totalPaginas=%s erro=%s" % (
        status, (data or {}).get("totalRegistros"), (data or {}).get("totalPaginas"), erro))
    if status == 200 and data and data.get("data"):
        r = data["data"][0]
        orgao = r["orgaoEntidade"]
        print("2) itens - contratacao de amostra %s" % r.get("numeroControlePNCP"))
        url_itens = ITENS_URL_TPL.format(cnpj=orgao["cnpj"], ano=r["anoCompra"], seq=r["sequencialCompra"])
        status_i, data_i, _, erro_i = client.get("itens", url_itens)
        print("   status=%s n_itens=%s erro=%s" % (status_i, len(data_i) if isinstance(data_i, list) else None, erro_i))
        if status_i == 200 and data_i:
            print("3) resultados - item 1 da amostra")
            url_res = RESULTADOS_URL_TPL.format(cnpj=orgao["cnpj"], ano=r["anoCompra"], seq=r["sequencialCompra"], item=data_i[0]["numeroItem"])
            status_r, data_r, _, erro_r = client.get("resultados", url_res)
            print("   status=%s n_resultados=%s erro=%s" % (status_r, len(data_r) if isinstance(data_r, list) else None, erro_r))
        print("4) detalhe - endpoint novo (destino do 301)")
        url_det = DETALHE_URL_TPL.format(cnpj=orgao["cnpj"], ano=r["anoCompra"], seq=r["sequencialCompra"])
        status_d, data_d, _, erro_d = client.get("detalhe", url_det)
        print("   status=%s tem_dados=%s erro=%s" % (status_d, bool(data_d), erro_d))
    print("Total de requisicoes reais nesta validacao: %s" % sum(client.contagem.values()))
    db.close()


# ---------------------------------------------------------------------------
# Exportacao (implementada em pncp_ap_export.py, chamada aqui por conveniencia)
# ---------------------------------------------------------------------------

def exportar():
    from pncp_ap_export import exportar_tudo
    exportar_tudo(DB_PATH, EXPORT_DIR)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--time-budget-min", type=float, default=None)
    args = ap.parse_args()

    if args.validate:
        validar()
    elif args.run:
        rodar(time_budget_min=args.time_budget_min)
    elif args.status:
        imprimir_status()
    elif args.export:
        exportar()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
