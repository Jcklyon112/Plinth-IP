"""Run the data-center analyzer against a small set of parcels and
write the JSON results to docs/samples/.

Usage (from plinth-sip/backend with the venv active):

    python scripts/dc_demo.py
        --parcel ma_acton/M_192712_899423=strong
        --parcel ny_babylon_town/SOMEID=mid
        --parcel ma_provincetown/SOMEID=weak

Each --parcel takes the form "<municipality_id>/<parcel_id>=<label>".
The script writes <repo>/docs/samples/dc-sample-<label>.json for each.

When invoked with no --parcel arguments, prints a usage hint. Skip a
parcel that doesn't resolve (logs a warning rather than crashing).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# sys.path setup for both `python scripts/dc_demo.py` and `python -m scripts.dc_demo`
THIS = Path(__file__).resolve()
BACKEND = THIS.parents[1]                  # plinth-sip/backend
PROJECT_ROOT = BACKEND.parent              # plinth-sip
for p in (BACKEND, PROJECT_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("dc_demo")


def parse_parcel_arg(s: str) -> tuple[str, str, str]:
    """Parse '<muni>/<parcel>=<label>' -> (muni, parcel, label)."""
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"--parcel must include =<label>: got {s!r}")
    spec, label = s.split("=", 1)
    if "/" not in spec:
        raise argparse.ArgumentTypeError(f"--parcel must be muni/id=label: got {s!r}")
    muni, parcel = spec.split("/", 1)
    return muni, parcel, label


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--parcel",
        action="append",
        default=[],
        help="<muni>/<parcel_id>=<label>; repeatable",
    )
    p.add_argument(
        "--out-dir",
        default=str(PROJECT_ROOT / "docs" / "samples"),
        help="Where to write dc-sample-<label>.json files",
    )
    args = p.parse_args()

    if not args.parcel:
        log.warning("No --parcel arguments provided. Example:")
        log.warning("  python scripts/dc_demo.py --parcel ma_acton/M_xxx=strong")
        return 0

    from app.database import SessionLocal
    from app.engine.datacenter.analyzer import analyze_parcel

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for raw in args.parcel:
        muni, parcel_id, label = parse_parcel_arg(raw)
        out_path = out_dir / f"dc-sample-{label}.json"
        log.info("Analyzing %s/%s (label=%s) ...", muni, parcel_id, label)
        sess = SessionLocal()
        try:
            result = analyze_parcel(
                sess,
                parcel_id=parcel_id,
                municipality_id=muni,
                use_cache=False,
            )
            sess.commit()
        except Exception:
            log.exception("Failed to analyze %s/%s", muni, parcel_id)
            failures.append(label)
            continue
        finally:
            sess.close()

        with out_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        log.info("  -> wrote %s (grade %s, composite %.1f)",
                 out_path, result.get("overallScore"), result.get("compositeScore", 0.0))

    if failures:
        log.error("Failed labels: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
