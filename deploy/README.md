# Demo pubblica: preventivo di prestito end-to-end

Espone il prototipo (due nodi MPC + un piccolo backend orchestratore +
un frontend) sull'IP pubblico di questa macchina, cos&igrave; un
preventivo di mutuo/prestito pu&ograve; essere lanciato da un browser e
far girare un calcolo MPC reale, non simulato.

**Non &egrave; un secondo deployment a due macchine** &mdash; usa la
topologia "estranei sulla stessa macchina" gi&agrave; validata (5/5,
CV 6.4&ndash;6.5%, vedi `node/README.md` &sect;8.2): due reti Docker
isolate, porta MP-SPDZ di party 0 pubblicata 1:1, `PARTY0_HOST` =
indirizzo IP reale della macchina. Non sostituisce il test su due
macchine fisiche separate, ancora non fatto (stesso limite gi&agrave;
documentato).

## Componenti

```
deploy/deploy_nodes.sh   -- party 0 + party 1, persistenti (restart
                             unless-stopped), stessa topologia validata
backend/                 -- FastAPI: login, orchestrazione (sottomette
                             lo stesso request_id a entrambi i nodi in
                             parallelo, come richiesto in
                             node/README.md &sect;5), rate limit, serve
                             il frontend statico
backend/static/index.html-- frontend "cryptolike" a pagina singola
deploy/certs/             -- certificato TLS self-signed (gitignored)
deploy/secrets/           -- password admin + secret di sessione (gitignored)
```

## Deploy da zero

```bash
# 1. immagini (se non gi&agrave; buildate, vedi node/README.md &sect;1)
./node/build.sh
docker build -f backend/Dockerfile -t collateral-backend:local backend/

# 2. i due nodi, persistenti
HOST_IP=<IP pubblico reale della macchina> ./deploy/deploy_nodes.sh

# 3. credenziali (una tantum -- se deploy/secrets/backend.env non esiste gi&agrave;)
ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-20)
SESSION_SECRET=$(openssl rand -hex 32)
mkdir -p deploy/secrets
cat > deploy/secrets/backend.env << EOF
ADMIN_PASSWORD_PLAINTEXT=$ADMIN_PASSWORD
ADMIN_PASSWORD_HASH=$(printf '%s' "$ADMIN_PASSWORD" | sha256sum | cut -d' ' -f1)
SESSION_SECRET=$SESSION_SECRET
EOF
chmod 600 deploy/secrets/backend.env

# 4. certificato TLS self-signed (una tantum)
mkdir -p deploy/certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout deploy/certs/key.pem -out deploy/certs/cert.pem \
  -days 365 -subj "/CN=<IP pubblico>" -addext "subjectAltName=IP:<IP pubblico>"

# 5. redis + backend, collegato a ENTRAMBE le reti dei nodi (cos&igrave;
#    pu&ograve; chiamare node0-api/node1-api per nome, e i nodi possono
#    richiamare backend per la callback, senza passare dall'IP pubblico
#    dell'host e dal relativo hairpin NAT)
source deploy/secrets/backend.env
docker run -d --name backend-redis --network node0-net --restart unless-stopped \
  -v backend-redis-data:/data redis:7-alpine redis-server --save 60 1
docker run -d --name backend --network node0-net --restart unless-stopped \
  -p 9090:9090 \
  -e NODE0_URL=http://node0-api:8000 -e NODE1_URL=http://node1-api:8000 \
  -e CALLBACK_BASE_URL=http://backend:9091 \
  -e REDIS_URL=redis://backend-redis:6379/0 \
  -e SESSION_SECRET=$SESSION_SECRET -e ADMIN_PASSWORD_HASH=$ADMIN_PASSWORD_HASH \
  -v $(pwd)/deploy/certs:/certs:ro \
  collateral-backend:local
docker network connect node1-net backend

# 6. firewall (richiede sudo -- non eseguibile in automatico in questo ambiente)
sudo ufw allow 9090/tcp comment "collateral-mpc demo HTTPS"
```

Poi `https://<IP pubblico>:9090` &mdash; il browser mostrer&agrave; un
avviso "connessione non sicura" (certificato self-signed, nessun
dominio/CA reale disponibile per questo prototipo): va confermato una
volta, il traffico resta comunque cifrato.

## Perch&eacute; due porte sul backend (9090 e 9091)

`backend/entrypoint.sh` avvia **due processi uvicorn** dalla stessa app:
`9090` (HTTPS, pubblicata sull'host, per il browser) e `9091` (HTTP in
chiaro, mai pubblicata sull'host, raggiungibile solo dalle reti Docker
dei nodi). I nodi chiamano la callback via HTTP semplice, senza gestione
di un certificato self-signed lato client &mdash; puntarli su `9090`
fallisce subito (un client in chiaro contro una porta solo-TLS si vede
come connessione chiusa, non un errore leggibile). Verificato
direttamente: il primo tentativo puntava entrambe le rotte sulla stessa
porta 9090 e le callback fallivano sistematicamente con
`RemoteDisconnected`.

**Bug correlato, anch'esso trovato ed eliminato**: due processi uvicorn
distinti sono due processi Python distinti, che non condividono memoria.
Uno stato `quotes = {}` in-process (come nella primissima versione)
veniva scritto dal processo `9090` alla sottomissione ma non era mai
visibile al processo `9091` quando arrivava la callback &mdash; loggato
come `callback per request_id sconosciuto`, anche se la callback stessa
arrivava correttamente. Fix: stato spostato su Redis (`backend-redis`),
stesso pattern gi&agrave; usato in `node/app/store.py`.

## Sicurezza (livello prototipo, non produzione)

- **Login**: password condivisa singola (non multi-utente), hash SHA-256
  confrontato con `hmac.compare_digest`, sessione via cookie firmato
  (`itsdangerous`/Starlette `SessionMiddleware`), 8 ore di validit&agrave;.
  Password in `deploy/secrets/backend.env` (mai committato, vedi
  `.gitignore`).
- **Rate limit**: 3 richieste/ora per indirizzo IP (in-memory sul
  processo `9090`, sufficiente a questa scala), pi&ugrave; un tetto
  globale di 3 preventivi contemporaneamente "in coda"/"in corso" &mdash;
  ogni calcolo impegna entrambi i nodi seriali per decine di secondi,
  non ha senso accettare una coda illimitata.
- **TLS self-signed**: cifra il traffico ma non autentica il server
  contro una CA riconosciuta &mdash; un avviso browser atteso, non un
  errore. Non adatto oltre lo scope di un prototipo di tesi.
- **Non fatto, deliberatamente fuori scope per questo giro**: rotazione
  password, audit log, WAF/rate-limit a livello di rete (solo applicativo
  qui), certificato CA reale (richiederebbe un dominio puntato su questo
  IP).

## Cosa il preventivo NON rivela mai (a N=2)

Il form chiede l'esposizione esistente di **entrambe** le piattaforme
(party 0 e party 1) solo a scopo dimostrativo &mdash; in un deployment
reale, ciascuna piattaforma conoscerebbe solo il proprio dato,
esattamente come i due container non si scambiano mai quei valori in
chiaro. A N=2, `crypto_collateral_risk.mpc` rivela solo la fascia di
leva aggregata (0&ndash;3), mai l'importo esatto: darlo al richiedente
gli permetterebbe di dedurre per sottrazione l'esposizione esatta della
controparte (conosce gi&agrave; il proprio contributo e il nuovo
prestito) &mdash; stessa regola gi&agrave; documentata nel modulo
docstring di `crypto_collateral_risk.mpc`, non una scelta di questo
giro.
