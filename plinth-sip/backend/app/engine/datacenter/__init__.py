"""Data-center feasibility analyzer.

Parallel to `app/engine/rules/` (the ADU rules engine). On-demand,
per-parcel; not part of the batch scan flow. Returns the JSON shape
documented in `docs/datacenter-feasibility.md`.

Phase 1 lands `distance.py` and `iso.py` (consumed by both the loaders
and the analyzer). Phases 2-3 land the rest.
"""
