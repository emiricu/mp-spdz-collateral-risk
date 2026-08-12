# Nodo della rete MPC (mascot, field, produzione)

Un nodo = una piattaforma di lending, un container-set (`api` + `worker` +
`redis`) deployato su una macchina propria. Due nodi (party 0 = piattaforma
richiedente, party 1 = controparte) formano la rete a N=2 usata da
`Programs/Source/crypto_collateral_risk.mpc` compilato per `mascot`,
dominio `field`, `mode=2` (`decide_production`).

Questo giro costruisce **solo i due nodi**, asincroni via coda, con
callback HTTP diretta a fine calcolo. Backend "vero" e smart contract per
la pubblicazione on-chain sono deliberatamente fuori scope qui.

## 1. Build

```
./node/build.sh
```

Due step, entrambi riuso di meccanismi gi&agrave; esistenti nel repo, nessun
metodo di build nuovo:

1. `docker build --target machine --build-arg machine=mascot-party.x -t mpspdz-mascot:local .`
   &mdash; lo stage `machine` gi&agrave; documentato nel `Dockerfile` alla
   radice del repo.
2. `docker build -f node/Dockerfile -t collateral-node:local .` &mdash; compila
   `crypto_collateral_risk.mpc` per `field 2 0 2` (stesso programma, stessi
   argomenti gi&agrave; validati in `results_produzione/n2/`) e aggiunge
   l'app Python (API + worker), stessa immagine per entrambi i ruoli.

## 2. Configurazione, una macchina per volta

Copia `node/.env.example` in `node/.env` su **ciascuna** macchina e
compila:

| Variabile | Macchina party 0 | Macchina party 1 |
|---|---|---|
| `PARTY_ID` | `0` | `1` |
| `PARTY0_HOST` | il proprio indirizzo raggiungibile (**mai** `localhost`) | indirizzo raggiungibile della macchina party 0 |
| `PORT_BASE` / `PORT_BASE_PLUS1` | `5000` / `5001` (deve combaciare tra le due macchine) | idem |
| `HTTP_PORT` | porta dell'API di questo nodo | idem |
| `POLICY_VERSION` | `1` (deve combaciare) | idem |

**`PARTY0_HOST` va impostato su entrambe le macchine, incluso il valore
sulla macchina di party 0 stesso.** Non &egrave; ovvio, verificato
direttamente (non solo dedotto da `--help`): l'handshake di startup di
MP-SPDZ fa s&igrave; che **anche party 0** si connetta, come client, al
proprio indirizzo `-h` per registrarsi presso il proprio coordinatore
appena avviato; l'indirizzo IP che annuncia da quella connessione
(via `getsockname()`) &egrave; poi quello che party 1 user&agrave; per
il canale dati vero e proprio. Con `-h localhost` su party 0, l'indirizzo
annunciato sarebbe `127.0.0.1` &mdash; irraggiungibile da una macchina
(o container) diversa: il run si blocca indefinitamente subito dopo
l'handshake iniziale, senza alcun messaggio d'errore. Riprodotto e
risolto empiricamente durante il test end-to-end di questo giro, non
assunto dalla documentazione del framework.

Poi, su ciascuna macchina:

```
cd node
docker compose --env-file .env up -d
```

## 3. Porte da aprire tra le due macchine

- **`PORT_BASE` e `PORT_BASE_PLUS1`** (default 5000-5001), sul container
  `worker` &mdash; sono le porte che `mascot-party.x` apre per la
  coordinazione (party 0) e il canale dati tra le due parti. Vanno aperte
  in entrambe le direzioni tra le due macchine. `-t`/multi-thread pu&ograve;
  aprire porte aggiuntive oltre a queste due per N=2 con thread multipli:
  se in futuro si abilita il multi-threading, allargare il range aperto
  sul firewall di conseguenza (non fatto qui, thread singolo per
  semplicit&agrave;).
- **`HTTP_PORT`** (default 8000), sul container `api` &mdash; solo se il
  backend (prossimo giro) gira su una terza macchina; se gira sulla
  stessa rete privata dei due nodi, restringere comunque l'accesso al
  solo IP del backend.
- **Redis non va mai esposto fuori dalla macchina** &mdash; nessuna riga
  `ports:` per `redis` in `docker-compose.yml`, di proposito.

## 4. Canali cifrati (`-e`) e scambio certificati

`mascot-party.x` gira sempre con `-e` (canali cifrati) in questo
deployment &mdash; verificato via `--help` che **non esiste un flag `-u`**
per disattivarlo: `-e` &egrave; un flag di opt-in (default: canali in
chiaro), non un default che si sta preservando. Con due macchine
realmente separate su una rete che non si controlla del tutto, restare
su `-e` è la scelta giusta comunque.

`-e` richiede un certificato self-signed per parte (stesso meccanismo di
`Scripts/generate-cert.sh`/`Scripts/setup-ssl.sh` gi&agrave; nel repo,
verificato in `Networking/ssl_sockets.h`: directory `Player-Data/`,
lookup via hash OpenSSL). Al primo avvio, il container `worker` genera
**da solo** la propria coppia chiave/certificato (`Player-Data/P<PARTY_ID>.pem`
+ `.key`) e non li rigenera nei riavvii successivi (volume persistente).

**La chiave privata non lascia mai la macchina che l'ha generata.** Il
certificato pubblico (`.pem`, mai il `.key`) va scambiato fuori banda tra
i due operatori:

1. Avvia `docker compose up -d` su entrambe le macchine (i worker
   generano i propri certificati al boot, poi restano in attesa: `/salute`
   segnaler&agrave; `certificato_peer: false` finch&eacute; non completi
   questo passaggio).
2. Recupera `Player-Data/P0.pem` dalla macchina party 0 (es.
   `docker compose cp worker:/usr/src/MP-SPDZ/Player-Data/P0.pem .`) e
   mandalo alla macchina party 1 con qualunque canale fuori banda
   (email, chiavetta, gestore segreti &mdash; **mai** attraverso la
   stessa rete non fidata che si sta proteggendo con `-e`, se possibile).
3. Copia `P0.pem` dentro `Player-Data/` sulla macchina party 1 (stesso
   volume montato dal container `worker`), e viceversa `P1.pem` sulla
   macchina party 0.
4. Su entrambe le macchine, rigenera l'indice hash OpenSSL:
   `docker compose exec worker sh -c "c_rehash Player-Data || openssl rehash Player-Data"`.
5. Verifica `GET /salute` su entrambe: `certificato_locale` e
   `certificato_peer` devono essere entrambi `true`.

Questo passaggio &egrave; manuale **di proposito** in un deployment reale a
due macchine: automatizzarlo (es. copiare i certificati attraverso la
stessa rete che il canale cifrato dovrebbe proteggere) vanificherebbe in
parte lo scopo di `-e`. Per i test in locale, vedi `local-test/`, dove lo
scambio &egrave; automatizzato apposta perch&eacute; entrambi i volumi sono
sulla stessa macchina di sviluppo.

## 5. API

### `POST /calcola`

Corpo per il nodo **party 0** (richiedente):

```json
{
  "request_id": "req-123",
  "callback_url": "https://backend.example.com/callback",
  "wallet_address": "0xclean0000000000000000000000000000000001",
  "existing_debt_usd": 10000,
  "new_loan_usd": 50000,
  "collateral_asset": "BTC",
  "collateral_amount": 3.5
}
```

Corpo per il nodo **party 1** (controparte):

```json
{
  "request_id": "req-123",
  "callback_url": "https://backend.example.com/callback",
  "existing_debt_usd": 10000
}
```

Risposta immediata (**non** attende la fine del calcolo MPC):

```
202 Accepted
{"request_id": "req-123", "stato": "in coda", "party_id": 0}
```

Nota per chi costruir&agrave; il backend: l'MPC richiede entrambe le parti
online contemporaneamente &mdash; sottometti lo **stesso `request_id`** a
entrambi i nodi in parallelo, non in sequenza con un ritardo arbitrario.
I due worker seriali locali garantiscono "un calcolo alla volta" su
ciascun nodo, ma l'accoppiamento delle due sottomissioni per la stessa
richiesta resta responsabilit&agrave; del chiamante.

### `GET /stato/<request_id>`

Fallback via polling se una callback andasse persa. Stati possibili:
`in coda`, `in corso`, `completato`, `fallito`.

### `GET /salute`

`200` se il processo, Redis, il binario `mascot-party.x`, il programma
compilato e il certificato locale sono tutti presenti; `503` altrimenti
(dettaglio nel corpo). Uso previsto: readiness probe.

## 6. Callback a fine calcolo

Successo:

```json
{
  "request_id": "req-123",
  "stato": "completato",
  "party_id": 0,
  "esito": "REVIEW MANUALE",
  "risk_score": 65,
  "max_ltv_bps": 5000,
  "liq_threshold_bps": 6000,
  "fascia_esposizione": 1,
  "durata_secondi": 83.4
}
```

Fallimento (crash, timeout, errore di rete tra i due party &mdash; la
callback parte comunque, mai un backend lasciato in attesa indefinita):

```json
{
  "request_id": "req-123",
  "stato": "fallito",
  "party_id": 1,
  "errore": "timeout dopo 600.0s (budget 600s)"
}
```

### Retry della callback

Se la `POST` al `callback_url` fallisce (connessione rifiutata, timeout,
status &ge;300): backoff esponenziale, base 2s, fino a 6 tentativi
(2s, 4s, 8s, 16s, 32s &mdash; ultimo tentativo senza attesa successiva,
~62s totali nel caso peggiore). Configurabile via `CALLBACK_MAX_RETRIES`
e `CALLBACK_BACKOFF_BASE_SECONDS`. Se tutti i tentativi falliscono, il
risultato resta comunque leggibile via `GET /stato/<request_id>` &mdash;
loggato come errore, non perso. Nota: con worker a concorrenza 1, il
retry blocca il prossimo job in coda per la durata dei tentativi &mdash;
tradeoff accettato per un prototipo a singolo worker per nodo.

## 7. Test end-to-end manuale (due nodi, stessa macchina, `local-test/`)

Vedi `local-test/README.md` per il flusso completo con `curl` contro il
backend fittizio incluso in questo giro.

## 8. Log

`Player-Data/`, `node_logs/<request_id>.log` (stdout/stderr grezzo del
processo `mascot-party.x` per quella run, non solo il risultato
decodificato) sono su volumi montati (`player-data`, `node-logs`),
sopravvivono ai riavvii dei container.
