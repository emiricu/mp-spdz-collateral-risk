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
   segnaler&agrave; `certificati_peer` con `false` per ogni peer mancante
   finch&eacute; non completi questo passaggio -- un oggetto con una voce
   per peer, non pi&ugrave; un singolo booleano, da quando il prototipo
   supporta anche N=3, vedi deploy/deploy_nodes.sh).
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
5. Verifica `GET /salute` su entrambe: `certificato_locale` deve essere
   `true` e ogni voce di `certificati_peer` deve essere `true`.

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
  "port_base": 20000,
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
  "port_base": 20000,
  "existing_debt_usd": 10000
}
```

`port_base` &egrave; **obbligatorio**, campo aggiunto in &sect;9.6: il
chiamante (l'orchestratore) deve assegnarne uno per richiesta, mai un
valore fisso/riusato, e mandare lo **stesso valore a tutti gli N nodi**
per quella richiesta &mdash; vedi &sect;9.6 per il perch&eacute; e la
garanzia offerta contro il riuso.

Risposta immediata (**non** attende la fine del calcolo MPC):

```
202 Accepted
{"request_id": "req-123", "stato": "in coda", "party_id": 0}
```

Nota per chi costruir&agrave; il backend: l'MPC richiede tutte le parti
online contemporaneamente &mdash; sottometti lo **stesso `request_id`**
(e lo stesso `port_base`) a tutti i nodi in parallelo, non in sequenza
con un ritardo arbitrario. I worker seriali locali garantiscono "un
calcolo alla volta" su ciascun nodo, ma l'accoppiamento delle
sottomissioni per la stessa
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

## 9. Estensione a N=3

### 9.1 Cosa cambia rispetto a N=2, non solo "un nodo in pi&ugrave;"

`Programs/Source/crypto_collateral_risk.mpc` non &egrave; un file a parte
per la produzione: mode 2 (`decide_production`) &egrave; gi&agrave; lo
stesso file, selezionato via argomento di compilazione. A N=3 cambia il
comportamento del calcolo stesso: il branch gi&agrave; presente nel
programma (`if N_PLATFORMS == 2 ... else ...`) rivela una fascia 0-3
a N=2, ma il **valore esatto in dollari** a N&ge;3, perch&eacute; da
tre piattaforme in su nessuna pu&ograve; pi&ugrave; isolare per
sottrazione il contributo delle altre. Compilato con
`./compile.py -o field crypto_collateral_risk 3 0 2`, prodotto
`field-crypto_collateral_risk-3-0-2`, stessa immagine `collateral-node:local`
di N=2 (entrambi i programmi coesistono, selezionati a runtime da
`N_PLATFORMS`/`MP_SPDZ_PROG`, vedi `node/app/config.py`).

Verificato direttamente in `Networking/Player.cpp` (`PlainPlayer::setup_sockets`,
`default_port = portnum_base + playerno`): a N=2 solo la porta di party 0
deve essere raggiungibile dall'esterno (party 1 chiama solo verso il
basso). A N=3 anche la porta di party 1 deve esserlo, perch&eacute;
party 2 chiama verso il basso anche verso di lui &mdash;
`deploy/deploy_nodes.sh` pubblica quindi la porta MP-SPDZ di **tutti e
tre** i nodi 1:1 sull'host, non solo quella di party 0 come bastava a N=2.

### 9.2 Percorso di successo, ripetuto

Scenario di riferimento identico a `results_produzione/n3/` (stesso
totale di debito della verifica N=2, diviso su tre piattaforme invece di
due, stessa logica degli sweep N=2/3/4/5): wallet
`0xclean0000000000000000000000000000000001`, prestito $50.000,
collateral 3,5 BTC, debiti 6.667/6.667/6.666. Sottomesso tramite
l'orchestratore reale (`/api/preventivo` del backend pubblico), non un
harness separato.

| Batch | Ripetizioni | Esito |
|---|---|---|
| 1, senza pausa tra le richieste | 3 | 2 successo (255,4s / 246,4s), 1 fallito (268,3s, `stream truncated`) |
| 2, senza pausa | 3 | 1 successo (297,9s), 2 falliti (2,1s `connection reset`; 601,7s misto SIGPIPE/timeout/connection reset) |
| 3, pausa di 30s tra le richieste | 3 | 3/3 successo (308,2s / 292,5s / 560,0s&sup1;) |
| Finale, isolata dopo il fix del &sect;9.4 | 1 | successo, 270,6s, party0/1/2 = 269,01/269,53/269,52s |

&sup1; il terzo tempo include ~5 minuti di attesa in coda legittima
dietro una richiesta indipendente di un visitatore reale del sito
(IP `94.33.118.202`, lo stesso gi&agrave; osservato nell'incidente del
rate limit) sottomessa durante il test: nessuna contaminazione tra le
due richieste, ciascuna ha ottenuto il proprio risultato corretto e
isolato (verificato leggendo entrambi i record da Redis). Il tempo di
calcolo MPC proprio di quella ripetizione era 267-268s, in linea con le
altre.

In ogni successo, le tre piattaforme hanno riportato **esattamente** lo
stesso `esito`/`risk_score`, e `esposizione_totale_usd` &egrave; sempre
comparso come valore esatto ($70.000 = 6.667+6.667+6.666+50.000), mai
come fascia &mdash; conferma diretta che sia il branch N=3 del `.mpc`
sia la nuova logica di correlazione N-via dell'orchestratore
(`backend/app.py`'s `_finalize_if_all_reported`, che ora attende
**tutte** le N risposte e verifica l'accordo prima di fidarsi di una
qualunque, non solo un confronto a coppie) si comportano come previsto.

Sul percorso senza pausa: **3 successi e 3 fallimenti su 6 tentativi
consecutivi**, non un caso isolato. Con una pausa di 30s tra le
richieste, 3/3. Questo non era mai stato testato prima a nessun N: sia
`local-test/run_repeated_success.py` (N=2) sia
`run_repeated_strangers.py` fanno sempre un riavvio completo dello stack
tra una ripetizione e l'altra, quindi il caso "due calcoli MPC
consecutivi sugli stessi container, senza riavvio" non era mai stato
esercitato concretamente, a nessun N &mdash; vedi &sect;9.5 per cosa
&egrave; stato effettivamente trovato indagando questo caso.

### 9.3 Percorso di fallimento: un party ucciso a met&agrave; calcolo, a N=3

Stesso protocollo di verifica gi&agrave; usato a N=2 (&sect;8.5), ripetuto
qui: container `node2-worker` (party 2) ucciso con `docker kill` a 0,8s
dalla sottomissione, confermato `mascot-party.x` gi&agrave; attivo prima
del kill (non prima dell'avvio). L'orchestratore ha segnalato il
fallimento esplicitamente in **meno di 1 secondo** (0,98s) dal kill, con
l'errore completo di entrambi i party superstiti (party 0 e party 1,
entrambi: `read_some: stream truncated ... probabilmente perch&eacute;
il party 2 ha incontrato un problema` &mdash; MP-SPDZ stesso nomina il
party responsabile) &mdash; non un'attesa indefinita, non un
completamento con solo 2 risposte su 3 trattate come sufficienti.
`party2` nel record della richiesta &egrave; rimasto `null`, come atteso
(il worker ucciso non ha mai potuto aggiornare il proprio stato,
limite gi&agrave; osservato e documentato a N=2 in &sect;8.5, confermato
identico a N=3).

Correzione applicata in questo giro alla logica di correlazione
(`backend/app.py`): la versione precedente attendeva **tutte** le N
risposte anche per riportare un fallimento, il che avrebbe significato
aspettare `QUOTE_TIMEOUT_SECONDS` (700s) prima di segnalare un party
ucciso che non former&agrave; mai la sua risposta. Ora un fallimento
riportato anche da un solo party basta a marcare l'intera richiesta
fallita immediatamente (il protocollo MP-SPDZ &egrave; sincrono: se un
party fallisce, il calcolo &egrave; fallito per tutti, a prescindere da
quando gli altri se ne accorgono) &mdash; verificato con questo stesso
test: senza la correzione, la segnalazione sarebbe arrivata in minuti,
non in meno di un secondo.

### 9.4 Bug trovato e corretto: Player-Data non isolato nell'immagine Docker

Dopo il test di kill del &sect;9.3, **ogni** tentativo successivo (2 su
2) &egrave; fallito in modo identico e riproducibile: crash con
`SIGABRT`, `Removing Player-Data//Mascot-Secrets-2-40-<hash>-P<i>-3
because of MAC check failure` seguito da
`Assertion 'unlink(filename.c_str()) == 0' failed` &mdash; un crash
nel codice di pulizia di `mascot-party.x` stesso (`MAC_Check.hpp`), non
nel programma di questo progetto.

Causa radice, verificata direttamente (non assunta): il repo non aveva
mai avuto un `.dockerignore`. Il Dockerfile di primo livello fa
`COPY . .` dalla radice del repo, quindi ha sempre incluso
`Player-Data/` nell'immagine `mpspdz-mascot:local` &mdash; **2,0GB,
1133 file**, materiale di preprocessing lasciato da sessioni di
benchmark passate e completamente estranee a questo deployment
(`ChaiGear-Secrets`, vecchi `Mascot-Secrets`, certificati di test
precedenti). Docker, per comportamento documentato, pre-popola un
nuovo volume nominato con il contenuto gi&agrave; presente nell'immagine
al punto di mount la prima volta che viene usato &mdash; quindi anche
un volume "appena creato" per un nodo non partiva mai da uno stato
davvero vuoto. Confermato controllando le date dei certificati dentro i
container: `P0.pem`/`P1.pem`/`P2.pem` avevano la data della build
dell'immagine (o della sessione ancora precedente), non quella di
creazione del container. Un `docker rm` + `docker volume rm` + nuovo
deploy **ha effettivamente ripristinato il servizio** (1/1 successo),
ma senza spiegare perch&eacute; un volume "nuovo" contenesse gi&agrave;
materiale di sessioni passate.

Corretto aggiungendo `.dockerignore` alla radice del repo (esclude
`Player-Data/`), poi ricostruite entrambe le immagini
(`mpspdz-mascot:local`, ricompilazione C++ completa, e
`collateral-node:local` sopra di essa). Verificato che il
`Player-Data/` dentro l'immagine sia sceso da 1133 file/2,0GB a 16
voci/36KB (tutte generate dalla build stessa, non pi&ugrave; residui di
sessioni precedenti). Non risolve per&ograve; il problema del
&sect;9.5, scoperto proprio ri-testando dopo questo fix.

### 9.5 Problema aperto, non risolto in questo giro: possibile "incrocio" tra richieste sotto carico in coda

Il fix del &sect;9.4 **non** ha eliminato i fallimenti a richieste
consecutive ravvicinate: nella prima ripetizione dopo il fix (3
richieste, pausa di 20s), 2 su 3 sono fallite di nuovo rapidamente
(`connection reset`/SIGPIPE, 2,1s), inclusa la **prima richiesta mai
sottomessa a quel deployment appena ricreato** &mdash; quindi non
spiegabile con materiale residuo di questa sessione di test.

La terza richiesta di quel batch ha rivelato qualcosa di pi&ugrave;
serio, ricostruito dai log timestampati di tutti e tre i nodi: party 0
ha completato "con successo" in 204s (con un `esito`/`risk_score`/
`esposizione_totale_usd` del tutto plausibili, identici a ogni altra
run riuscita), ma party 1 e party 2 sono rimasti bloccati per l'intero
budget di 600s prima di essere terminati a forza. Party 0 aveva avviato
questo tentativo alle 13:51:22 (worker libero, avendo gi&agrave; fallito
rapidamente il tentativo precedente); party 1 e party 2 per&ograve;
erano ancora occupati fino alle 13:54:46 con il **tentativo
precedente**, i cui processi `mascot-party.x` non erano usciti subito
dopo che la loro controparte (party 0) era caduta &mdash; sono rimasti
in esecuzione per altri 226 secondi, bloccando la coda a concorrenza 1
di quei due nodi. La finestra in cui party 0 ha "completato con
successo" (13:51:22&ndash;13:54:45) coincide quasi esattamente con la
finestra in cui i processi *del tentativo precedente* di party 1/2 erano
ancora vivi (13:51:00&ndash;13:54:46) &mdash; i processi reali di party
1/2 per QUESTA richiesta non erano nemmeno partiti ancora a quel punto.

L'ipotesi pi&ugrave; probabile, non ancora confermata a livello di
pacchetti di rete ma coerente con ogni timestamp osservato: il processo
`mascot-party.x` di party 0 per la richiesta nuova si &egrave;
sincronizzato con i processi **ancora vivi ma orfani** di party 1/2
relativi alla richiesta precedente, non con i (futuri) processi della
propria richiesta. `mascot-party.x` non ha alcun concetto di
"request ID" nel proprio protocollo di coordinamento: si limita a
collegarsi alla porta fissa `PORT_BASE + player_id` e a completare il
protocollo con chiunque risponda l&igrave;. Se cos&igrave; fosse, in un
uso reale con richieste diverse in coda (non lo stesso scenario di test
ripetuto identico), un utente potrebbe ricevere un risultato calcolato
sui dati di **una richiesta precedente e non correlata**, non i propri.

**Perch&eacute; questo non ha prodotto un falso risultato positivo in
questo test**: la nuova logica di correlazione N-via dell'orchestratore
(&sect;9.2) non marca mai una richiesta `completato` finch&eacute; non
ricevono conferma **tutte** le N parti. Party 1 e party 2, occupati con
la richiesta sbagliata, non hanno mai confermato quella corrente in
tempo, quindi la richiesta &egrave; correttamente rimasta poi `fallito`
per timeout, mai mostrata come riuscita all'utente. Questo per&ograve;
&egrave; un effetto collaterale fortunato di un controllo pensato per
un motivo diverso (verificare l'accordo N-via sull'esito), non una
protezione progettata contro questo scenario specifico: se, per
sfortuna, party 1 e party 2 si sincronizzassero ENTRAMBI con lo stesso
processo orfano coerente tra loro, il controllo di accordo attuale non
lo distinguerebbe da un risultato genuino.

**Non risolto in questo giro, segnalato esplicitamente invece di essere
nascosto o minimizzato**: una singola richiesta isolata (nessun'altra
richiesta in coda) funziona in modo affidabile e corretto (confermato,
&sect;9.2, ultima riga della tabella). Il rischio &egrave; specifico a
richieste ravvicinate/in coda sugli stessi nodi. Rimedi possibili da
valutare in un giro successivo, non implementati qui: allocare una
porta MP-SPDZ diversa per richiesta invece di riusare sempre lo stesso
`PORT_BASE`; far attendere all'orchestratore la conferma esplicita che
tutti e N i worker siano davvero liberi prima di sottomettere una nuova
richiesta; oppure un identificatore di richiesta verificato a livello
applicativo prima di fidarsi di un esito rivelato. Fino ad allora, il
deployment pubblico va considerato affidabile per un uso supervisionato
e non concorrente (una richiesta alla volta, con margine tra l'una e
l'altra), non per carico concorrente reale non controllato.

### 9.6 Porte dedicate per richiesta: implementato, verificato, **non risolve il problema**

Implementato esattamente come pianificato: l'orchestratore assegna un
`port_base` per richiesta da un ring buffer Redis di 50 slot (stride
10, base 20000 &mdash; range 20000-20492), mandato a tutti e N i nodi
nello stesso `POST /calcola`, usato da ciascun nodo per il proprio
`mascot-party.x -pn` al posto di un `PORT_BASE` statico e condiviso da
ogni richiesta. Il riuso di uno slot entro `PORT_SLOT_MIN_AGE_SECONDS`
(900s, ben oltre il tetto massimo di 600s che `MPC_TIMEOUT_SECONDS`
impone per costruzione a ogni processo `mascot-party.x` su ogni nodo)
&egrave; **rifiutato esplicitamente** (503), non solo reso improbabile
da un pool grande &mdash; una garanzia dura, non statistica. Aggiunto
anche, in difesa in profondit&agrave;, un controllo che uccide processi
`mascot-party.x` residui prima di ogni nuovo run.
`deploy/deploy_nodes.sh` pubblica ora, per ciascun nodo, l'insieme
disgiunto di porte che pu&ograve; effettivamente usare (party i usa
sempre e solo offset `+i` dentro ogni slot), non pi&ugrave; una singola
porta fissa.

**Verifica, non solo "sembra funzionare"**: stress test di 25
sottomissioni consecutive senza alcuna pausa, prima e dopo il fix,
stesse condizioni esatte, ciascuna con un `existing_debt_p<i>` leggermente
diverso apposta (non pi&ugrave; lo stesso scenario fisso) cos&igrave; un
eventuale incrocio tra richieste sarebbe visibile confrontando
l'`esposizione_totale_usd` ottenuta con quella attesa per l'input di
*quella* richiesta specifica, non solo l'accordo tra i tre nodi.

| | Ripetizioni | Successi | Fallimenti | Risultato di un'altra richiesta (mismatch) |
|---|---|---|---|---|
| Prima del fix (baseline) | 25 | **3/25 (12%)** | 22/25 | 0/25 |
| Dopo il fix | 25 | **3/25 (12%)** | 22/25 | 0/25 |

**Identico, nessun miglioramento.** Il fix chiude realmente il rischio
specifico per cui era stato progettato &mdash; zero mismatch in
entrambe le 25 ripetizioni, cio&egrave; mai un risultato di una
richiesta consegnato a un'altra, nemmeno una volta su 50 tentativi
totali &mdash; ma quel rischio **non era la causa dominante** del tasso
di fallimento osservato nel &sect;9.5. La causa reale era un'altra,
trovata continuando a indagare invece di fermarsi al primo fix che
sembrava plausibile.

**Causa reale, questa volta ricostruita con precisione dai timestamp di
tutti e tre i nodi**: le code dei tre nodi (RQ, concorrenza 1 ciascuna,
indipendenti l'una dall'altra) **si disallineano sotto carico
continuo**. Osservato direttamente: per lo stesso `request_id`, node0
ha elaborato la richiesta partendo alle 18:29:23, mentre node1 e node2
(che restano perfettamente sincronizzati **tra loro**) hanno iniziato la
**stessa identica richiesta** solo alle 19:09:16 &mdash; **40 minuti
dopo**. Meccanismo: quando su un nodo una richiesta fallisce/si
risolve pi&ugrave; velocemente che sugli altri due (per qualunque
motivo, anche solo casuale variabilit&agrave; nella rapidit&agrave; con
cui un fallimento viene rilevato), quel nodo libera il proprio worker
prima e passa alla richiesta successiva in coda &mdash; ma gli altri due
nodi, ancora occupati con la richiesta precedente, non hanno **ancora
nemmeno avviato** il processo `mascot-party.x` per quella nuova
richiesta. Il nodo "in anticipo" tenta quindi di completare il
protocollo con partner che non esistono ancora, fallisce o va in
timeout, e il divario si allarga a ogni richiesta successiva invece di
richiudersi. Le porte dedicate per richiesta chiudono la possibilit&agrave;
che un processo si agganci per errore a un partner sbagliato ancora
vivo, ma non impediscono che un processo parta semplicemente **senza
alcun partner disponibile** &mdash; ed &egrave; questo, non l'incrocio
del &sect;9.5, il motivo dominante dei fallimenti osservati.

`MAX_CONCURRENT_QUOTES=3` (l'orchestratore accetta fino a tre richieste
contemporaneamente attive) presuppone implicitamente che le code dei
nodi restino allineate; con code indipendenti a concorrenza singola e
nessun meccanismo che le tenga sincronizzate, questa non &egrave; mai
stata una garanzia reale, semplicemente non era mai stata messa sotto
stress prima d'ora (lo stesso motivo per cui i problemi di questo intero
&sect;9 non erano mai emersi a nessun N: la metodologia di verifica
precedente riavviava sempre l'intero stack tra una ripetizione e
l'altra).

**Non risolto in questo giro.** Rimedi possibili per un giro
successivo, non implementati qui perch&eacute; richiedono una modifica
architetturale pi&ugrave; ampia di quanto pianificato per questo fix, e
non solo un cambiamento incrementale: un cancello di sincronizzazione
lato orchestratore che non sottometta una richiesta N+1 finch&eacute;
non ha conferma esplicita che tutti gli N nodi abbiano davvero finito
(non solo risposto) alla richiesta N; oppure, pi&ugrave; semplicemente,
ridurre la concorrenza effettiva dell'intero sistema a **una sola
richiesta MPC alla volta** (`MAX_CONCURRENT_QUOTES=1`), coerente con
quello che l'architettura a concorrenza singola per nodo permette
davvero di garantire oggi. Un singolo utente isolato, senza altre
richieste in coda, continua a funzionare in modo affidabile e corretto
(riconfermato pi&ugrave; volte, inclusa una richiesta immediatamente
dopo questi 50 tentativi di stress consecutivi). Il rischio &egrave;
specifico al carico concorrente/in coda continuo, non all'uso normale
supervisionato.

## 10. Log

`Player-Data/`, `node_logs/<request_id>.log` (stdout/stderr grezzo del
processo `mascot-party.x` per quella run, non solo il risultato
decodificato) sono su volumi montati (`player-data`, `node-logs`),
sopravvivono ai riavvii dei container.

## 11. Tentativo di integrazione di tinier (sperimentale) nei nodi: chiuso, non risolvibile in questo scope

tinier era gi&agrave; stato validato due volte, separatamente e in
isolamento: correttezza (34/34 scenari concordanti con mascot,
`results_binario_produzione_sperimentale/`) e tempo/dati/affidabilit&agrave;
sequenziale (`Scripts/tinier_vs_mascot.py`,
`results_tinier_vs_mascot/`: 30&times;-85&times; pi&ugrave; veloce,
31&times;-101&times; meno dati, 0/12 timeout/crash a ogni N). N&eacute;
l'uno n&eacute; l'altro giro avevano mai toccato l'infrastruttura reale
&mdash; questa sezione documenta il tentativo di farlo, con lo stesso
rigore gi&agrave; usato per mascot in &sect;7-9.6, e il punto preciso in
cui &egrave; stato interrotto.

**Aggiornamento (Giro 2, &sect;11.9 sotto)**: &sect;11.1-11.8 sono il
resoconto del Giro 1, lasciato intatto come cronologia. Il Giro 2 ha
approfondito esattamente il problema del test 2 fino a una diagnosi
precisa e a una chiusura definitiva &mdash; leggere &sect;11.9 in poi
per la conclusione che vale davvero; quanto segue qui sotto descrive
solo cosa si sapeva a fine Giro 1, incluse due ipotesi (rallentamento
legato alla generazione del materiale MAC; possibile confronto non
equo tra le velocit&agrave; dei due protocolli) poi confermate/escluse
con precisione nel Giro 2.

**Non si &egrave; arrivati all'integrazione in produzione (n&eacute; nel
Giro 1 n&eacute; nel Giro 2).** Il test 2
(party ucciso a met&agrave; calcolo) ha mostrato per tinier un
rilevamento di crash sistematicamente e riproducibilmente pi&ugrave;
lento di quello gi&agrave; noto per mascot (&sect;8.5/&sect;9.3, <1s) &mdash;
esattamente la condizione di stop esplicita concordata prima di questo
giro. Non corretto per tentativi, non smussato: riportato di seguito con
i dati completi, poi fermato.

### 11.1 Passo 1: binario e immagine Docker, dual-protocol

`tinier-party.x` (verificato nel Makefile, non assunto per analogia con
`mascot-party.x`: `Makefile:245`) costruito con `make tinier-party.x`
sopra l'immagine base gi&agrave; contenente `mascot-party.x` &mdash;
riusa gli oggetti OT/libOTe gi&agrave; compilati come dipendenza
transitiva di quest'ultimo (`$(SPDZ) -> $(TINIER) -> $(OT)` nel
Makefile di primo livello), verificato empiricamente: ~63s, non una
ricompilazione completa di libOTe da zero. `crypto_collateral_risk_binary_produzione_sperimentale.mpc`
compilato per N=2 e N=3 (`-B 64 -o binary ... <n> 0 0`), stessi argomenti
di compilazione gi&agrave; validati in `results_tinier_vs_mascot/`.
Entrambi i protocolli nella STESSA immagine `collateral-node:local`, non
due deployment paralleli (`node/Dockerfile`).

**Build pulita da zero verificata** (`--no-cache` su entrambi gli stage,
non solo sull'ultimo): immagine base (mascot-party.x + tutto MP-SPDZ +
libOTe da zero) 28m8s, immagine nodo (+tinier-party.x + 4 programmi
compilati + app) 1m55s, **zero errori**. Confermato dal log di build che
ogni fix gi&agrave; noto per mascot si applica identico anche qui:
`g++` (mai `clang++`), `bookworm`, `-Wno-error=restrict` accettato senza
errori su ogni singolo file compilato.

### 11.2 Passo 2: selezione del protocollo per richiesta, coda unica invariata

Campo `protocol` (`mascot`|`tinier`) aggiunto a `POST /calcola`
(`node/app/main.py`), obbligatorio, stesso trattamento di `request_id`/
`port_base`. Verificato esplicitamente prima di accodare il job (non
lasciato fallire dopo, come timeout silenzioso): se il binario o il
programma compilato per il protocollo richiesto non esistono su questo
nodo, `503` immediato con il percorso mancante nel messaggio.
`node/app/config.py`'s `PROTOCOLS` mappa `protocol` &rarr;
(binario, programma compilato), derivata da `N_PLATFORMS` con la stessa
convenzione di `compile.py` gi&agrave; in uso. `/salute` riporta lo stato
di entrambi i protocolli separatamente (`dettagli.protocolli.mascot`,
`.tinier`), non un flag unico.

**Verificato esplicitamente nel codice** (non assunto) che l'aggiunta
del secondo protocollo non introduce una seconda coda o un secondo
worker: `node/app/main.py` istanzia una sola `Queue('default', ...)`,
`node/entrypoint.sh` lancia un solo processo `rq worker ... default`,
`node/app/tasks.py` registra un solo tipo di job (`run_mpc_job`) che ora
riceve `protocol` come parametro in pi&ugrave;, non come dispatch verso
un secondo percorso. L'invariante "un calcolo alla volta per nodo" su
cui si regge &sect;8/&sect;9 resta intatta.

### 11.3 Metodologia di verifica: topologia isolata, mai i nodi reali

Tutti i test di questa sezione girano su una topologia Docker isolata
ed effimera (`local-test/tinier_integration_test.py`, container
`tint*-node{i}-*`, reti e porte proprie, mai `node0-net`/`node1-net`/
`node2-net`) &mdash; mai i nodi di produzione reali, che sono rimasti
online e non toccati per l'intera durata di questo giro (verificato:
stessa immagine di prima, `Up 24 hours` durante tutti questi test).
Stesso meccanismo di rete reale gi&agrave; usato da `deploy_nodes.sh`
(reti Docker indipendenti, IP host reale come `PARTY0_HOST`, pool di
porte pubblicate 1:1), isolato semplicemente su nomi/porte diversi.

### 11.4 Test 1 &mdash; percorso di successo ripetuto, N=2

Prima ripetizione (5 run, teardown completo tra l'una e l'altra, stesso
protocollo di &sect;8.1/8.2): tinier ha mostrato un fallimento su 5
(`Connection reset by peer`), un tasso pi&ugrave; alto del CV storico di
mascot. Anzich&eacute; fermarsi su un campione di 5, verificato se fosse
un segnale reale o rumore: stesso identico harness rieseguito anche per
mascot (mai fatto prima con QUESTO harness specifico, i numeri storici
di &sect;8.1/8.2 vengono da uno script diverso, in una sessione
diversa), poi esteso a 20 ripetizioni per protocollo.

| Protocollo | Ripetizioni | Successi | CV tempo (run riuscite) |
|---|---|---|---|
| tinier | 20 | 15/20 (75%) | 14.2% |
| mascot (stesso harness, stesso giro) | 20 | 17/20 (85%) | 14.1% |

Test di Fisher esatto sulla differenza di tasso di successo (15/5 vs
17/3): **p=0.69** &mdash; nessuna differenza statisticamente
significativa. La firma di errore &egrave; identica per entrambi
(`Fatal communication error... Receiving error (Connection reset by
peer)... probably because another party encountered a problem`),
la stessa classe gi&agrave; documentata per mascot in &sect;8.2
("fallimento transitorio isolato... non riprodotto in modo
sistematico") &mdash; qui riprodotta pi&ugrave; spesso per **entrambi**
i protocolli sotto ripetizione rapida (teardown+rebuild immediato,
stesse porte host riusate ogni volta), non introdotta da tinier. Il
tasso assoluto (15-25%) &egrave; pi&ugrave; alto del campione storico
pulito (0/5, 0/5), verosimilmente per lo stato di questa macchina dopo
ore di churn Docker continuo in questa sessione, non per una regressione
di codice &mdash; ma essendo identico tra i due protocolli, non &egrave;
attribuibile a tinier. **Non un segnale di stop**: la prima lettura
(1/5 per tinier, 0/5 per mascot) sarebbe stata fuorviante presa da sola,
esattamente il tipo di falso allarme che una ripetizione pi&ugrave; ampia
&egrave; pensata per correggere.

Dati grezzi: `local-test/results_tinier_integration/test1_success_repeated_{tinier,mascot}*.json`.

### 11.5 Test 5 &mdash; indirizzo di rete del coordinatore

Verificato nel codice sorgente PRIMA di testare (`GC/ShareParty.hpp`,
`Tools/NetworkOptions.cpp`) che tinier passa dalla stessa identica
funzione di coordinamento di rete di mascot, `Server::start_networking`
(chiamata sia da `NetworkOptionsWithNumber::start_networking` per
tinier sia da `Processor/OnlineMachine.hpp` per mascot) &mdash; non
codice di rete separato per la famiglia garbled-circuits, come si
sarebbe potuto presumere. Confermato empiricamente: `PARTY0_HOST=localhost`
produce lo stesso blocco silenzioso e indefinito gi&agrave; documentato
per mascot (`node/app/config.py`), nessun errore, entrambe le parti
bloccate su `in corso` oltre i 90s di attesa del test. **Comportamento
identico a mascot, non una regressione.**

### 11.6 Test 2 &mdash; party ucciso a met&agrave; calcolo: qui si &egrave; fermato il giro

Stesso protocollo gi&agrave; usato per mascot in &sect;8.5/&sect;9.3:
container `worker` del party 2 (N=3) ucciso con `docker kill` a computazione
gi&agrave; attiva (confermato `tinier-party.x` in esecuzione via `pgrep`
subito prima del kill, non prima dell'avvio). Ripetuto **tre volte
indipendenti** prima di trarre conclusioni, stessa disciplina gi&agrave;
usata per il Test 1:

| Run | Kill a t+ | Rilevamento (party superstiti) | Returncode | Modalit&agrave; di uscita |
|---|---|---|---|---|
| 1 | 2.49s | **6.08s** | -6 (SIGABRT) | eccezione C++ non gestita |
| 2 | 1.88s | **5.61s** | 1 | uscita pulita, stesso messaggio di mascot |
| 3 | 2.09s | **14.17s** | -6 (SIGABRT) | eccezione C++ non gestita |

Baseline mascot (&sect;8.5/&sect;9.3, verificata due volte, N=2 e N=3):
**rilevamento &lt;1s su ogni party superstite, ogni volta**, uscita
pulita (`Fatal error in communication: read_some: stream truncated`,
returncode gestito).

**tinier &egrave; 6-14&times; pi&ugrave; lento a rilevare lo stesso
guasto di rete, in modo riproducibile su tre run indipendenti, non un
campione singolo.** In pi&ugrave;, un secondo problema distinto trovato
nello stesso test: la stessa identica causa (`read_some: stream
truncated [asio.ssl.stream:1]`, lo stesso errore SSL sottostante di
mascot) produce per tinier **due modalit&agrave; di uscita diverse e non
deterministiche** a seconda della run &mdash; a volte un'eccezione
C++ non gestita (`terminate called after throwing an instance of
'boost::wrapexcept<boost::system::system_error>'`, `SIGABRT`), a volte
un'uscita pulita con lo stesso messaggio di mascot. Il primo caso non
compromette la correttezza applicativa (`node/app/mpc_runner.py` cattura
comunque il `returncode != 0` e riporta `fallito` con l'errore completo,
verificato in tutti e tre i log), ma &egrave; un segnale di gestione
degli errori meno matura nel binario stesso, non nel codice di
integrazione di questo progetto.

Causa pi&ugrave; probabile, visibile nei log di tutti e tre i run
(non confermata a livello di codice sorgente, ma coerente con ogni
timestamp osservato): appena prima del crash, ogni party superstite
stampa `No proper key material found in Player-Data//Mascot-Secrets-B-64-...,
generating from scratch` &mdash; tinier, a differenza di mascot, genera
materiale di preprocessing (le proprie chiavi MAC, necessarie per il
protocollo malicious cut-and-choose) **live, a ogni run**, non lo
riusa da un batch offline. Il rilevamento pi&ugrave; lento coincide con
questa finestra: un party impegnato nella generazione locale del
preprocessing non controlla lo stato della connessione con la stessa
frequenza di un party gi&agrave; nella fase di calcolo principale.
Ipotesi plausibile, non verificata a livello di codice C++ in questo
giro &mdash; motivo aggiuntivo, non l'unico, per fermarsi qui piuttosto
che continuare a indagare per tentativi.

Dati grezzi completi (log, timestamp, errori integrali):
`local-test/results_tinier_integration/test2_kill_mid_computation_tinier_run{1,2,3}.json`.

### 11.7 Condizione di stop: dichiarata esplicitamente, applicata

Il test 2 soddisfa esattamente la condizione di stop concordata prima di
questo giro ("rilevamento di crash pi&ugrave; lento" di quello gi&agrave;
noto per mascot sullo stesso test, riprodotto su pi&ugrave; run
indipendenti, non un campione singolo). Per istruzione esplicita:
fermarsi qui, riportare con precisione, non correggere per tentativi n&eacute;
smussare in una conclusione pi&ugrave; ottimistica di quanto i dati
sostengano.

**Test 3 (25 richieste consecutive senza pausa) e Test 6 (20 richieste
consecutive a protocollo misto) non sono stati eseguiti.** Non per
mancanza di tempo: la condizione di stop esplicita si applica appena un
qualunque test del Passo 3 mostra un comportamento peggiore di quello
gi&agrave; noto per mascot, e il Test 2 lo mostra gi&agrave; in modo
chiaro e riprodotto. Eseguire comunque i test 3/6 non avrebbe cambiato
questa conclusione, solo consumato altro tempo su una domanda gi&agrave;
risposta.

**Il supporto duale mascot/tinier NON &egrave; stato implementato in
produzione** (Passo 4 del giro, mai raggiunto): `backend/app.py` non
espone un campo `protocol`, i nodi di produzione (`node0`/`node1`/`node2`)
restano sull'immagine mascot-only precedente, non sono mai stati
toccati da questo giro.

**Riepilogo dei sei test previsti dal Passo 3:**

| Test | Esito | Confronto con mascot |
|---|---|---|
| 1. Percorso di successo ripetuto (N=2) | OK | Statisticamente indistinguibile (p=0.69) |
| 2. Party ucciso a met&agrave; calcolo | **PEGGIORE** | Rilevamento 6-14&times; pi&ugrave; lento, uscita non deterministica |
| 3. 25 richieste consecutive senza pausa | Non eseguito | Condizione di stop gi&agrave; soddisfatta dal test 2 |
| 4. Isolamento Player-Data | OK | 36KB/17 voci, stesso profilo del fix di &sect;9.4 |
| 5. Indirizzo di rete del coordinatore | OK | Comportamento identico (stesso codice sorgente) |
| 6. Coda condivisa, protocollo misto | Non eseguito | Condizione di stop gi&agrave; soddisfatta dal test 2 |

### 11.8 Cosa resta e cosa non cambia

Il codice di questo tentativo resta nel repository, committato: il
supporto per il campo `protocol` in `node/app/*` (`main.py`, `config.py`,
`tasks.py`, `mpc_runner.py`), l'immagine Docker dual-protocol
(`node/Dockerfile`), l'harness di verifica isolato
(`local-test/tinier_integration_test.py`). Non &egrave; codice morto:
se in futuro la lentezza/non-determinismo del rilevamento crash di
tinier verr&agrave; risolto o accettato come compromesso deliberato, la
strada di integrazione &egrave; gi&agrave; costruita e verificata fin
dove si &egrave; arrivati (Test 1, 4, 5 passati). **Ma va trattato come
codice sperimentale non ancora pronto**, non come una feature quasi
finita: manca un'analisi della causa radice della lentezza/non-
determinismo (dentro `tinier-party.x`, fuori dallo scope di un progetto
che non modifica MP-SPDZ stesso), e i test 3/6 restano da fare una volta
che il test 2 sia stato affrontato, non prima.

**Nota di compatibilit&agrave;, per chi riprender&agrave; questo lavoro**:
`collateral-node:local` ricostruita con questo `node/Dockerfile` richiede
il campo `protocol` su ogni `POST /calcola` (non ha pi&ugrave; un default
impliciti lato nodo). Il backend attuale (`backend/app.py`, invariato da
questo giro) non lo manda. **Non ridistribuire questa immagine ai nodi
di produzione senza aggiornare prima il backend** &mdash; romperebbe
`/api/preventivo` con un 422 su ogni richiesta, non un fallimento
silenzioso ma comunque una rottura da evitare con un deploy disattento.

### 11.9 Giro 2 &mdash; diagnosi mirata: perch&eacute; tinier &egrave; pi&ugrave; lento a rilevare un party ucciso

Obiettivo di questo giro: arrivare a una conclusione definitiva sul
singolo problema del test 2, non un'altra osservazione parziale.
Testate due ipotesi, in ordine; la prima ha rivelato un problema
diverso e pi&ugrave; specifico di quello ipotizzato, la cui correzione
del metodo di misura ha permesso di isolare la causa reale.

#### 11.9.1 Ipotesi 1: materiale di preprocessing MAC non persistito

Confermato **nel codice**, non solo nei log (`OT/MascotMacKey.hpp`,
`read_or_generate`): il meccanismo di persistenza **esiste gi&agrave;**
nel framework, condiviso identico da mascot e tinier (entrambi passano
da `OT/NPartyTripleGenerator.hpp`) &mdash; tenta di leggere
`Player-Data/Mascot-Secrets-*` all'avvio, rigenera da zero solo se il
file manca o non corrisponde. Il problema non era l'assenza del
meccanismo, ma che l'harness di test isolato (`local-test/
tinier_integration_test.py`) non montava alcun volume persistente per
`Player-Data/` &mdash; ogni run, per **entrambi** i protocolli,
rigenerava sempre da zero.

Corretto: aggiunto un volume Docker nominato per party
(`Topology(persistent=True)`), pi&ugrave; una run di riscaldamento prima
dei test di kill. Verificato che la cache funzioni: `generating from
scratch` non compare pi&ugrave; nei log successivi al warm-up, per
nessun party.

**Risultato, sorprendente rispetto all'ipotesi**: con la cache calda, il
rilevamento del crash non &egrave; migliorato &mdash; &egrave;
**peggiorato**, apparentemente a tempo indefinito (nessun rilevamento
entro 90s, su 4/4 run valide; snapshot periodici di `ps aux` durante
l'attesa confermano che i party superstiti restano vivi con CPU vicina
allo 0%, genuinamente bloccati, non lenti). L'ipotesi originale era
sbagliata nella direzione, ma il tentativo di verificarla ha portato
dritti alla causa reale.

#### 11.9.2 Causa reale: due percorsi di rete distinti, condivisi da entrambi i protocolli

`Networking/sockets.h` definisce `CONNECTION_TIMEOUT` (in questo
progetto compilato a **86400 secondi**, `CONFIG.mine`:
`MY_CFLAGS += -DCONNECTION_TIMEOUT=86400`) &mdash; usato da
`ServerSocket.cpp` **solo nella fase di stabilimento della connessione**
(`accept`/`connect`), un percorso di codice completamente diverso dalla
lettura su un socket **gi&agrave; connesso**, che invece fallisce
all'istante con un'eccezione se il peer sparisce (`read_some: stream
truncated`).

Verificato con un test isolato, senza Docker, direttamente sui binari
(`tinier-party.x`/`mascot-party.x` sul host): uccidere il party 2 **a
t+0,1s** (prima che le connessioni tra le tre parti siano stabilite),
con `CONNECTION_TIMEOUT` temporaneamente abbassato a 5s per velocizzare
la verifica (modifica reversibile, solo in `CONFIG.mine`, file locale
non versionato, mai toccato il codice sorgente C++): rilevamento in
esattamente **5,09s**, messaggio `Timed out waiting for peer` &mdash;
non `stream truncated`. Confermato: sono due percorsi realmente
distinti, e quello sbagliato (di gran lunga pi&ugrave; lento, in
produzione 86400s = 24 ore) si attiva quando il kill arriva **prima**
che la connessione N-via sia completa.

Con la cache calda, l'intera computazione di tinier passa da qualche
secondo a ~3-4s (salta la generazione delle chiavi) &mdash; il ritardo
di kill fisso usato nell'harness (attesa di un processo confermato +
1,5s) che prima ricadeva quasi sempre nella fase "gi&agrave; connesso"
ora ricade, non deterministicamente, anche nella fase "ancora in fase
di connessione". Non &egrave; quindi un difetto specifico di tinier
messo in luce dalla cache calda: &egrave; un artefatto del metodo di
misura (un ritardo di kill fisso, tarato implicitamente sui tempi
lunghi di mascot) che smette di essere valido quando il tempo totale di
computazione si accorcia parecchio.

`CONFIG.mine` riportato a `86400` e i binari host ricompilati subito
dopo il test, prima di procedere &mdash; l'esperimento non ha lasciato
lo stato del repository alterato. **Non si tratta di un intervento
adottabile come fix**: abbassare `CONNECTION_TIMEOUT` in modo permanente
&egrave; una scelta a livello di intero progetto (tollerare meno tempo
di avvio prima di dichiarare un peer irraggiungibile, per ogni
protocollo e ogni deployment, incluso lo scenario reale di due macchine
fisiche separate con latenza di rete vera), non un aggiustamento
specifico a tinier &mdash; e, come mostrato sotto, **non risolve
comunque** il problema di fondo del test 2.

#### 11.9.3 Il confronto corretto: stesso ritardo di kill, garantito post-connessione, per entrambi

Metodologia corretta: invece di un ritardo fisso tarato sui tempi di
mascot, un ritardo fisso di 2,0s dopo l'avvio dei tre processi,
verificato sufficiente a garantire connessione N-via gi&agrave;
stabilita per **entrambi** i protocolli su questo host (stesso identico
harness raw, nessun Docker, `CONNECTION_TIMEOUT` di produzione
ripristinato). 5 run indipendenti per protocollo, N=3, party 2 ucciso:

| Run | mascot: rilevamento | mascot: uscita | tinier: rilevamento | tinier: uscita |
|---|---|---|---|---|
| 1 | 0,091s | pulita | 2,488s | SIGABRT |
| 2 | 0,137s | pulita | 2,457s | SIGABRT |
| 3 | 0,149s | pulita | 2,324s | SIGABRT |
| 4 | 0,059s | pulita | 0,062s | pulita |
| 5 | 0,072s | pulita | 0,059s | pulita |

**mascot: 5/5 rapido (0,06-0,15s), 5/5 uscita pulita &mdash; deterministico.**

**tinier: bimodale, non rumore casuale.** 3 run su 5 a 2,3-2,5s con
`SIGABRT` (eccezione C++ non gestita) su entrambe le parti superstiti,
2 run su 5 indistinguibili da mascot (~0,06s, uscita pulita) &mdash;
sempre la STESSA causa di fondo (`read_some: stream truncated`), due
esiti radicalmente diversi a seconda della run. Rimosso ogni confondente
di tempistica di connessione (stesso ritardo, verificato post-connessione
per entrambi), la differenza persiste, riproducibile, non spiegabile da
cache calda n&eacute; da `CONNECTION_TIMEOUT`.

**Ipotesi sulla causa (architetturale, non verificata scendendo nel
debug C++ riga per riga, che eccederebbe lo scope di questo progetto)**:
`GC::ShareParty` (il percorso di avvio di tinier, `GC/ShareParty.hpp`)
costruisce il `Player` **due volte** &mdash; una per l'impostazione della
chiave MAC (`"shareparty"`), una dentro `ThreadMaster::run_with_error()`
(`"main"`) &mdash; e usa un pool di thread (`GC::ThreadMaster`/`Thread`)
per l'esecuzione, a differenza del percorso pi&ugrave; lineare di
mascot (`DishonestMajorityFieldMachine`). Se l'eccezione di socket
troncato viene sollevata su un thread diverso da quello principale, non
protetto dallo stesso `try/catch` di livello superiore, il risultato
&egrave; `std::terminate()` invece di un'uscita gestita &mdash; e quale
thread nota per primo il socket morto dipende da un dettaglio di
scheduling non deterministico, spiegando la natura bimodale
dell'osservazione. Verificarlo con certezza richiederebbe mettere un
debugger dentro `tinier-party.x`, cio&egrave; lavorare sul codice
sorgente C++ di MP-SPDZ &mdash; esplicitamente fuori scope.

#### 11.9.4 Condizione di successo: non soddisfatta

Concordata prima di questo giro: rilevamento nello stesso ordine di
grandezza del <1s di mascot **su almeno 5 run indipendenti**, uscita
deterministica. Il risultato osservato (3/5 run a 2,3-2,5s con
`SIGABRT`, 2/5 run allineate a mascot) **non soddisfa n&eacute; il
primo n&eacute; il secondo criterio**: n&eacute; "stesso ordine di
grandezza" (2,3-2,5s &egrave; ~20-40&times; il tempo di mascot, non un
fattore 2-3&times;) n&eacute; "deterministico" (l'alternanza tra
`SIGABRT` e uscita pulita a parit&agrave; di causa &egrave; precisamente
la non-determinismo che la condizione di successo escludeva
esplicitamente). Non un miglioramento parziale da presentare come
"sostanzialmente risolto" &mdash; un fallimento del test 2, confermato
con una diagnosi pi&ugrave; precisa di quella del Giro 1, non attenuato
da essa.

#### 11.9.5 Passo 2 (tuning di rete): eseguito, non sufficiente

Il Passo 2 previsto da questo giro (opzione di configurazione per il
timeout di rete, non una patch C++) &egrave; stato effettivamente
trovato e testato: `CONNECTION_TIMEOUT`, gi&agrave; un flag di
compilazione esposto (`CONFIG.mine`), non richiede alcuna modifica al
codice sorgente. Ma il test del &sect;11.9.3, che ha gi&agrave; escluso
la fase di connessione dal confronto (kill garantito post-connessione
per entrambi i protocolli), mostra che il problema **non &egrave;
questo timeout** &mdash; persiste identico con `CONNECTION_TIMEOUT` di
produzione invariato. Non c'&egrave; quindi un secondo valore di questo
flag da provare: il Passo 2, nella sua unica forma applicabile senza
toccare il codice C++, non risolve il problema del test 2, per un
motivo verificato, non per mancato tentativo.

#### 11.9.6 Conclusione definitiva: integrazione tinier chiusa

**Il problema non &egrave; risolvibile restando dentro lo scope di
questo progetto.** Le due strade previste da questo giro sono state
entrambe percorse fino in fondo:

- Persistenza del materiale MAC (Passo 1): il meccanismo esiste
  gi&agrave; ed &egrave; stato correttamente attivato, ma non tocca la
  causa del problema &mdash; ha solo reso pi&ugrave; visibile un
  artefatto separato del metodo di misura (tempi di kill tarati sulla
  velocit&agrave; di mascot, non validi per un protocollo molto
  pi&ugrave; veloce), gi&agrave; corretto nel confronto del &sect;11.9.3.
- Tuning di rete (Passo 2): `CONNECTION_TIMEOUT` &egrave; un flag di
  compilazione reale, testato, e non &egrave; la causa del problema
  isolato nel &sect;11.9.3.
- La causa pi&ugrave; probabile che rimane (architettura multi-thread di
  `GC::ThreadMaster`/gestione delle eccezioni tra thread in
  `tinier-party.x`) richiederebbe una modifica al codice sorgente C++ di
  MP-SPDZ per essere confermata e corretta &mdash; esplicitamente fuori
  scope, per istruzione esplicita di questo stesso giro.

**Il Test 3 e il Test 6 restano non eseguiti, questa volta in modo
definitivo, non "in sospeso in attesa del test 2".** Il supporto duale
mascot/tinier nei nodi di produzione **non verr&agrave; implementato**.
Questa chiusura non &egrave; "da riprovare in futuro senza una ragione
nuova" &mdash; lo diventerebbe solo se emergesse una ragione concreta e
nuova per riaprirla (es. una versione futura di MP-SPDZ che corregge la
gestione delle eccezioni multi-thread di tinier, verificabile con lo
stesso identico test del &sect;11.9.3 come criterio di accettazione).

Dati grezzi completi (5+5 run, timestamp, messaggi, exit code):
`local-test/results_tinier_integration/test2_warm_tinier.json` (round
cache calda, Docker) e nei log di sessione per il confronto raw
host-level del &sect;11.9.3 (non salvato come file JSON separato, dati
integrali riportati nella tabella sopra).
