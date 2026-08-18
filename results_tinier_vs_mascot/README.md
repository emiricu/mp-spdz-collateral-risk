# tinier (sperimentale) contro mascot in produzione, N=2/3/4/5

Confronto cronometrato tra `crypto_collateral_risk_binary_produzione_sperimentale.mpc`
(dominio binario, protocollo `tinier`, validato per correttezza in
`results_binario_produzione_sperimentale/` su 34 scenari) e
`crypto_collateral_risk.mpc` mode=produzione (dominio field, protocollo
`mascot`, il riferimento fidato attualmente in uso nei nodi Docker),
stessa metodologia e stesso rigore di `Scripts/retest_controllato.py`
(scenario fixture congelato, ordine alternato tinier→mascot per ogni N,
non due sweep separati; 3 ripetizioni per ciascuna delle 8 combinazioni
2 protocolli × 4 N; timeout fisso di 1200s per ogni N, non la scala
graduata 600/800/1000/1200 usata altrove — qui il timeout conta anche
come segnale di affidabilità in sé, non solo come budget "equo").

Script: `Scripts/tinier_vs_mascot.py`. Dati grezzi:
`index.json`/`summary.json` più un `.json`/`.log`/`.mpc_raw.log` per
ogni singola run (24 run totali).

Scenario fixture (identico per ogni N e ogni ripetizione, `--price-source
fixture`): wallet `0xclean0000...0001`, collateral 3.5 BTC, nuovo
prestito 50000 USD, debito preesistente totale 20000 USD suddiviso in
parti uguali tra le N piattaforme.

## Prerequisito di macchina scoperto durante questo giro

`mpc_bench.build()` chiama `make -j{jobs} -k {binari}` senza un `CXX=`
esplicito, quindi dipende interamente da `CONFIG`/`CONFIG.mine`.
`CONFIG` di base usa `clang++`, incompatibile con il proprio stesso
flag `-Wno-error=restrict` (nome di flag specifico di GCC — clang++ lo
rifiuta come opzione sconosciuta e il build fallisce). Ogni binario
host-nativo già presente su questa macchina (incluso `mascot-party.x`,
`tinier-party.x`, tutta la storia di `results_produzione`/
`results_retest_controllato`) era in realtà stato compilato con g++,
tramite un `CONFIG.mine` locale che risultava perso. Scoperto perché
`mpc_bench.build()` ha tentato silenziosamente di ricompilare
`tinier-party.x` con clang++ a metà smoke-test, fallendo — mascherato
solo dal fatto che `make -k` lascia intatto il binario precedente
(funzionante) quando la ricompilazione fallisce, quindi lo smoke test
di per sé "funzionava" comunque con un binario stantio. Corretto
aggiungendo `CXX = g++` a `CONFIG.mine` (file non versionato, gitignored
— la correzione non ha un commit corrispondente ma è un prerequisito
per riprodurre questi numeri su questa macchina). Verificato con
ricompilazione reale e pulita di entrambi i binari prima di lanciare lo
sweep vero.

## Verifica di correttezza, per ciascun N

Prima di fidarsi di qualunque tempo, tinier e mascot sono stati
eseguiti sullo stesso scenario per ogni N e confrontati campo per campo
(`esito`, `risk_score`, `max_ltv_bps`, `liq_threshold_bps`):

| N | Esito |
|---|---|
| 2 | **OK, coincidono** |
| 3 | **OK, coincidono** |
| 4 | **OK, coincidono** |
| 5 | **OK, coincidono** |

Nessuna divergenza a nessun N. Valori concordanti su tutte le run
(scenario fisso → stesso esito su ogni ripetizione): `esito=APPROVATO`,
`risk_score=73`, `max_ltv_bps=6500`, `liq_threshold_bps=7500`.

## Affidabilità: timeout e crash, budget fisso 1200s

| Combinazione | Run totali | Timeout | Crash |
|---|---|---|---|
| tinier, ogni N (2/3/4/5) | 3 ciascuno (12 tot.) | 0 | 0 |
| mascot, ogni N (2/3/4/5) | 3 ciascuno (12 tot.) | 0 | 0 |

**24/24 run completate con successo**, nessun timeout, nessun crash, a
nessun N per nessuno dei due protocolli — incluso mascot a N=5, dove il
retest storico (`node/README.md` §9.5/9.6) aveva mostrato instabilità
grave (3/25 = 12% di successo).

Questo però non è un confronto tra "stesso sistema, carico più leggero":
`tinier_vs_mascot.py` non passa mai per l'architettura che ha prodotto
quei fallimenti. La catena di chiamate è `tinier_vs_mascot.py` →
`crypto_risk_run.compile_and_run()` → `mpc_bench.compile_domain/build/
run_protocol()` → `Scripts/mascot.sh`/`Scripts/tinier.sh`, che lancia
`mascot-party.x`/`tinier-party.x` come N processi locali sullo stesso
host — **verificato leggendo il codice, nessuna chiamata HTTP a
`node0-api`/`node1-api`/`node2-api`, nessun passaggio per l'orchestratore
`backend/app.py`, nessuna coda RQ**. Il bug di §9.5/9.6 è specifico a
quell'architettura a container (code RQ indipendenti a concorrenza
singola per nodo su `node1-worker`/`node2-worker`, che si disallineano
sotto carico continuo quando un nodo libera il proprio worker prima
degli altri) e semplicemente non esiste un meccanismo equivalente in
questo sweep: ogni run è un processo locale isolato, senza code da
disallineare.

Di conseguenza, "0/24 fallimenti per entrambi i protocolli" **non dice
nulla** sul criterio di affidabilità che aveva deciso mascot-su-mama —
non perché lo sweep sia stato troppo leggero o troppo breve, ma perché
misura un percorso di esecuzione che non attraversa mai il componente
in cui vive quel problema, indipendentemente da quante ripetizioni
fossero state fatte.

## Tempo e dati scambiati, per ripetizione

### N=2

| Run | tinier tempo | tinier dati (MB) | mascot tempo | mascot dati (MB) |
|---|---|---|---|---|
| 1 | 1.7s | 84.8 | 63.1s | 2663.6 |
| 2 | 3.2s | 84.8 | 77.3s | 2663.6 |
| 3 | 2.4s | 84.8 | 78.4s | 2663.6 |

### N=3

| Run | tinier tempo | tinier dati (MB) | mascot tempo | mascot dati (MB) |
|---|---|---|---|---|
| 1 | 9.3s | 262.7 | 211.8s | 15603.1 |
| 2 | 3.5s | 262.7 | 203.7s | 15603.1 |
| 3 | 4.6s | 262.7 | 246.2s | 15603.1 |

### N=4

| Run | tinier tempo | tinier dati (MB) | mascot tempo | mascot dati (MB) |
|---|---|---|---|---|
| 1 | 7.1s | 524.8 | 505.9s | 40422.6 |
| 2 | 7.4s | 524.8 | 371.3s | 40422.6 |
| 3 | 8.5s | 524.8 | 426.9s | 40422.6 |

### N=5

| Run | tinier tempo | tinier dati (MB) | mascot tempo | mascot dati (MB) |
|---|---|---|---|---|
| 1 | 7.6s | 903.3 | 769.3s | 91713.2 |
| 2 | 6.1s | 903.3 | 746.6s | 91713.2 |
| 3 | 14.4s | 903.3 | 872.5s | 91713.2 |

I dati scambiati sono identici su tutte le ripetizioni di una stessa
combinazione protocollo/N (circuito e scenario fissi → traffico
deterministico) — non è un errore di misura, è atteso.

## Tabella di confronto diretto (media / min / max sulle 3 ripetizioni)

| N | tempo mascot | tempo tinier | speedup | dati mascot (MB) | dati tinier (MB) | riduzione dati |
|---|---|---|---|---|---|---|
| 2 | 72.9s (63.1–78.4) | 2.4s (1.7–3.2) | **30.1×** | 2663.6 | 84.8 | **31.4×** |
| 3 | 220.6s (203.7–246.2) | 5.8s (3.5–9.3) | **38.1×** | 15603.1 | 262.7 | **59.4×** |
| 4 | 434.7s (371.3–505.9) | 7.7s (7.1–8.5) | **56.7×** | 40422.6 | 524.8 | **77.0×** |
| 5 | 796.1s (746.6–872.5) | 9.4s (6.1–14.4) | **85.1×** | 91713.2 | 903.3 | **101.5×** |

Sia il vantaggio in tempo sia quello in dati scambiati **crescono con
N**, non restano costanti — coerente con mascot che scala peggio con N
(atteso: MASCOT è un protocollo a campo, malicious, con generazione di
tripli Beaver via OT esteso, un costo per-confronto molto più alto del
confronto bit a bit nativo di un protocollo binario come tinier).

## La domanda esplicita: tinier supera mascot?

**Sul criterio tempo/dati: sì, nettamente, a ogni N testato** — da
30× più veloce (N=2) a 85× più veloce (N=5), con un margine di dati
scambiati che va da 31× a oltre 100× a favore di tinier. Non è un
risultato marginale o rumoroso: il tempo minimo di mascot a N=2 (63.1s)
resta comunque oltre 19× il tempo MASSIMO di tinier allo stesso N
(3.2s) — non c'è sovrapposizione tra le due distribuzioni a nessun N.

**Sul criterio affidabilità: questo sweep non può parlarne, non solo
"non lo differenzia".** 0 timeout e 0 crash per entrambi i protocolli,
su tutte le 24 run — ma è un dato più debole di quanto sembri, e per un
motivo più preciso di "carico più leggero". `tinier_vs_mascot.py` non
passa mai per l'architettura in cui vive il problema di §9.5/9.6:
verificato leggendo il codice, la catena `crypto_risk_run.
compile_and_run()` → `mpc_bench.run_protocol()` → `Scripts/mascot.sh`/
`Scripts/tinier.sh` lancia i processi MP-SPDZ direttamente sull'host,
senza nessuna chiamata HTTP a `node0-api`/`node1-api`/`node2-api`,
senza passare per l'orchestratore `backend/app.py`, senza le code RQ
indipendenti su `node1-worker`/`node2-worker` il cui disallineamento
sotto carico continuo è la causa reale documentata in §9.6. Non c'è
alcuna coda da disallineare in questo sweep. Quindi "0/24 fallimenti
per entrambi" non dimostra che tinier sia più affidabile di mascot, e
nemmeno che lo sia "in condizioni isolate" nello stesso senso di
§9.5/9.6 — dimostra solo che l'esecuzione MP-SPDZ nuda, fuori
dall'architettura a container, è affidabile per entrambi i protocolli,
il che è un'altra domanda. Un confronto di affidabilità vero tra tinier
e mascot richiederebbe integrare tinier nei nodi Docker e ripetere lo
stress di §9.5/9.6 con tinier al posto di mascot — non fatto qui,
deliberatamente fuori scope per questo prompt.

**Conclusione, con la stessa onestà già usata per la correzione ×3.8
mascot/mama**: qui non c'è un'inversione di verdetto da correggere come
in quel caso — i dati non sono contraddittori, sono semplicemente
incompleti su un asse. Il vantaggio di tinier su tempo e dati è reale,
ampio, e cresce con N; ma "tinier supera mascot su affidabilità" **non
è dimostrato da questo sweep**, perché non è stato messo alla prova
nelle stesse condizioni (carico concorrente) che avevano reso
l'affidabilità il criterio decisivo la prima volta. Il quadro è quindi:
**vantaggio netto su un criterio, criterio mancante sull'altro** — non
un "tinier vince su entrambi" e non un "quadro misto" nel senso di
risultati in conflitto, ma un risultato a metà, onestamente incompleto
sull'asse che più contava.

## Cosa NON cambia per questo risultato

Anche un vantaggio di tempo/dati così ampio **non rende tinier pronto
per uso reale**. Resta un file sperimentale isolato:
- non integrato nei nodi Docker né nell'orchestratore;
- mai sottoposto all'irrobustimento di concorrenza/affidabilità già
  fatto per mascot (porte dedicate per richiesta, richieste consecutive
  senza pausa, un party che muore a metà calcolo — `node/README.md`
  §8–9.6);
- la correttezza è validata su 34 scenari statici
  (`results_binario_produzione_sperimentale/`) più i 4 di questo giro,
  non sotto le stesse condizioni avversarie già esercitate su mascot.

Se questo risultato deve portare a riconsiderare la raccomandazione
mascot-in-produzione, serve un giro separato ed esplicito che ripeta
su tinier lo stesso stress concorrente già fatto per mascot — non è
incluso in questo prompt e non va dedotto implicitamente da questi
numeri.
