"""Additive research harness (panel-only).

This package is RESEARCH SCRATCH for the quant panel: vectorized alpha scanning over
the parquet archive. It is intentionally decoupled from the production engine
(runner/risk/strategies) and must never be imported by live code. Cardinal rule:
point-in-time / no look-ahead. Features at bar ``t`` use only data <= t; labels use
forward bars and are NEVER fed back as features.
"""
