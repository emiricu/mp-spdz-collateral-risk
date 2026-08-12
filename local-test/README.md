# Test end-to-end locale (DEV ONLY)

Entrambi i nodi (api+worker+redis ciascuno) pi&ugrave; un backend fittizio,
tutti sulla stessa macchina via rete Docker interna. **Non** &egrave; una
modalit&agrave; di deploy: serve solo a verificare il flusso
sottomissione&rarr;coda&rarr;callback senza gi&agrave; avere due macchine o
un backend vero. Vedi `node/README.md` per il deploy reale a due macchine.

## Avvio

```
./node/build.sh                    # da eseguire una volta, dalla radice del repo
cd local-test
docker compose up -d
```

Attendi che `cert-exchange` completi (`docker compose ps` &mdash; deve
risultare `Exited (0)`) prima di sottomettere richieste: i worker
aspettano proprio quel completamento prima di partire.

## Test del percorso di successo

Stesso scenario gi&agrave; validato in `results_produzione/n2/mascot.json`
(stesso wallet, stesso collateral, stesso debito totale diviso in parti
uguali tra le due piattaforme &mdash; $10.000 ciascuna):

```bash
REQ=test-$(date +%s)

curl -s -X POST http://localhost:8000/calcola \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "'"$REQ"'",
    "callback_url": "http://fake-backend:9000/callback-test",
    "wallet_address": "0xclean0000000000000000000000000000000001",
    "existing_debt_usd": 10000,
    "new_loan_usd": 50000,
    "collateral_asset": "BTC",
    "collateral_amount": 3.5
  }'
# atteso: 202 {"request_id": "...", "stato": "in coda", "party_id": 0}

curl -s -X POST http://localhost:8001/calcola \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "'"$REQ"'",
    "callback_url": "http://fake-backend:9000/callback-test",
    "existing_debt_usd": 10000
  }'
# atteso: 202 {"request_id": "...", "stato": "in coda", "party_id": 1}
```

Nota: `callback_url` punta al servizio `fake-backend` **con il nome del
servizio Docker**, non `localhost` &mdash; i nodi lo raggiungono sulla
rete Docker interna di questo compose, non sull'host.

Segui l'avanzamento:

```bash
watch -n2 "curl -s http://localhost:8000/stato/$REQ; echo; curl -s http://localhost:8001/stato/$REQ"
```

A calcolo concluso (fino a ~1-2 minuti, coerente con i tempi gi&agrave;
misurati in `results_produzione/n2/`), verifica che il backend fittizio
abbia ricevuto **entrambe** le callback, con `esito`/`risk_score`
identici tra loro:

```bash
curl -s "http://localhost:9000/callback-test/$REQ" | python3 -m json.tool
```

Devi vedere due oggetti (`party_id: 0` e `party_id: 1`), entrambi
`"stato": "completato"`, stesso `esito` e `risk_score`, coerenti con
`results_produzione/n2/mascot.json`.

## Test del percorso di fallimento (backend irraggiungibile + retry)

```bash
docker compose stop fake-backend

REQ2=test-fail-$(date +%s)
curl -s -X POST http://localhost:8000/calcola -H 'Content-Type: application/json' \
  -d '{"request_id":"'"$REQ2"'","callback_url":"http://fake-backend:9000/callback-test","wallet_address":"0xclean0000000000000000000000000000000001","existing_debt_usd":10000,"new_loan_usd":50000,"collateral_asset":"BTC","collateral_amount":3.5}'
curl -s -X POST http://localhost:8001/calcola -H 'Content-Type: application/json' \
  -d '{"request_id":"'"$REQ2"'","callback_url":"http://fake-backend:9000/callback-test","existing_debt_usd":10000}'
```

Segui i log del worker per vedere i tentativi con backoff:

```bash
docker compose logs -f node0-worker
```

Atteso: righe `callback fallita (tentativo N/6) ...`, con pause
crescenti (2s, 4s, 8s...), poi `callback non riuscita dopo 6 tentativi`.
Nel frattempo il risultato resta comunque leggibile:

```bash
curl -s http://localhost:8000/stato/$REQ2 | python3 -m json.tool
```

Deve mostrare `"stato": "completato"` (o `"fallito"` con `errore`, a
seconda dello scenario) anche se **nessuna** callback &egrave; mai
arrivata a destinazione. Poi riavvia il backend e conferma che una nuova
richiesta torni a funzionare normalmente:

```bash
docker compose start fake-backend
```

Se qualcosa non coincide con quanto descritto qui, o la callback di
fallimento non parte affatto, &egrave; un bug da investigare prima di
considerare i nodi pronti.
