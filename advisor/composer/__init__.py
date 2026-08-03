"""Phase 3 — the environment composer.

Every advisor component before this one SELECTS from a catalog (single
service, single pattern). This package COMPUTES a whole environment: parsed
inventory -> inferred components -> network arithmetic -> build sequence ->
InfoSec gate. Same discipline as the rest of advisor/: "Rules decide. LLM
explains. Forms validate. Azure deploys." — nothing in this package ever
calls an LLM, and network_planner.py in particular must never have its
arithmetic touched by one.
"""
