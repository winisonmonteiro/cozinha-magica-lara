# -*- coding: utf-8 -*-
"""Testes reais (nao simulados) do classificador v2 contra descricoes
literais extraidas do banco captura.sqlite recebido. Cada caso documenta
o comportamento ERRADO da v1 (comprovado por consulta ao banco) e o
comportamento ESPERADO da v2."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classificador import classificar_item

CASOS = [
    # (descricao, material_ou_servico, status_esperado, produto_id_esperado_ou_None, observacao)
    ("VEÍCULO  TIPO MICROÔNIBUS COM ACESSIBILIDADE: ... plataforma elevatória veicular ... "
     "documentação (emplacamento e licenciamento) em nome do ente federado", "M",
     "EXCLUIDO", None, "microonibus NAO pode virar Placa de EVA (bug eva~placa por .* irrestrito)"),

    ("TECIDO VOIL VOAL XADREZ, CORES VARIADAS, MATERIAL 100% POLIÉSTER, APRESENTAÇÃO 50 METROS CADA.", "M",
     "EXCLUIDO", None, "tecido com padrao xadrez NAO e jogo de xadrez"),

    ("TECIDO JACQUARD MEDALHÃO ACETINADO, CORES VARIADAS, MATERIAL 100% POLIÉSTER, APRESENTAÇÃO 50 METROS CADA.", "M",
     "EXCLUIDO", None, "medalhao (tecido) NAO e medalha (bug substring medalha~medalhao)"),

    ("Cartucho toner impressora hp", "M",
     "EXCLUIDO", None, "toner nao e papel sulfite"),

    ("LIVRO ATA - Material: papel sulfite; Quantidade de folhas: 100 un; Gramatura: 56 g, m²", "M",
     "EXCLUIDO", None, "livro ata (produto encadernado) NAO e resma de papel sulfite A4"),

    ("LIVRO DE PROTOCOLO DE CORRESPONDÊNCIA, CAPA DURA, 100 FOLHAS, PAPEL SULFITE OFF-SET", "M",
     "EXCLUIDO", None, "livro de protocolo NAO e resma de papel"),

    ("Atendimento Odontológico - em bloco, 100 folhas, papel A4, timbre da PMVJ E SEMSA  tamanho: 30x20cm.", "S",
     "EXCLUIDO", None, "formulario medico impresso em bloco NAO e resma de papel A4"),

    ("Capa Processo formato 1: 477 x 327, gramatura: 180, material: cartolina", "M",
     "EXCLUIDO", None, "capa de processo (produto acabado) NAO e folha de cartolina (materia-prima)"),

    ("Colete Salva-Vidas tipo: classe 2, características adicionais: laranja com fitas refletivas e apito, "
     "tamanho: adulto", "M",
     "EXCLUIDO", None, "colete salva-vidas com apito NAO e apito de arbitro"),

    ("DETECTOR DE METAIS E ARMAS - Tipo: manual; Sensibilidade: com vibração ou com apito sonoro", "M",
     "EXCLUIDO", None, "detector de metais com apito sonoro NAO e apito de arbitro"),

    ("LOTE 01 - PREGO 1.1/2X13, O PREGO COM CABEÇA POSSUI CORPO LISO, CABEÇA CÔNICA E AXADREZADA E "
     "PONTA TIPO DIAMANTE.", "M",
     "EXCLUIDO", None, "prego com cabeca axadrezada NAO e jogo de xadrez (bug substring xadrez~axadrezada)"),

    ("APONTADOR C/DEPÓSITO                     Composição: Resina termoplástica e lâmina em aço inox.", "M",
     "ACEITO", "P08", "falso negativo v1: apontador com deposito deveria ser aceito"),

    ("APONTADOR: material plástico tipo escolar, cores variadas, tamanho pequeno, quantidade de furos 01, "
     "características adicionais sem deposito.", "M",
     "ACEITO", "P08", "falso negativo v1: apontador escolar generico deveria ser aceito"),

    ("Apontador lápis", "M", "ACEITO", "P08", "falso negativo v1: apontador lapis deveria ser aceito"),

    ("Armário Armário, Material:Aço, Tipo:Alto Com 02 Portas, Tipo Portas:Com Maçanetas E Chaves, Cor:Preta, "
     "Altura:1,98 M", "M",
     "EXCLUIDO", None, "armario com macanetas NAO e caneta esferografica (bug substring caneta~macanetas)"),

    ("Biscoito características adicionais: assado, ingredientes: polvilho azedo, água, óleo, ovos e sal, "
     "sabor: água e sal, tipo: bambolê", "M",
     "EXCLUIDO", None, "biscoito (alimento) com nome fantasia 'bambole' NAO e bambole esportivo"),

    ("BASTÃO - Tipo: sinalizador para trânsito; ... Com botão seletor para apito e seletor para luz "
     "piscante/fixa/lanterna/desliga.", "M",
     "EXCLUIDO", None, "bastao sinalizador de transito com apito NAO e apito de arbitro"),

    ("Apito tráfego", "M", "EXCLUIDO", None, "apito de controle de trafego NAO e apito de arbitro esportivo"),

    ("MEDALHA CONDECORATIVA - Tipo: Medalha Mérito Bombeiro Militar Imperador Dom Pedro II; ...", "M",
     "QUARENTENA", "E23", "medalha condecorativa/militar e variante fora de especificacao (revisar)"),

    ("MEDALHA - Finalidade: segurança pública; Demais especificações: conforme modelo do órgão.", "M",
     "QUARENTENA", "E23", "medalha de seguranca publica e variante fora de especificacao (revisar)"),

    ("MEDALHAS 06 cm largura PERSONALIZADAS: Medalhas em metal medindo 7cm x 7cm, ...", "S",
     "QUARENTENA", "E23", "medalha esportiva com materialOuServico=S deve ir para revisao"),

    ("Cola branca 40g", "M", "QUARENTENA", "P12", "gramatura 40g fora da especificacao (90g ou 1kg)"),

    ("LÁPIS DE COR - Material: madeira ; Cor: Diversas; quantidade cores: 24.", "M",
     "QUARENTENA", "P04", "caixa de 24 cores fora da especificacao (rol pede 12)"),

    ("RÉGUA - Tipo: escritório; Material: alumínio; Comprimento: 30 cm.", "M",
     "QUARENTENA", "P14", "regua de aluminio fora da especificacao (rol pede plastica)"),

    # casos que devem continuar ACEITOS normalmente (nao regredir)
    ("BOLA: Material: poliuretano; Tamanho: nº 04; Peso cheia: 410 a 450 g; Modalidade: futebol", "M",
     "ACEITO", "E01", "bola de futebol de campo deve continuar aceita"),
    ("BOLA FUTSAL - Circunferência: entre 62 e 63 cm; Peso: entre 400 e 440 g.", "M",
     "ACEITO", "E02", "bola de futsal deve continuar aceita"),
    ("MEDALHAS EM ACRILICO", "M", "ACEITO", "E23", "medalha esportiva comum deve continuar aceita"),
    ("Troféu", "M", "ACEITO", "E22", "trofeu simples deve continuar aceito"),
    ("Cartolina", "M", "ACEITO", "P15", "cartolina crua deve continuar aceita"),
    ("Cola branca líquida 1kg", "M", "ACEITO", "P12", "cola branca 1kg dentro da especificacao"),
    ("LÁPIS DE COR - Material: madeira; caixa com 12 cores.", "M", "ACEITO", "P04", "lapis de cor 12 cores dentro da especificacao"),
    ("PAPEL A4 - Material: papel milimetrado; Gramatura: 75 g/m².", "M", "ACEITO", "P01", "papel a4 solto continua aceito"),
    ("CONE TREINO DE AGILIDADE - Material: PVC; Altura: 20 a 24 cm; Cor: variadas.", "M",
     "ACEITO", "E10", "cone esportivo de treino continua aceito"),
    ("Serviços de Plotagem - colorida, em papel sulfite A0 de cor branca", "M",
     "QUARENTENA", "P01", "servico de plotagem continua em quarentena (nao regredir)"),

    # regressoes reais encontradas durante o reprocessamento completo da base (11221 itens)
    ("BOLA VOLEIBOL: Material: poliuretano; Peso cheia: 260 a 280 g; Circunferência: 66 a 68 cm.", "M",
     "ACEITO", "E03", "regressao: 'voleibol' (forma composta) deve continuar casando com bola de volei"),
    ("Rede Esporte aplicação: voleibol, características adicionais: tamanho: 9,50 x 1,00m.", "M",
     "ACEITO", "E07", "regressao: rede de voleibol (forma composta) deve continuar casando"),
    ("BOLA DE BASQUETEBOL - Peso: 175 gramas; Diâmetro: 24 Centímetros; Material: borracha.", "M",
     "ACEITO", "E04", "regressao: bola de basquetebol (forma composta) deve continuar casando"),
    ("BOMBA ENCHER: Material corpo: plástico; Material Bico: metal; Aplicação: para bola esportiva; "
     "Características adicionais: dupla ação.", "M",
     "ACEITO", "E14", "regressao: bomba encher para bola esportiva deve continuar aceita"),

    ("Pincel pintura predial", "M",
     "EXCLUIDO", None, "pincel de pintura predial (construcao civil) NAO e pincel escolar"),
    ("Balança Antropométrica para obesos MODO DE OPERAÇÃO: DIGITAL ... RÉGUA ANTROPOMÉTRICA: INTEGRADA, "
     "COM COMPRIMENTO DE ATÉ 2 METROS; ... PRECISÃO: GRAU DE EXATIDÃO COMPATÍVEL COM USO CLÍNICO", "M",
     "EXCLUIDO", None, "regua antropometrica de balanca medica NAO e regua escolar plastica"),
]


def main():
    falhas = 0
    for desc, mos, status_esp, pid_esp, obs in CASOS:
        r = classificar_item(desc, material_ou_servico=mos)
        ok_status = r["status"] == status_esp
        ok_pid = (r["produto_id"] == pid_esp)
        ok = ok_status and ok_pid
        marca = "OK  " if ok else "FALHA"
        if not ok:
            falhas += 1
        print("[%s] esperado=(%s,%s) obtido=(%s,%s) | %s" % (
            marca, status_esp, pid_esp, r["status"], r["produto_id"], obs))
        if not ok:
            print("       descricao: %s" % desc[:120])
            print("       motivo obtido: %s" % r["motivo"])

    print("\n=== %d/%d casos passaram ===" % (len(CASOS) - falhas, len(CASOS)))
    if falhas:
        sys.exit(1)


if __name__ == "__main__":
    main()
