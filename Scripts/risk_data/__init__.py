"""Public-data layer for the crypto-collateral risk demo.

Everything under this package runs OUTSIDE MP-SPDZ, in the clear: price
history, on-chain wallet analysis, and the metrics/scaling derived from
them. None of it is secret-shared. See Scripts/crypto_risk_run.py for how
its output is fed into Programs/Source/crypto_collateral_risk.mpc as the
requesting platform's private input.
"""
