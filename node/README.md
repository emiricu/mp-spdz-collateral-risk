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
