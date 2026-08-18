# Confronto binario sperimentale (tinier) vs field produzione (mascot), 34 scenari

| # | Scenario | Esito bin | Esito field | Score bin/field | max_ltv bin/field | liq_thr bin/field |
|---|---|---|---|---|---|---|
| 1 | baseline (tutto in zona comoda, atteso APPROVATO) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 2 | vol=499 (appena sotto confine stable/bluechip) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 3 | vol=500 (appena sopra, bluechip) | APPROVATO | APPROVATO | 98/98 | 6500/6500 | 7500/7500 |
| 4 | vol=2999 (appena sotto confine bluechip/medium) | APPROVATO | APPROVATO | 98/98 | 6500/6500 | 7500/7500 |
| 5 | vol=3000 (appena sopra, medium) | APPROVATO | APPROVATO | 90/90 | 5000/5000 | 6000/6000 |
| 6 | vol=5999 (appena sotto confine medium/high) | APPROVATO | APPROVATO | 90/90 | 5000/5000 | 6000/6000 |
| 7 | vol=6000 (appena sopra, high) | APPROVATO | APPROVATO | 83/83 | 3500/3500 | 4500/4500 |
| 8 | vol=15001 (vol_extreme, atteso RIFIUTATO) | RIFIUTATO | RIFIUTATO | 83/83 | 3500/3500 | 4500/4500 |
| 9 | corr=2999 (appena sotto confine low/med) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 10 | corr=3000 (appena sopra, med) | APPROVATO | APPROVATO | 93/93 | 8500/8500 | 9500/9500 |
| 11 | corr=5999 (appena sotto confine med/high) | APPROVATO | APPROVATO | 93/93 | 8500/8500 | 9500/9500 |
| 12 | corr=6000 (appena sopra, high) | APPROVATO | APPROVATO | 85/85 | 8500/8500 | 9500/9500 |
| 13 | liq=99999 (appena sotto confine good) | APPROVATO | APPROVATO | 94/94 | 8500/8500 | 9500/9500 |
| 14 | liq=100000 (appena sopra, good) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 15 | liq=19999 (appena sotto confine ok) | APPROVATO | APPROVATO | 89/89 | 8500/8500 | 9500/9500 |
| 16 | liq=20000 (appena sopra, ok) | APPROVATO | APPROVATO | 94/94 | 8500/8500 | 9500/9500 |
| 17 | liq=4999 (illiquid, atteso RIFIUTATO) | RIFIUTATO | RIFIUTATO | 85/85 | 8500/8500 | 9000/9000 |
| 18 | liq=5000 (appena sopra, min) | APPROVATO | APPROVATO | 89/89 | 8500/8500 | 9500/9500 |
| 19 | age=365 (NON established, confine stretto >) | APPROVATO | APPROVATO | 96/96 | 8500/8500 | 9500/9500 |
| 20 | age=366 (established) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 21 | age=90 (NON new_ish, confine stretto >) | APPROVATO | APPROVATO | 92/92 | 8500/8500 | 9500/9500 |
| 22 | age=91 (new_ish) | APPROVATO | APPROVATO | 96/96 | 8500/8500 | 9500/9500 |
| 23 | flags=0 (pulito) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 24 | flags=1 (una segnalazione) | APPROVATO | APPROVATO | 96/96 | 8500/8500 | 9500/9500 |
| 25 | flags=2 (flags_bad, atteso RIFIUTATO) | RIFIUTATO | RIFIUTATO | 93/93 | 8500/8500 | 9500/9500 |
| 26 | flags=3 (flags_bad, atteso RIFIUTATO) | RIFIUTATO | RIFIUTATO | 93/93 | 8500/8500 | 9500/9500 |
| 27 | leva: loan=42000 (appena sotto confine half, tier0) | APPROVATO | APPROVATO | 100/100 | 8500/8500 | 9500/9500 |
| 28 | leva: loan=43000 (appena sopra, tier1) | APPROVATO | APPROVATO | 90/90 | 8500/8500 | 9500/9500 |
| 29 | leva: loan=63000 (appena sotto confine three_q, tier1) | APPROVATO | APPROVATO | 90/90 | 8500/8500 | 9500/9500 |
| 30 | leva: loan=64000 (appena sopra, tier2) | APPROVATO | APPROVATO | 80/80 | 8500/8500 | 9500/9500 |
| 31 | leva: loan=84000 (appena sotto confine within, tier2) | REVIEW MANUALE | REVIEW MANUALE | 80/80 | 8500/8500 | 9500/9500 |
| 32 | leva: loan=86000 (appena sopra, tier3, oltre policy) | RIFIUTATO | RIFIUTATO | 65/65 | 8500/8500 | 9500/9500 |
| 33 | any_invalid: existing_debt_p1 oltre il cap, atteso RIFIUTATO | RIFIUTATO | RIFIUTATO | 65/65 | 8500/8500 | 9500/9500 |
| 34 | review manuale: punteggio 57<70, nessun rifiuto netto | REVIEW MANUALE | REVIEW MANUALE | 57/57 | 5000/5000 | 6000/6000 |
