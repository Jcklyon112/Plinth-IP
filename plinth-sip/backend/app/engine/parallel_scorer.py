"""
Parallel in-memory parcel scorer.
Scores parcels using ThreadPoolExecutor — no database writes.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from app.engine.runner import evaluate_parcel, normalize_land_use_type


def score_parcels_parallel(
    parcels: list[dict],
    config: dict,
    templates: list[dict] | None = None,
    max_workers: int = 8,
) -> list[dict]:
    """
    Score parcels in parallel. Returns list of parcel dicts with scores added.
    Nothing is written to the database.
    """
    if templates is None:
        templates = []

    # Derive state_code once for all parcels
    state_code = config.get("state", "")

    results = []

    def score_one(parcel: dict) -> dict:
        try:
            # Normalize land_use_type so the output reflects mapped types
            parcel = normalize_land_use_type(parcel, config, state_code)
            result = evaluate_parcel(parcel, config, templates)
            # Use result directly — it includes the enriched parcel plus scores
            return result
        except Exception as e:
            return {
                **parcel,
                "score": None,
                "tier": 4,
                "confidence": 0,
                "rule_results": [],
                "score_breakdown": {},
                "blockers": [],
                "error": str(e)[:200],
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(score_one, p): i for i, p in enumerate(parcels)}
        for future in as_completed(futures):
            results.append(future.result())

    return sorted(results, key=lambda x: x.get("score") or 0, reverse=True)
