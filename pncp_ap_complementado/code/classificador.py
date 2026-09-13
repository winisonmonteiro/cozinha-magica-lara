# -*- coding: utf-8 -*-
"""
Motor de classificacao de itens PNCP - Amapa - esportivos/educativos.

VERSAO 2 (v2) - corrige falsos positivos/negativos da v1 identificados em
auditoria. Bugs raiz confirmados na base recebida (ver relatorio de
reconciliacao para evidencias linha-a-linha):

1) Padroes sem limite de palavra (\\b) casavam substrings dentro de OUTRAS
   palavras. Exemplos comprovados nesta base:
     - "medalha"  dentro de "medalhAO"      -> falso positivo E23
     - "xadrez"   dentro de "aXADREZAda"    -> falso positivo E21
     - "eva"      dentro de "ELEVAtoria"    -> falso positivo P16 (EVA)
     - "caneta"   dentro de "ma-CANETA-s"   -> falso positivo P06
   Correcao: todo token curto usado como identidade de produto agora exige
   \\b...\\b (limite de palavra real, nao apenas ausencia de acento).

2) Padroes combinando duas palavras com ".*" irrestrito casavam PALAVRAS
   NAO RELACIONADAS que apareciam a centenas de caracteres de distancia,
   dentro de descricoes longas de especificacao tecnica. Exemplo comprovado:
   "...plataforma ELEVAtoria... elevacao com sistema eletrico... [500
   caracteres depois] ...documentacao (EMPLACAmento e licenciamento)..."
   citado por um veiculo tipo microonibus casou "eva.*placa" e foi
   classificado como Placa de EVA (R$ 590.030,33 R$ homologados a mais
   nos rankings). Correcao: distancia maxima limitada (janela de
   caracteres) em vez de ".*" livre.

3) Acessorios/materiais citados de passagem dentro da descricao de OUTRO
   produto geravam falso positivo pela mera presenca da palavra-gatilho.
   Exemplos comprovados: "Colete Salva-Vidas ... e apito" -> falso E12;
   "Detector de Metais ... com apito sonoro" -> falso E12; "Embarcacao
   Fluvial ... apito; luz de emergencia" -> falso E12; "BASTAO sinalizador
   para transito ... botao seletor para apito" -> falso E12.
   Correcao: lista de exclusao ampliada com os produtos hospedeiros reais.

4) Variantes fora de especificacao do rol de 50 produtos eram aceitas
   silenciosamente. Exemplos comprovados: "Cola branca 40g" (especificacao
   pede 90g ou 1kg); "Lapis de cor ... 24 cores" (especificacao pede caixa
   de 12); "Regua ... Material: aluminio" (especificacao pede plastica);
   35 registros de "MEDALHA CONDECORATIVA" / "seguranca publica" / "merito
   institucional" / bombeiro militar (o rol pede medalha de premiacao
   esportiva, nao honraria/condecoracao). Correcao: essas variantes vao
   para QUARENTENA (revisao manual), nunca EXCLUIDO nem ACEITO silencioso.

5) Falso negativo comprovado: "APONTADOR C/DEPOSITO", "APONTADOR: material
   plastico tipo escolar..." nao casavam com os padroes literais estreitos
   "apontador de lapis"/"apontador escolar". Correcao: identidade ampliada
   para \\bapontador\\b (nao ha outro produto do rol que possa ser
   confundido com "apontador"), com dominancia sobre mencoes a lapis/cor
   que aparecam apenas como acessorio compativel.

6) materialOuServico='S' (servico) classificado automaticamente como
   fornecimento de material. Correcao: rebaixado para QUARENTENA com
   motivo explicito, nunca promovido automaticamente a ACEITO.
"""
import re
import unicodedata

REGRA_CLASSIFICACAO_VERSAO = "v2"

# ---------------------------------------------------------------------------
# Catalogo de produtos prioritarios (identico ao da v1; preservado)
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


def _b(token):
    """Palavra/frase com limite de palavra real nos dois lados."""
    return r"\b%s\b" % token


_VOLEI = r"\bvolei(?:bol)?\b"
_BASQUETE = r"\bbasquete(?:bol)?\b"


def _near(a, b, janela=30):
    """a e b (ja com \\b) proximos, distancia maxima 'janela' caracteres,
    para impedir casamentos entre palavras nao relacionadas em descricoes
    longas (bug raiz do EVA/microonibus)."""
    return r"%s.{0,%d}%s" % (a, janela, b)


# Regras de classificacao: (incluir[any], excluir[any-desqualifica])
# Escritas sobre texto sem acento, minusculo. Todo token identidade usa \b.
REGRAS = {
    "E01": ([_b("bola de futebol de campo"), _b("bola futebol de campo"),
             _near(_b("bola"), _b("futebol"), 100)],
            ["futsal", "salao", "society"]),
    "E02": ([_b("futsal")], ["campo"]),
    "E03": ([_near(_b("bola"), _VOLEI, 100)], []),
    "E04": ([_near(_b("bola"), _BASQUETE, 100)], []),
    "E05": ([_near(_b("bola"), _b("handebol"), 100)], []),
    "E06": ([_near(_b("rede"), r"(?:trave|gol|futsal|futebol)", 100)], ["volei", "basquete"]),
    "E07": ([_near(_b("rede"), _VOLEI, 100)], []),
    "E08": ([_near(_b("rede"), _BASQUETE, 100), _near(_b("rede"), _b("cesta"), 100)], []),
    "E09": ([_near(_b("colete"), r"(?:esport|treina|futebol|volei|basquete|time|atlet|jogo)", 100),
              _b("colete de treinamento")],
             ["salva.?vidas", "balistic", r"reflet.{0,15}(viari|transito|obra)",
              "seguranca do trabalho", r"epi\b"]),
    "E10": ([_near(_b("cone"), r"(?:esport|sinaliza|treino|agilidade)", 100),
              _b("cone de treinamento")],
             ["transito", "obra", "sinalizacao viaria", "trafego", "rodovia", "via publica", "veicular"]),
    "E11": ([_b("prato demarcat"), _b("chapeu chines"), _b("disco demarcat")], []),
    "E12": ([_b("apito")],
             ["panela", "trem", "bombeiro", "seguranca patrimonial",
              "colete", r"salva.?vidas", "detector de metais", r"embarca[cç]",
              "bastao", "sinalizador", "transito", "trafego", "vibracao"]),
    "E13": ([_b("cronometro")], []),
    "E14": ([_near(_b("bomba"), r"(?:\bar\b|inflar|encher)", 40), _b("inflador de bola")],
             ["bicicleta", "pneu"]),
    "E15": ([_b("colchonete")], ["hospitalar", "cama", "solteiro", "berco"]),
    "E16": ([_b("corda de pular")], []),
    "E17": ([_b("tatame")], ["alfabeto", "numero", r"infantil.{0,15}quebra"]),
    "E18": ([_near(_b("raquete"), r"(?:tenis de mesa|ping.?pong)", 25)],
             ["tenis de campo", r"tenis \(esporte\) individual"]),
    "E19": ([_near(_b("bola"), r"(?:tenis de mesa|ping.?pong)", 25)], []),
    "E20": ([_near(_b("mesa"), r"(?:tenis de mesa|ping.?pong)", 25)], []),
    "E21": ([_b("xadrez")],
             ["tecido", "voil", "voal", "estampa", "malha", "toalha", "camisa",
              "guardanapo", "pano de prato", "cortina", "jacquard"]),
    "E22": ([_b("trofeu")], []),
    "E23": ([_b("medalhas?")], ["tecido", "jacquard", "voil", "voal", "estampa"]),
    "E24": ([_b("bambole")],
             ["biscoito", "aliment", "comestivel", "polvilho", "ingrediente", "receita", "sabor"]),
    "E25": ([_b("medicine ball"), _b("bola de peso"), _b("bola medicinal")], []),
    "P01": ([_b("papel sulfite"), _b("papel a4"), _near(_b("papel"), _b("resma"), 25)],
             ["cartolina", "cartao", "fotografico", "livro ata", "livro de protocolo",
              "caderno protocolo", "bloco de atendimento", "bloco recado", "bloco de recado",
              "formulario", "talao", "boletim", "atendimento", "atestado", "receituario",
              "prontuario", "requisicao"]),
    "P02": ([_b("caderno universitario"), _near(_b("caderno"), _b("capa dura"), 20)],
             ["brochura", "brochurao"]),
    "P03": ([_b("caderno brochura"), _b("caderno brochurao"),
              _near(_b("caderno"), r"(?:48|96|100).{0,10}folhas.{0,10}brochura", 40)],
             ["capa dura", "universitario"]),
    "P04": ([_b("lapis de cor")], ["kit escolar completo", "estojo completo"]),
    "P05": ([_b("lapis grafite"), _b("lapis preto"), _near(_b("lapis"), r"(?:n.?2|hb)\b", 15)],
             ["lapis de cor"]),
    "P06": ([_b("caneta esferografica"), _near(_b("caneta"), r"(?:azul|preta|vermelha)\b", 30)],
             ["corretiva", "corretivo", r"marca.?texto", "hidrografica"]),
    "P07": ([_b("borracha escolar"), _near(_b("borracha"), _b("apagar"), 20)],
             [r"borracha.{0,25}(vedacao|silicone industrial|pneu)"]),
    "P08": ([_b("apontador")], ["kit"]),
    "P09": ([_b("massa de modelar")], []),
    "P10": ([_b("tinta guache"), _b("guache")], []),
    "P11": ([_b("pincel escolar"), _near(_b("pincel"), r"(?:pintura|arte)", 20),
              _b("pincel chato"), _b("pincel redondo")],
             ["maquiagem", "cosmetico", "predial", "parede", "obra", "construcao", "industrial"]),
    "P12": ([_b("cola branca"), _b("cola escolar liquida")],
             ["cola quente", "cola de contato", "cola para madeira", "cola industrial",
              "cola de silicone", "cola instantanea", r"super ?bonder", "cola isopor"]),
    "P13": ([_b("tesoura escolar"), _b("tesoura sem ponta")],
             ["tesoura de poda", "tesoura industrial", "tesoura de cabelo"]),
    "P14": ([_near(r"\bregua\b", r"(?:30 ?cm|plastica|escolar|comprimento)", 100)],
             ["metalica industrial", "de pedreiro", "nivel", "antropometric", "clinic", "hospitalar",
              "paciente", "balanca", "cientific", "medic"]),
    "P15": ([_b("cartolina")],
             [r"pasta.{0,15}cartolina", "papel sulfite", "capa de processo", "capa processo", "dossie"]),
    "P16": ([_b("folha de eva"), _b("placa de eva"),
              _near(_b("eva"), r"(?:artesanato|escolar|folha|placa)", 25)],
             ["cola quente", "espuma industrial", "vedacao", "solado",
              "veiculo", "onibus", r"micro.?onibus", "pick-up", "pickup", "caminhonete"]),
    "P17": ([_b("giz de cera")], []),
    "P18": ([_near(_b("giz"), r"(?:quadro|lousa|escolar)", 20), _b("giz branco escolar")],
             ["giz de cera", "giz de alfaiate"]),
    "P19": ([_near(_b("apagador"), r"(?:quadro|lousa)", 20)], []),
    "P20": ([_b("mochila escolar")], [r"mochila.{0,25}(costas adulto executiv|camping|motoqueiro)"]),
    "P21": ([_b("blocos logicos"), _b("bloco logico")], []),
    "P22": ([_b("material dourado")], []),
    "P23": ([_b("alfabeto movel")], ["tatame", "tapete"]),
    "P24": ([_b("abaco escolar"), _b("abaco pedagogico")], []),
    "P25": ([r"quebra.?cabeca"], []),
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
DOMINANCIA = {
    "P08": ["P04", "P05"],   # apontador domina mencao a lapis
    "P07": ["P04", "P05"],   # borracha domina mencao a grafite/lapis
    "P10": ["P15", "P16"],   # guache domina mencao a cartolina/EVA (superficie de aplicacao)
    "P12": ["P16"],          # cola branca domina mencao a EVA (uso em artesanato)
    "E09": ["E01", "E02", "E03", "E04", "E05"],  # colete domina mencao a esporte/bola no contexto
}

# ---------------------------------------------------------------------------
# Variantes fora de especificacao do rol (secao 7 do escopo): vao para
# QUARENTENA em vez de ACEITO silencioso, mesmo casando a regra de identidade.
# ---------------------------------------------------------------------------

_RE_MEDALHA_INSTITUCIONAL = re.compile(
    r"condecorat|militar|bombeiro|defesa civil|seguranca publica|policial|"
    r"honraria|comenda|merito(?!.{0,15}(desportiv|esportiv))",
)
_RE_COLA_GRAMATURA = re.compile(r"(\d+)\s*g\b")
_RE_LAPIS_CORES = re.compile(r"(\d+)\s*cores|cores?\D{0,10}?(\d+)")
_RE_REGUA_MATERIAL = re.compile(r"regua.{0,40}(aluminio|metal|metalic|inox|aco)")


def _detectar_variante_fora_espec(produto_id, desc_norm):
    """Retorna motivo (str) se a descricao, apesar de casar a regra de
    identidade do produto, e uma variante fora das especificacoes do rol
    (secao 7) que deve ir para revisao manual em vez de ACEITO direto."""
    if produto_id == "E23":
        if _RE_MEDALHA_INSTITUCIONAL.search(desc_norm):
            return ("variante fora de especificacao: medalha institucional/militar/condecorativa "
                    "(rol pede medalha de premiacao esportiva)")
    if produto_id == "P12":
        m = _RE_COLA_GRAMATURA.search(desc_norm)
        if m and int(m.group(1)) not in (90,):
            return "variante fora de especificacao: gramatura %sg (rol pede 90g ou 1kg)" % m.group(1)
    if produto_id == "P04":
        m = _RE_LAPIS_CORES.search(desc_norm)
        if m:
            qtd = m.group(1) or m.group(2)
            if qtd and int(qtd) != 12:
                return "variante fora de especificacao: caixa de %s cores (rol pede caixa de 12)" % qtd
    if produto_id == "P14":
        if _RE_REGUA_MATERIAL.search(desc_norm):
            return "variante fora de especificacao: regua de material metalico/aluminio (rol pede plastica)"
    return None


def classificar_item(descricao, material_ou_servico=None):
    """Retorna dict com status, produto_id, linha, motivo, regra_versao.

    material_ou_servico: valor bruto do campo materialOuServico da API
    (tipicamente 'M' ou 'S'), quando disponivel. Usado para nao promover
    automaticamente objetos de servico a fornecimento de material.
    """
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
        return dict(status="QUARENTENA", produto_id=None, linha=linhas.pop(),
                     motivo="ambiguidade entre produtos proximos: %s" % ",".join(sorted(candidatos)),
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    pid = candidatos[0]
    linha = PRODUTOS_MAP[pid][1]

    if any(re.search(p, desc_norm) for p in EXCLUSAO_GENERICA):
        return dict(status="QUARENTENA", produto_id=pid, linha=linha,
                     motivo="descricao sugere servico/locacao, nao aquisicao de material",
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    variante_motivo = _detectar_variante_fora_espec(pid, desc_norm)
    if variante_motivo:
        return dict(status="QUARENTENA", produto_id=pid, linha=linha,
                     motivo=variante_motivo, regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    if material_ou_servico and str(material_ou_servico).strip().upper().startswith("S"):
        return dict(status="QUARENTENA", produto_id=pid, linha=linha,
                     motivo="materialOuServico=S (servico); revisar se e prestacao de servico "
                            "ou fornecimento efetivo do material antes de aceitar",
                     regra_versao=REGRA_CLASSIFICACAO_VERSAO)

    return dict(status="ACEITO", produto_id=pid, linha=linha,
                motivo="casou regra de identidade do produto %s" % pid,
                regra_versao=REGRA_CLASSIFICACAO_VERSAO)
