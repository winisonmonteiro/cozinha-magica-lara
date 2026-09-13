# -*- coding: utf-8 -*-
"""Regras de negocio auxiliares: cancelamento de resultados e classificacao
de esfera administrativa. Versao 2 corrige o bug de cancelamento
identificado na auditoria (situacao explicita "Cancelado" sem data/motivo
preenchidos era tratada como NAO cancelado)."""
import re
import unicodedata

CANCEL_SENTINELAS = {None, "", "0001-01-01T00:00:00"}


def strip_acentos(txt):
    if txt is None:
        return ""
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c)).lower()


def avaliar_cancelamento(resultado):
    """Retorna (cancelado_bool, motivo_avaliacao_str).

    Bug v1 comprovado na base recebida: numero_controle_pncp
    00394502001205-1-000471/2025 item 3 tem situacao_resultado_nome=
    'Cancelado' mas dataCancelamento e motivoCancelamento vazios/None,
    e a v1 concluia "nao cancelado" por falta de data/motivo. A situacao
    explicita do proprio PNCP e evidencia suficiente por si so.
    """
    data_cnl = resultado.get("dataCancelamento")
    motivo_cnl = resultado.get("motivoCancelamento")
    situacao_nome = strip_acentos(resultado.get("situacaoCompraItemResultadoNome") or "")

    data_valida = data_cnl not in CANCEL_SENTINELAS
    tem_motivo = bool(motivo_cnl and str(motivo_cnl).strip())
    situacao_indica_cancelado = bool(re.search(r"\bcancelad", situacao_nome))

    if situacao_indica_cancelado:
        # Situacao explicita do PNCP e evidencia suficiente por si so;
        # data/motivo ausentes NAO tornam o item elegivel (correcao v2).
        detalhe_extra = []
        if not data_valida:
            detalhe_extra.append("data de cancelamento ausente/sentinela")
        if not tem_motivo:
            detalhe_extra.append("motivo de cancelamento ausente")
        extra = (" (%s, mas situacao oficial e suficiente)" % "; ".join(detalhe_extra)) if detalhe_extra else ""
        return True, "situacao oficial='%s' declara cancelamento%s" % (
            resultado.get("situacaoCompraItemResultadoNome"), extra)

    if data_valida and tem_motivo:
        return True, "data de cancelamento valida + motivo preenchido (situacao nao rotulada como cancelada)"

    if data_valida and not tem_motivo:
        return False, ("evidencia contraditoria: data de cancelamento preenchida mas sem motivo e sem "
                        "situacao de cancelamento; revisar manualmente antes de tratar como cancelado ou elegivel")

    return False, "sem evidencia de cancelamento (situacao nao indica cancelado; sem data/motivo validos)"


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
