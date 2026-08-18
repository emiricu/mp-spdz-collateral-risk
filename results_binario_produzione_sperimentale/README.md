# Esperimento: produzione su dominio binario con tinier, composizione a bit nativa

Esperimento isolato, non una feature consegnata. Domanda: la composizione
della decisione di `decide_production` (attualmente vietata in dominio
binario, vedi il `CompilerError` in cima a
`Programs/Source/crypto_collateral_risk.mpc`) può funzionare nel dominio
binario se riscritta con operatori bit a bit nativi (`&`, `|`, `~`)
invece dell'aritmetica su interi (`+`, `-`, `*`) che si era rivelata
inaffidabile in una revisione precedente?

## Passo 1 — probe isolati

Tre probe minimi, `Programs/Source/probe_binary_bitops_{1,2,2b,3}.mpc`,
compilati `-B 64` ed eseguiti con `tinier` (`Scripts/tinier.sh`):

1. **`probe_binary_bitops_1.mpc`** — confronto segreto (`a < b`, `a > b`,
   `a == b`) combinato con `&`, `|`, `^`, `~` nativi, rivelato con
   `.reveal()`. **Esito: corretto su tutti i 7 valori × 3 coppie di
   input testate**, verificato a mano contro la tabella di verità attesa.
2. **`probe_binary_bitops_2.mpc`** (prima versione) — tentativo di
   decodificare il valore impacchettato **dentro** l'.mpc con
   scorrimento/mascheramento dopo il `.reveal()`. **Fallito a runtime**:
   `Code not defined for instruction CONVINT` — la stessa classe di
   errore già nota per `|`/`&` su valori regint/cint rivelati nel
   dominio binario (vedi il docstring di `compute_flags` in
   `crypto_collateral_risk.mpc`), qui confermata anche per lo
   scorrimento/mascheramento POST-reveal, non solo per `|`/`&` diretti.
3. **`probe_binary_bitops_2b.mpc`** (versione corretta, decodifica in
   Python dopo il reveal, non dentro l'.mpc) — impacchettamento di 4
   valori segreti distinti tramite due tecniche indipendenti (scorrimento
   + OR, e `bit_decompose`+`bit_compose`), un solo `.reveal()`.
   **Esito: corretto su tutti e 4 gli scenari testati, le due tecniche
   concordano esattamente, zero perdita/corruzione di bit.**
4. **`probe_binary_bitops_3.mpc`** — un valore già rivelato in precedenza
   (come `r_any_invalid`), riconvertito a segreto (`sint(valore_chiaro)`)
   e combinato con un flag ancora segreto tramite `&`. **Esito: corretto
   su tutti e 4 gli scenari testati.**

**Conclusione Passo 1**: le primitive di base si comportano come atteso,
con un limite preciso e ora documentato (punto 2) — nessuna ulteriore
aritmetica bit a bit va tentata su un valore già rivelato nel dominio
binario, la decodifica del valore impacchettato deve avvenire fuori
dall'MPC, nel livello Python chiamante.

## Passo 2 — il programma sperimentale

`Programs/Source/crypto_collateral_risk_binary_produzione_sperimentale.mpc`.
Logica di business (costanti di policy, le cinque funzioni di
sotto-punteggio) copiata identica dalla versione field/ring — non
serviva cambiarla, già portabile (usata da `decide_public`/
`MODE_BENCHMARK`, l'unica modalità finora compilata per il dominio
binario). Riscritti solo:
- `decide_production_binary`: le sette flag combinate con `~`/`&` invece
  di `sint(1)-flag`/`*`.
- L'impacchettamento finale (decision code, punteggio, max_ltv_bps,
  liq_threshold_bps) tramite scorrimento+OR (tecnica A del Passo 1,
  confermata), un solo `.reveal()` broadcast. Il valore impacchettato
  viene stampato grezzo — la decodifica avviene nello script di
  confronto Python, non nel programma MPC (vedi punto 2 sopra).

Compila senza errori: `./compile.py -B 64
crypto_collateral_risk_binary_produzione_sperimentale 2 0`.

## Passo 3 — 34 scenari, confronto campo per campo

Scenario di riferimento più altri 33 pensati per toccare i confini di
ciascuno dei cinque componenti del punteggio (volatilità, correlazione,
liquidità, età wallet, leva), i tre esiti possibili, e il caso limite
`any_invalid`. Ogni scenario eseguito identico su:
- **Binario sperimentale**: `crypto_collateral_risk_binary_produzione_sperimentale`,
  protocollo `tinier`.
- **Field produzione**: `crypto_collateral_risk.mpc` compilato `mode=2`
  dominio field, protocollo `mascot` (la versione già fidata e
  verificata in produzione).

Tabella completa in `tabella.md`, dati grezzi in `scenari_confronto.json`.

**Risultato: 34 scenari su 34 concordano esattamente** su
esito/risk_score/max_ltv_bps/liq_threshold_bps, incluse tutte le
transizioni di confine e tutti e tre gli esiti (APPROVATO, REVIEW
MANUALE, RIFIUTATO) più il caso `any_invalid`. Nessuna divergenza
trovata — lo script di confronto si sarebbe fermato immediatamente al
primo scostamento (vedi `binary_experiment_compare.py` nello scratchpad
di sessione, non incluso nel repo).

## Cosa NON è stato fatto, deliberatamente

Questo file sperimentale **non** è integrato in nessun nodo Docker,
nell'orchestratore, o in `crypto_collateral_risk_binary_benchmark.mpc`
(che tra l'altro non esiste ancora in questo repo). Resta un esperimento
isolato. Se in futuro si decide di promuoverlo, va rivisto e integrato
in un passaggio separato ed esplicito, non implicito in questo commit.
