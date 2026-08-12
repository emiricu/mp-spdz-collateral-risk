# Nodo della rete MPC (mascot, field, produzione)

Un nodo = una piattaforma di lending, un container-set (`api` + `worker` +
`redis`) deployato su una macchina propria. Due nodi (party 0 = piattaforma
richiedente, party 1 = controparte) formano la rete a N=2 usata da
`Programs/Source/crypto_collateral_risk.mpc` compilato per `mascot`,
dominio `field`, `mode=2` (`decide_production`).

Questo giro costruisce **solo i due nodi**, asincroni via coda, con
callback HTTP diretta a fine calcolo. Smart contract per la pubblicazione
on-chain resta deliberatamente fuori scope. Un backend minimo e un
frontend per lanciare un preventivo reale da browser esistono ora in
`deploy/README.md` (demo pubblica, non un backend "di produzione").

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

(`durata_secondi` qui &egrave; solo illustrativo &mdash; vedi &sect;8 per
i tempi reali confermati su ripetizioni, non un campione singolo.)

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

## 8. Affidabilit&agrave;: verifica ripetuta, non un campione singolo

Un primo run di successo e uno di fallimento erano gi&agrave; stati
verificati una volta ciascuno (vedi commit `3f16539a`). Un singolo
campione non basta a sapere se un tempo &egrave; rappresentativo &mdash;
stesso principio gi&agrave; applicato al retest controllato mascot/mama
in `results_retest_controllato/`. Questa sezione riporta i numeri
confermati su ripetizioni, non il primo campione.

### 8.1 Tempo di calcolo, 5 ripetizioni, rete Docker Compose condivisa

Stesso scenario (`local-test/`), stack riavviato da zero tra una
ripetizione e l'altra (`local-test/run_repeated_success.py`, dati grezzi
in `local-test/results/run1..5.json`):

| | party 0 | party 1 |
|---|---|---|
| media | 46.4s | 46.9s |
| min&ndash;max | 41.0s&ndash;54.3s | 41.5s&ndash;54.9s |
| coefficiente di variazione | 10.7% | 10.6% |

Coerente con l'ordine di grandezza gi&agrave; visto nel retest controllato
mascot/mama (CV 5&ndash;29% a seconda della combinazione), non un fattore
&times;2: il tempo &egrave; affidabile. Il campione singolo pubblicato in
precedenza (34.07s/34.48s) era, con questi dati alla mano, il pi&ugrave;
veloce mai osservato &mdash; un campione fortunato, non rappresentativo
della media, anche se dentro l'ordine di grandezza corretto.

### 8.2 Stessa verifica, senza la scorciatoia della rete Docker condivisa

`local-test/docker-compose.yml` mette i due nodi sulla stessa rete
Docker di un singolo progetto compose &mdash; si trovano per nome
servizio tramite il DNS interno di Compose, una scorciatoia che due
macchine fisicamente separate non avrebbero mai. Per eliminarla restando
comunque su una macchina sola: `local-test/run_repeated_strangers.py`
mette i due nodi su **due reti Docker isolate e non collegate tra loro**
(`docker network create`, non un compose condiviso), pubblica la porta
MP-SPDZ di party 0 sull'host con mappatura **1:1**, e imposta
`PARTY0_HOST` all'indirizzo IP reale dell'interfaccia di rete della
macchina (`hostname -I`) &mdash; mai un nome di servizio Docker, mai
`localhost`. L'unico percorso tra i due nodi &egrave; quello che
l'host instraderebbe davvero, lo stesso meccanismo (`getsockname()` su
un indirizzo effettivamente routabile) che servir&agrave; su due macchine
fisiche.

5 ripetizioni, dati grezzi in `local-test/results/strangers_run1..5.json`:

| | party 0 | party 1 |
|---|---|---|
| media | 54.3s | 54.7s |
| min&ndash;max | 51.2s&ndash;58.8s | 51.6s&ndash;59.3s |
| coefficiente di variazione | 6.5% | 6.4% |

5/5 riuscite, varianza ancora pi&ugrave; bassa che nel test con rete
condivisa. Pi&ugrave; lento in media (54.3s contro 46.4s, +17%) &mdash;
plausibile, un salto NAT/routing aggiuntivo attraverso l'host invece di
un bridge Docker diretto, e comunque un costo piccolo e stabile, non un
segnale di inaffidabilit&agrave;.

**Bug trovato e risolto durante la costruzione di questo test** (nello
script di verifica, non nell'app): la prima versione pubblicava la porta
di party 0 con una mappatura NON 1:1 (host 15000 &rarr; container 5000).
`mascot-party.x` usa lo stesso valore numerico di `-pn` sia come "la mia
porta locale" sia come "la porta su cui gli altri mi trovano" &mdash; con
una mappatura diversa, party 1 tentava di contattare `PARTY0_HOST:5000`,
una porta che l'host non pubblicava affatto (il vero endpoint era
`:15000`). Manifestato come un blocco di 600s senza errori (connessione
rifiutata silenziosamente ritentata), non distinguibile a occhio da un
bug applicativo &mdash; verificato con un test di connettivit&agrave;
diretto (`/dev/tcp/...`) prima di individuare la causa. **La mappatura
1:1 &egrave; un requisito, non un dettaglio di configurazione.**

Un fallimento transitorio isolato (`Connection reset by peer`,
livello SSL, guarito da solo al tentativo successivo) &egrave; comparso
una volta durante la diagnosi manuale di quel bug, con container in uno
stato non pulito; non si &egrave; pi&ugrave; ripresentato nelle 5
ripetizioni pulite (stack ricreato da zero ogni volta). Non escluso con
certezza come possibile hairpin-NAT occasionale (un container che
raggiunge un altro container dello stesso host passando dall'IP
pubblico dell'host stesso) &mdash; una classe di comportamento di rete
nota per essere meno prevedibile del routing diretto tra due macchine
reali. Non riprodotto in modo sistematico; segnalato qui per completezza,
non nascosto.

### 8.3 Test cross-macchina vero (due host fisicamente separati)

**Non eseguito in questa sessione** &mdash; nessuna seconda macchina
disponibile. Il test 8.2 copre lo stesso percorso di codice (indirizzo
host reale, non un bridge Docker) e lo stesso meccanismo di rete che due
macchine separate userebbero, ma **non** &egrave; equivalente a una
verifica su due macchine fisiche reali: niente hairpin NAT da
considerare, latenza di rete reale invece che quella (minima) di un
singolo host, un firewall/router reale in mezzo invece di iptables
locale. **Questo resta l'unico passaggio della checklist di
affidabilit&agrave; non ancora verificato.** Da fare prima di dichiarare
il prototipo pronto per un deployment reale a due macchine, non solo
"probabilmente pronto".

### 8.4 Percorso di fallimento, ripetuto

Backend fittizio fermato a met&agrave; sessione, richiesta sottomessa:
comportamento **identico** su due ripetizioni indipendenti &mdash; 6/6
tentativi di callback falliti con backoff esponenziale osservato nei log,
calcolo comunque completato correttamente su entrambe le parti, risultato
sempre leggibile via `GET /stato/<request_id>`, ripristino confermato
riavviando il backend.

### 8.5 Un party che muore a met&agrave; calcolo (nuovo scenario)

Non ancora testato prima di questo giro: container `worker` di un party
ucciso (`docker kill`, non uno stop pulito) mentre `mascot-party.x` &egrave;
attivamente in corso (CPU al 74%, non in attesa), non prima dell'avvio.
Osservato sul party superstite:

- **Rilevamento in meno di 1 secondo**, non un blocco indefinito:
  `Fatal error in communication: read_some: stream truncated
  [asio.ssl.stream:1] -- This is probably because party 1 encountered a
  problem.` &mdash; `mascot-party.x` esce da solo con un errore esplicito
  non appena il socket SSL verso il party ucciso si chiude di colpo.
- Stato aggiornato a `fallito` con l'errore completo, **callback
  consegnata correttamente** al backend (o al backend fittizio) nello
  stesso istante &mdash; nessun blocco a valle.
- **Limite osservato, non un malfunzionamento**: `GET /stato` sul party
  **ucciso** (una volta che il suo container/API tornano su) resta
  fermo su `"in corso"` per sempre per quella specifica richiesta &mdash;
  il worker &egrave; morto prima di poter aggiornare il proprio stato.
  Il chiamante impara comunque che la richiesta &egrave; fallita tramite
  il party superstite (callback o `/stato`), ma interrogare
  direttamente il nodo caduto per quella richiesta, isolatamente,
  sarebbe fuorviante.
- **Limite osservato in `/salute`**: durante il test, l'endpoint
  `/salute` del party ucciso ha continuato a rispondere `ok: true` anche
  a worker morto &mdash; controlla solo Redis raggiungibile e la
  presenza di file statici (binario, programma compilato, certificati),
  mai se il processo worker &egrave; effettivamente vivo. Un monitoraggio
  basato solo su `/salute` non rileverebbe questo scenario. Non corretto
  in questo giro (nessun bug nel comportamento del party superstite da
  correggere, solo una lacuna di osservabilit&agrave; nel party morto,
  fuori dallo scope "verifica di affidabilit&agrave;" di questo giro) --
  segnalato esplicitamente qui, non nascosto.

Dati grezzi in `local-test/results/kill_mid_computation.json`.

## 9. Log

`Player-Data/`, `node_logs/<request_id>.log` (stdout/stderr grezzo del
processo `mascot-party.x` per quella run, non solo il risultato
decodificato) sono su volumi montati (`player-data`, `node-logs`),
sopravvivono ai riavvii dei container.
