#!/bin/bash
# Comando unico: da un checkout pulito, prepara l'ambiente e lancia il
# primo giro di test. Pensato per essere idempotente -- rilanciarlo su un
# ambiente già pronto salta rapidamente i passi già fatti (setup_tutorial.sh
# e make usano dipendenze/timestamp, setup-ssl.sh sovrascrive i certificati
# senza chiedere).
#
# Uso: ./run_all.sh [dominio] [N certificati]
#   dominio       field (default) | ring | binary | astra | trio | yao
#   N certificati quante coppie di certificati generare (default 20 --
#                 vedi il report: N=100 in locale ha già causato un crash
#                 della VM di test, si sale con cautela)

set -e

DOMAIN=${1:-field}
CERT_N=${2:-20}

echo "=== 1/4: setup ambiente (submodule, dipendenze, boost, make setup) ==="
./setup_tutorial.sh

echo "=== 2/4: certificati SSL per $CERT_N parti ==="
Scripts/setup-ssl.sh "$CERT_N"

echo "=== 3/4: dati di input di esempio (scenario 'approvato', N=2 piattaforme) ==="
mkdir -p Player-Data
python3 Scripts/crypto_risk_run.py \
    --wallet 0xclean0000000000000000000000000000000001 \
    --platform-debts 0,0 --new-loan 50000 \
    --collateral-asset BTC --collateral-amount 3.5 \
    --domain "$DOMAIN" --protocol semi >/dev/null
# crypto_risk_run.py scrive già Input-P<i>-0 per ogni piattaforma -- lo si
# richiama qui solo per popolare Player-Data prima del giro di benchmark
# generico sotto (che esercita l'intero catalogo di protocolli, non solo
# 'semi'); i valori restano gli stessi scenario per scenario.

echo "=== 4/4: primo giro di test -- dominio $DOMAIN ==="
python3 Scripts/mpc_bench.py sweep --domain "$DOMAIN"

echo
echo "=== fatto. Altri comandi utili: ==="
echo "  ./Scripts/crypto_risk_run.py --wallet 0x... --platform-debts 0,0 --new-loan 50000 \\"
echo "      --collateral-asset BTC --collateral-amount 3.5 --domain field --protocol semi"
echo "  python3 Scripts/mpc_bench.py sweep --domain ring"
echo "  python3 Scripts/mpc_bench.py sweep --domain binary"
echo "  python3 Scripts/mpc_bench.py scale --n-list 10,20"
echo "  python3 Scripts/mpc_bench.py fixed"
