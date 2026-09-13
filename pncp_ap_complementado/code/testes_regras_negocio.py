# -*- coding: utf-8 -*-
"""Testes reais do fix de cancelamento, incluindo o caso comprovado no
banco recebido (00394502001205-1-000471/2025 item 3)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regras_negocio import avaliar_cancelamento

CASOS = [
    (dict(situacaoCompraItemResultadoNome="Cancelado", dataCancelamento=None, motivoCancelamento=None),
     True, "bug real do banco: situacao=Cancelado sem data/motivo DEVE ser tratado como cancelado"),
    (dict(situacaoCompraItemResultadoNome="Cancelado", dataCancelamento="0001-01-01T00:00:00", motivoCancelamento=""),
     True, "sentinela de data nao anula situacao explicita de cancelamento"),
    (dict(situacaoCompraItemResultadoNome="Informado", dataCancelamento=None, motivoCancelamento=None),
     False, "situacao normal sem evidencia de cancelamento -> nao cancelado"),
    (dict(situacaoCompraItemResultadoNome="Informado", dataCancelamento="2025-05-10T00:00:00",
          motivoCancelamento="Desistencia do fornecedor"),
     True, "data valida + motivo preenchido, mesmo com situacao nao rotulada -> cancelado"),
    (dict(situacaoCompraItemResultadoNome="Informado", dataCancelamento="2025-05-10T00:00:00", motivoCancelamento=None),
     False, "data valida sem motivo e sem situacao de cancelamento -> evidencia contraditoria, nao assumir cancelado"),
    (dict(situacaoCompraItemResultadoNome="Cancelada", dataCancelamento=None, motivoCancelamento=None),
     True, "variacao de genero 'Cancelada' tambem deve ser reconhecida"),
]


def main():
    falhas = 0
    for resultado, esperado, obs in CASOS:
        cancelado, motivo = avaliar_cancelamento(resultado)
        ok = cancelado == esperado
        if not ok:
            falhas += 1
        print("[%s] esperado=%s obtido=%s | %s" % ("OK  " if ok else "FALHA", esperado, cancelado, obs))
        if not ok:
            print("       motivo: %s" % motivo)
    print("\n=== %d/%d casos passaram ===" % (len(CASOS) - falhas, len(CASOS)))
    if falhas:
        sys.exit(1)


if __name__ == "__main__":
    main()
