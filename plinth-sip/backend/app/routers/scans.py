from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Body
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import os
from app.database import get_db
from app.models.scan_run import ScanRun
from app.models.municipality import Municipality, MunicipalityConfig
from app.models.parcel import Parcel, ParcelGeometry, ParcelRuleResult, ParcelScore
from app.models.template import PlinthTemplate
from app.engine.runner import evaluate_parcel

router = APIRouter()


# ---------------------------------------------------------------------------
# Auto-scan endpoint (Phase 5)
# ---------------------------------------------------------------------------

@router.post("/auto-scan")
def auto_scan(
    payload: dict = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """
    Scan any US municipality from a single address, city+state, or zip code.

    Body: {"query": "Burlington, VT"} or {"query": "05401"}

    Returns immediately with a scan_run_id. Poll GET /scans/detail/{id} for status.
    """
    query = payload.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="'query' field is required")

    # Step 1: Resolve municipality
    from app.agents.municipality_resolver import resolve_municipality
    resolved = resolve_municipality(query)
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail=f"Could not resolve '{query}' to a US municipality. Try a more specific address."
        )

    municipality_id = resolved["municipality_id"]
    municipality_name = resolved["municipality_name"]
    state = resolved["state"]
    county = resolved["county"]

    # Step 2: Check state registry
    from app.agents.state_gis_registry import get_state_config
    state_cfg = get_state_config(state)
    if not state_cfg:
        raise HTTPException(
            status_code=422,
            detail=f"State '{state}' is not yet supported. Supported: MA, NH, VT, CT, ME, RI"
        )
    if state_cfg.get("parcel_source") != "arcgis_rest":
        raise HTTPException(
            status_code=422,
            detail=f"State '{state}' uses {state_cfg.get('parcel_source')} source — not yet supported by auto-scan."
        )

    # Step 3: Create or get municipality in DB
    muni = db.query(Municipality).filter(Municipality.municipality_id == municipality_id).first()
    if not muni:
        muni = Municipality(
            municipality_id=municipality_id,
            name=municipality_name,
            county=county,
            state=state,
        )
        db.add(muni)
        db.commit()

    # Step 4: Create scan run to track progress
    scan_run = ScanRun(
        municipality_id=municipality_id,
        config_version=0,
        status="queued",
        run_type="auto_scan",
        triggered_by="api",
        started_at=datetime.now(timezone.utc),
    )
    db.add(scan_run)
    db.commit()
    db.refresh(scan_run)
    scan_run_id = str(scan_run.id)

    # Step 5: Launch full pipeline as background task
    configs_dir = os.environ.get(
        "CONFIGS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "..", "configs")
    )

    background_tasks.add_task(
        _run_auto_scan_pipeline,
        scan_run_id=scan_run_id,
        municipality_id=municipality_id,
        municipality_name=municipality_name,
        state=state,
        county=county,
        state_cfg=state_cfg,
        configs_dir=configs_dir,
    )

    return {
        "scan_run_id": scan_run_id,
        "municipality_id": municipality_id,
        "municipality_name": municipality_name,
        "state": state,
        "county": county,
        "status": "queued",
        "message": f"Auto-scan started for {municipality_name}, {state}. Poll /scans/detail/{scan_run_id} for status.",
    }


def _run_auto_scan_pipeline(
    scan_run_id: str,
    municipality_id: str,
    municipality_name: str,
    state: str,
    county: str,
    state_cfg: dict,
    configs_dir: str,
):
    """
    Background task: full auto-scan pipeline.
    Fetch → config → ingest → score
    """
    from app.database import SessionLocal
    from app.agents.gis_fetcher import fetch_parcels_as_gdf
    from app.agents.auto_config import generate_and_save_config
    from app.ingestion.generic_ingest import ingest_from_gdf
    from geoalchemy2.shape import to_shape

    db = SessionLocal()
    scan = None

    def _update_status(status: str, notes: str = None):
        nonlocal scan
        if not scan:
            scan = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
        if scan:
            scan.status = status
            if notes:
                scan.error_log = notes
            db.commit()

    try:
        _update_status("fetching")

        # 1. Fetch parcels from ArcGIS REST
        gdf = fetch_parcels_as_gdf(state_cfg, municipality_name)
        print(f"[auto_scan] Fetched {len(gdf)} parcels for {municipality_name}, {state}")

        # 2. Generate config
        _update_status("configuring")
        config = generate_and_save_config(
            municipality_id=municipality_id,
            municipality_name=municipality_name,
            state=state,
            county=county,
            configs_dir=configs_dir,
            gdf=gdf,
        )

        # 3. Load config into DB
        _update_status("loading_config")
        existing_configs = db.query(MunicipalityConfig).filter(
            MunicipalityConfig.municipality_id == municipality_id
        ).all()
        for ec in existing_configs:
            ec.active = False
        db.commit()

        latest = db.query(MunicipalityConfig).filter(
            MunicipalityConfig.municipality_id == municipality_id
        ).order_by(MunicipalityConfig.version.desc()).first()
        next_version = (latest.version + 1) if latest else 1

        new_config = MunicipalityConfig(
            municipality_id=municipality_id,
            version=next_version,
            active=True,
            config_data=config,
            notes=f"Auto-generated by auto_scan pipeline",
        )
        db.add(new_config)
        db.commit()
        db.refresh(new_config)

        # Update scan run config version
        scan = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
        if scan:
            scan.config_version = next_version
            db.commit()

        # 4. Ingest parcels
        _update_status("ingesting")
        ingest_result = ingest_from_gdf(gdf, state_cfg, municipality_id)
        ingested = ingest_result.get("ingested", 0)
        print(f"[auto_scan] Ingested {ingested} parcels")

        scan = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
        if scan:
            scan.parcels_ingested = ingested
            db.commit()

        # 5. Score parcels
        _update_status("scoring")

        # Get active config and templates
        config_record = db.query(MunicipalityConfig).filter(
            MunicipalityConfig.municipality_id == municipality_id,
            MunicipalityConfig.active == True,
        ).first()
        if not config_record:
            raise RuntimeError("No active config found after loading")

        config_data = config_record.config_data

        templates = db.query(PlinthTemplate).filter(PlinthTemplate.active_status == True).all()
        template_dicts = [
            {
                "template_id": t.template_id,
                "template_name": t.template_name,
                "footprint_area_sqft": float(t.footprint_area_sqft),
                "active_status": t.active_status,
            }
            for t in templates
        ]

        parcels = db.query(Parcel).filter(Parcel.municipality_id == municipality_id).all()
        geom_records = db.query(ParcelGeometry).filter(
            ParcelGeometry.municipality_id == municipality_id
        ).all()
        geometry_by_parcel_id = {}
        for g in geom_records:
            try:
                geometry_by_parcel_id[g.parcel_id] = to_shape(g.geom)
            except Exception:
                pass

        calc_crs = config_data.get("calc_crs", "EPSG:4326")
        try:
            calc_epsg = int(calc_crs.replace("EPSG:", "").replace("epsg:", ""))
        except (ValueError, AttributeError):
            calc_epsg = 4326

        scored = 0
        score_errors = 0

        for parcel in parcels:
            try:
                parcel_dict = {
                    "parcel_id": parcel.parcel_id,
                    "lot_area_sqft": float(parcel.lot_area_sqft) if parcel.lot_area_sqft else None,
                    "zoning_code": parcel.zoning_code,
                    "land_use_type": parcel.land_use_type,
                    "assessed_use": parcel.assessed_use,
                    "existing_building_footprint_area": float(parcel.existing_building_footprint_area) if parcel.existing_building_footprint_area else None,
                    "existing_structure_count": parcel.existing_structure_count,
                    "constraints_flags": [],
                    "geometry_shapely": geometry_by_parcel_id.get(parcel.parcel_id),
                    "calc_epsg": calc_epsg,
                }

                result = evaluate_parcel(parcel_dict, config_data, template_dicts)

                db.query(ParcelRuleResult).filter(
                    ParcelRuleResult.parcel_id == parcel.parcel_id,
                    ParcelRuleResult.municipality_id == municipality_id,
                ).delete()
                db.query(ParcelScore).filter(
                    ParcelScore.parcel_id == parcel.parcel_id,
                    ParcelScore.municipality_id == municipality_id,
                ).delete()

                for rr in result["rule_results"]:
                    db.add(ParcelRuleResult(
                        parcel_id=parcel.parcel_id,
                        municipality_id=municipality_id,
                        scan_run_id=scan_run_id,
                        rule_id=rr["rule_id"],
                        rule_category=rr["rule_category"],
                        result=rr["result"],
                        explanation=rr["explanation"],
                        assumptions_used=rr["assumptions_used"],
                        confidence=rr["confidence"],
                    ))

                sr = result["score_record"]
                db.add(ParcelScore(
                    parcel_id=parcel.parcel_id,
                    municipality_id=municipality_id,
                    scan_run_id=scan_run_id,
                    scoring_profile=sr["scoring_profile"],
                    score=sr["score"],
                    tier=sr["tier"],
                    score_breakdown=sr["score_breakdown"],
                    confidence=sr["confidence"],
                    template_fits=sr["template_fits"],
                    blockers=sr["blockers"],
                ))

                scored += 1
                if scored % 200 == 0:
                    db.commit()
                    print(f"[auto_scan] ...{scored} parcels scored")

            except Exception as e:
                score_errors += 1
                continue

        db.commit()

        scan = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
        if scan:
            scan.status = "complete"
            scan.parcels_scored = scored
            scan.completed_at = datetime.now(timezone.utc)
            db.commit()

        print(f"[auto_scan] Complete: {scored} scored, {score_errors} errors")

    except Exception as e:
        import traceback
        traceback.print_exc()
        _update_status("failed", str(e))
        raise
    finally:
        db.close()


@router.get("/{municipality_id}")
def list_scan_runs(municipality_id: str, db: Session = Depends(get_db)):
    runs = db.query(ScanRun).filter(
        ScanRun.municipality_id == municipality_id
    ).order_by(ScanRun.created_at.desc()).all()
    return [_scan_to_dict(r) for r in runs]


@router.get("/detail/{scan_run_id}")
def get_scan_run(scan_run_id: str, db: Session = Depends(get_db)):
    run = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Scan run not found")
    return _scan_to_dict(run)


@router.post("/{municipality_id}/rescore")
def rescore_municipality(
    municipality_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Re-run scoring for all existing parcels in a municipality using the active config.
    Does not re-ingest data — only re-evaluates rules and scores.
    """
    config_record = db.query(MunicipalityConfig).filter(
        MunicipalityConfig.municipality_id == municipality_id,
        MunicipalityConfig.active == True,
    ).first()
    if not config_record:
        raise HTTPException(status_code=404, detail="No active config for this municipality")

    scan_run = ScanRun(
        municipality_id=municipality_id,
        config_version=config_record.version,
        status="running",
        run_type="rescore",
        triggered_by="api",
        started_at=datetime.now(timezone.utc),
    )
    db.add(scan_run)
    db.commit()
    db.refresh(scan_run)

    background_tasks.add_task(
        _run_scoring_task,
        scan_run_id=str(scan_run.id),
        municipality_id=municipality_id,
        config_data=config_record.config_data,
    )

    return {"scan_run_id": str(scan_run.id), "status": "running"}


def _run_scoring_task(scan_run_id: str, municipality_id: str, config_data: dict):
    """Background task: score all parcels for a municipality."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        templates = db.query(PlinthTemplate).filter(PlinthTemplate.active_status == True).all()
        template_dicts = [
            {
                "template_id": t.template_id,
                "template_name": t.template_name,
                "footprint_area_sqft": float(t.footprint_area_sqft),
                "active_status": t.active_status,
            }
            for t in templates
        ]

        parcels = db.query(Parcel).filter(Parcel.municipality_id == municipality_id).all()
        scored = 0

        for parcel in parcels:
            parcel_dict = {
                "parcel_id": parcel.parcel_id,
                "lot_area_sqft": float(parcel.lot_area_sqft) if parcel.lot_area_sqft else None,
                "zoning_code": parcel.zoning_code,
                "land_use_type": parcel.land_use_type,
                "assessed_use": parcel.assessed_use,
                "existing_building_footprint_area": float(parcel.existing_building_footprint_area) if parcel.existing_building_footprint_area else None,
                "existing_structure_count": parcel.existing_structure_count,
                "constraints_flags": [],
            }

            result = evaluate_parcel(parcel_dict, config_data, template_dicts)

            # Store rule results
            for rr in result["rule_results"]:
                db.add(ParcelRuleResult(
                    parcel_id=parcel.parcel_id,
                    municipality_id=municipality_id,
                    scan_run_id=scan_run_id,
                    rule_id=rr["rule_id"],
                    rule_category=rr["rule_category"],
                    result=rr["result"],
                    explanation=rr["explanation"],
                    assumptions_used=rr["assumptions_used"],
                    confidence=rr["confidence"],
                ))

            # Store score
            sr = result["score_record"]
            db.add(ParcelScore(
                parcel_id=parcel.parcel_id,
                municipality_id=municipality_id,
                scan_run_id=scan_run_id,
                scoring_profile=sr["scoring_profile"],
                score=sr["score"],
                tier=sr["tier"],
                score_breakdown=sr["score_breakdown"],
                confidence=sr["confidence"],
                template_fits=sr["template_fits"],
                blockers=sr["blockers"],
            ))
            scored += 1

            if scored % 100 == 0:
                db.commit()

        db.commit()

        scan = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
        if scan:
            scan.status = "complete"
            scan.parcels_scored = scored
            scan.completed_at = datetime.now(timezone.utc)
            db.commit()

    except Exception as e:
        db.rollback()
        scan = db.query(ScanRun).filter(ScanRun.id == scan_run_id).first()
        if scan:
            scan.status = "failed"
            scan.error_log = str(e)
            db.commit()
        raise
    finally:
        db.close()


def _scan_to_dict(run: ScanRun) -> dict:
    return {
        "id": str(run.id),
        "municipality_id": run.municipality_id,
        "config_version": run.config_version,
        "status": run.status,
        "run_type": run.run_type,
        "parcels_ingested": run.parcels_ingested,
        "parcels_scored": run.parcels_scored,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
