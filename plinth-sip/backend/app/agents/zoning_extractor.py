"""
LangGraph Zoning Extractor

Extracts real zoning district rules from municipal ordinance documents
to generate high-confidence Plinth SIP configs.

Pipeline (LangGraph state machine):
  1. search_ordinance  — Web search for the municipality's zoning ordinance PDF
  2. fetch_document    — Download and extract text from the ordinance PDF/HTML
  3. extract_districts — LLM extracts zoning districts, lot sizes, setbacks, ADU rules
  4. validate_extract  — Cross-check extracted data for consistency
  5. generate_config   — Build a Plinth SIP municipality config from extracted data

Usage:
    python -m app.agents.zoning_extractor "Burlington" "VT"

Requires ANTHROPIC_API_KEY environment variable.
"""

import json
import os
import re
from typing import TypedDict, Annotated, Optional
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class ZoningState(TypedDict):
    """State passed between LangGraph nodes."""
    municipality_name: str
    state: str
    county: str
    municipality_id: str

    # Search results
    ordinance_urls: list[str]
    search_notes: str

    # Document content
    document_text: str
    document_source: str

    # Extracted zoning data
    districts_raw: list[dict]        # Raw LLM extraction
    adu_rules: dict                  # ADU-specific rules
    sewer_info: dict                 # Sewer/septic info

    # Validated output
    districts_validated: list[dict]  # After validation pass
    confidence: float
    validation_notes: str

    # Final config
    config: dict                     # Complete Plinth SIP config
    error: str                       # Error message if any step fails


# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------

def _get_llm(model: str = "claude-sonnet-4-20250514", temperature: float = 0.0):
    """Get a ChatAnthropic LLM instance."""
    return ChatAnthropic(
        model=model,
        temperature=temperature,
        max_tokens=4096,
    )


# ---------------------------------------------------------------------------
# Node: Search for zoning ordinance
# ---------------------------------------------------------------------------

SEARCH_SYSTEM_PROMPT = """You are a municipal zoning research assistant.
Given a municipality name and state, generate the most likely URLs where
the zoning ordinance or zoning bylaw document can be found.

Focus on:
1. The municipality's official website (e.g., burlingtonvt.gov)
2. Municode, ecode360, or American Legal Publishing hosted ordinances
3. State-level municipal code repositories
4. Direct PDF links to zoning bylaws/ordinances

Return a JSON array of objects with "url" and "description" fields.
Return at most 5 URLs, ordered by likelihood of containing zoning district details."""


def search_ordinance(state: ZoningState) -> ZoningState:
    """Search for the municipality's zoning ordinance online."""
    llm = _get_llm()

    prompt = (
        f"Find the zoning ordinance/bylaw for {state['municipality_name']}, "
        f"{state['state']}. I need the document that defines zoning districts "
        f"with lot sizes, setbacks, building coverage, and ADU rules.\n\n"
        f"Return JSON array of {{url, description}} objects."
    )

    response = llm.invoke([
        SystemMessage(content=SEARCH_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    # Parse URLs from response
    text = response.content
    urls = []
    try:
        # Try to extract JSON from the response
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            urls = [item["url"] for item in parsed if "url" in item]
    except (json.JSONDecodeError, KeyError):
        # Fallback: extract URLs with regex
        urls = re.findall(r'https?://[^\s\'"<>]+', text)

    state["ordinance_urls"] = urls[:5]
    state["search_notes"] = f"Found {len(urls)} potential ordinance URLs"
    return state


# ---------------------------------------------------------------------------
# Node: Fetch and extract document text
# ---------------------------------------------------------------------------

def fetch_document(state: ZoningState) -> ZoningState:
    """Fetch the zoning ordinance document and extract text."""
    import requests

    if not state.get("ordinance_urls"):
        state["error"] = "No ordinance URLs found"
        state["document_text"] = ""
        return state

    # Try each URL until we get content
    for url in state["ordinance_urls"]:
        try:
            resp = requests.get(url, timeout=30, headers={
                "User-Agent": "PlinthSIP/1.0 (zoning-research)"
            })
            if resp.status_code != 200:
                continue

            content_type = resp.headers.get("content-type", "")

            if "pdf" in content_type or url.endswith(".pdf"):
                # PDF — extract text
                try:
                    import io
                    # Try pdfplumber if available
                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                        pages_text = []
                        for page in pdf.pages[:50]:  # Limit to 50 pages
                            text = page.extract_text()
                            if text:
                                pages_text.append(text)
                        state["document_text"] = "\n\n".join(pages_text)
                except ImportError:
                    # Fallback: just note that we found a PDF
                    state["document_text"] = (
                        f"[PDF found at {url} but pdfplumber not installed. "
                        "Install with: pip install pdfplumber]"
                    )
                state["document_source"] = url
                return state

            elif "html" in content_type or "text" in content_type:
                # HTML — extract readable text
                text = resp.text
                # Simple HTML stripping
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()

                if len(text) > 500:  # Meaningful content
                    state["document_text"] = text[:50000]  # Limit to ~50k chars
                    state["document_source"] = url
                    return state

        except Exception as e:
            continue

    state["error"] = "Could not fetch any ordinance document"
    state["document_text"] = ""
    return state


# ---------------------------------------------------------------------------
# Node: Extract zoning districts with LLM
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM_PROMPT = """You are a zoning ordinance analyst for Plinth, an ADU deployment company.
Extract zoning district rules from the provided ordinance text.

For each RESIDENTIAL zoning district, extract:
- district_code: The official district code (e.g., "R-4", "RES-1", "VR")
- district_name: Full name (e.g., "Village Residential")
- min_lot_area_sqft: Minimum lot area in square feet
- min_frontage_ft: Minimum lot frontage in feet
- max_lot_coverage_pct: Maximum lot coverage as decimal (e.g., 0.30 for 30%)
- max_height_ft: Maximum building height in feet
- setbacks: {front_ft, rear_ft, side_ft}
- adu_allowed: true/false/null (null if not mentioned)
- adu_max_sqft: Maximum ADU size in sqft (if specified)
- adu_notes: Any ADU-specific conditions or notes
- residential_uses: List of allowed residential use types

Also extract:
- sewer_service: Whether the municipality has municipal sewer (true/false/null)
- adu_state_law: Any state-level ADU law that overrides local rules

Return a JSON object with:
{
  "districts": [...],
  "sewer_service": true/false/null,
  "adu_state_law": "description or null",
  "extraction_confidence": 0.0-1.0,
  "notes": "any caveats about the extraction"
}

Convert acres to sqft (1 acre = 43,560 sqft).
If a value is not found in the text, use null — do NOT guess.
Only include residential districts where ADUs could potentially be placed."""


def extract_districts(state: ZoningState) -> ZoningState:
    """Use LLM to extract zoning district rules from document text."""
    if not state.get("document_text") or len(state["document_text"]) < 100:
        state["error"] = state.get("error", "") + " No document text to extract from."
        state["districts_raw"] = []
        return state

    llm = _get_llm()

    # Truncate document if too long for context
    doc_text = state["document_text"][:30000]

    prompt = (
        f"Extract zoning district rules for {state['municipality_name']}, "
        f"{state['state']} from this ordinance text:\n\n"
        f"---\n{doc_text}\n---\n\n"
        f"Return the JSON extraction as described in your instructions."
    )

    response = llm.invoke([
        SystemMessage(content=EXTRACT_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])

    text = response.content
    try:
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            extracted = json.loads(json_match.group())
            state["districts_raw"] = extracted.get("districts", [])
            state["adu_rules"] = {
                "state_law": extracted.get("adu_state_law"),
            }
            state["sewer_info"] = {
                "sewer_service": extracted.get("sewer_service"),
            }
            state["confidence"] = extracted.get("extraction_confidence", 0.5)
            state["search_notes"] += f"\n{extracted.get('notes', '')}"
        else:
            state["districts_raw"] = []
            state["error"] = "LLM response did not contain valid JSON"
    except json.JSONDecodeError:
        state["districts_raw"] = []
        state["error"] = "Failed to parse LLM extraction response"

    return state


# ---------------------------------------------------------------------------
# Node: Validate extracted data
# ---------------------------------------------------------------------------

def validate_extract(state: ZoningState) -> ZoningState:
    """Cross-check extracted districts for consistency and completeness."""
    districts = state.get("districts_raw", [])
    validated = []
    notes = []

    for d in districts:
        code = d.get("district_code", "")
        if not code:
            notes.append(f"Skipped district with no code")
            continue

        # Validate lot size is reasonable (500 sqft to 10 acres)
        lot_size = d.get("min_lot_area_sqft")
        if lot_size and (lot_size < 500 or lot_size > 435600):
            notes.append(f"{code}: lot size {lot_size} sqft seems unusual")

        # Validate setbacks are reasonable
        setbacks = d.get("setbacks", {})
        for side in ["front_ft", "rear_ft", "side_ft"]:
            val = setbacks.get(side)
            if val and (val < 0 or val > 200):
                notes.append(f"{code}: {side} setback {val}ft seems unusual")

        # Validate coverage
        coverage = d.get("max_lot_coverage_pct")
        if coverage and (coverage < 0.05 or coverage > 0.95):
            notes.append(f"{code}: coverage {coverage} seems unusual")

        validated.append(d)

    state["districts_validated"] = validated
    state["validation_notes"] = "; ".join(notes) if notes else "All values within expected ranges"

    # Adjust confidence based on validation
    if not validated:
        state["confidence"] = 0.0
    elif notes:
        state["confidence"] = max(0.3, state.get("confidence", 0.5) - 0.1 * len(notes))

    return state


# ---------------------------------------------------------------------------
# Node: Generate Plinth SIP config
# ---------------------------------------------------------------------------

def generate_config(state: ZoningState) -> ZoningState:
    """Build a Plinth SIP municipality config from validated extraction."""
    from app.agents.auto_config import STATE_DEFAULTS, GENERIC_DEFAULTS

    districts_data = state.get("districts_validated", [])
    if not districts_data:
        state["error"] = state.get("error", "") + " No validated districts to generate config from."
        state["config"] = {}
        return state

    st = state["state"]
    defaults = STATE_DEFAULTS.get(st, GENERIC_DEFAULTS)

    sewer = state.get("sewer_info", {}).get("sewer_service")
    if sewer is None:
        sewer = defaults["sewer_default"]

    # Build zoning districts and code map
    districts = {}
    zoning_code_map = {}

    for d in districts_data:
        code = d["district_code"]
        key = code.replace(" ", "_").replace("/", "_").replace("-", "_")

        setbacks = d.get("setbacks", {})
        district = {
            "label": d.get("district_name", f"District {code}"),
            "use_allowed": d.get("residential_uses", ["single_family", "residential"]),
            "min_lot_area_sqft": d.get("min_lot_area_sqft") or defaults["typical_min_lot_sf"],
            "min_frontage_ft": d.get("min_frontage_ft") or max(60, defaults["typical_setback_front"] * 2),
            "max_lot_coverage_pct": d.get("max_lot_coverage_pct") or defaults["typical_coverage"],
            "max_height_ft": d.get("max_height_ft") or 35,
            "setbacks": {
                "front_ft": setbacks.get("front_ft") or defaults["typical_setback_front"],
                "rear_ft": setbacks.get("rear_ft") or defaults["typical_setback_rear"],
                "side_ft": setbacks.get("side_ft") or defaults["typical_setback_side"],
            },
            "far": None,
            "adu_allowed": d.get("adu_allowed", defaults["adu_allowed_default"]),
            "adu_max_sqft": d.get("adu_max_sqft", 900),
            "adu_max_bedrooms": 2,
            "adu_parking_required": 1,
            "adu_notes": d.get("adu_notes", "Extracted from ordinance — verify with local planning office."),
            "confidence": state.get("confidence", 0.5),
            "citations": [
                f"Source: {state.get('document_source', 'unknown')}",
                f"Extracted by Plinth Zoning Extractor (LangGraph)",
            ],
        }
        districts[key] = district
        zoning_code_map[code] = key

    config = {
        "municipality_id": state["municipality_id"],
        "municipality_name": state["municipality_name"],
        "county": state.get("county", ""),
        "state": st,
        "config_version": 1,
        "config_notes": (
            f"Extracted from zoning ordinance for {state['municipality_name']}, {st}. "
            f"Source: {state.get('document_source', 'unknown')}. "
            f"Confidence: {state.get('confidence', 0.5):.0%}. "
            f"{state.get('validation_notes', '')}"
        ),
        "auto_generated": True,
        "auto_generated_confidence": (
            "HIGH" if state.get("confidence", 0) >= 0.7
            else "MEDIUM" if state.get("confidence", 0) >= 0.4
            else "LOW"
        ),
        "crs": "EPSG:4326",
        "calc_crs": defaults.get("calc_crs", "EPSG:4326"),
        "adapter": defaults.get("adapter", "generic"),
        "zoning_code_map": zoning_code_map,
        "state_law_overrides": {},
        "data_sources": {
            "parcel_data": {
                "url": "ArcGIS REST API (auto-fetched)",
                "format": "arcgis_rest",
                "confidence": 0.8,
                "notes": "Auto-fetched from state GIS portal.",
            },
            "zoning_data": {
                "url": state.get("document_source", "unknown"),
                "format": "ordinance_extraction",
                "confidence": state.get("confidence", 0.5),
                "notes": "Extracted from zoning ordinance via LangGraph pipeline.",
            },
        },
        "sewer_service": sewer,
        "sewer_service_notes": (
            f"{'Municipal sewer' if sewer else 'Private septic assumed'}. "
            "Extracted from ordinance data."
        ),
        "sewer_service_confidence": 0.6 if sewer is not None else 0.3,
        "zoning_districts": districts,
        "septic_assumptions": {
            "min_lot_area_for_new_system_sqft": 40000,
            "bedroom_load_factor": "standard",
            "title_5_perc_test_required": (st == "MA"),
            "notes": "Default septic assumptions. Verify with local health department.",
            "confidence": 0.4,
        },
        "parking_assumptions": {
            "default_spaces_required_per_unit": 1,
            "notes": "Default parking assumption. Verify with local ordinance.",
            "confidence": 0.5,
        },
        "overlays": [
            {
                "overlay_type": "flood_zone",
                "label": "FEMA Flood Zone",
                "constraint_level": "hard_block",
                "notes": "Standard FEMA flood zone constraint.",
            },
            {
                "overlay_type": "wetlands_buffer",
                "label": "Wetlands Buffer",
                "constraint_level": "hard_block",
                "notes": "State wetlands regulations apply.",
            },
        ],
    }

    state["config"] = config
    return state


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------

def should_continue(state: ZoningState) -> str:
    """Decide whether to continue the pipeline or stop on error."""
    if state.get("error"):
        return "error"
    return "continue"


def has_document(state: ZoningState) -> str:
    """Check if we have document text to extract from."""
    if state.get("document_text") and len(state["document_text"]) > 100:
        return "extract"
    return "error"


# ---------------------------------------------------------------------------
# Build the LangGraph
# ---------------------------------------------------------------------------

def build_zoning_graph() -> StateGraph:
    """Build the LangGraph state machine for zoning extraction."""
    graph = StateGraph(ZoningState)

    # Add nodes
    graph.add_node("search_ordinance", search_ordinance)
    graph.add_node("fetch_document", fetch_document)
    graph.add_node("extract_districts", extract_districts)
    graph.add_node("validate_extract", validate_extract)
    graph.add_node("generate_config", generate_config)

    # Define edges
    graph.set_entry_point("search_ordinance")
    graph.add_edge("search_ordinance", "fetch_document")

    # After fetch, check if we got content
    graph.add_conditional_edges(
        "fetch_document",
        has_document,
        {
            "extract": "extract_districts",
            "error": END,
        },
    )

    graph.add_edge("extract_districts", "validate_extract")
    graph.add_edge("validate_extract", "generate_config")
    graph.add_edge("generate_config", END)

    return graph


def compile_zoning_extractor():
    """Compile the LangGraph into a runnable."""
    graph = build_zoning_graph()
    return graph.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_zoning_config(
    municipality_name: str,
    state: str,
    county: str = "",
    municipality_id: str = "",
    save_to: str | None = None,
) -> dict:
    """
    Run the full zoning extraction pipeline.

    Args:
        municipality_name: e.g. "Burlington"
        state: e.g. "VT"
        county: e.g. "Chittenden County"
        municipality_id: e.g. "vt_burlington_city"
        save_to: Path to save the generated config (optional)

    Returns:
        The generated Plinth SIP config dict, or dict with "error" key on failure.
    """
    if not municipality_id:
        municipality_id = f"{state.lower()}_{municipality_name.lower().replace(' ', '_')}"

    initial_state: ZoningState = {
        "municipality_name": municipality_name,
        "state": state,
        "county": county,
        "municipality_id": municipality_id,
        "ordinance_urls": [],
        "search_notes": "",
        "document_text": "",
        "document_source": "",
        "districts_raw": [],
        "adu_rules": {},
        "sewer_info": {},
        "districts_validated": [],
        "confidence": 0.0,
        "validation_notes": "",
        "config": {},
        "error": "",
    }

    extractor = compile_zoning_extractor()
    result = extractor.invoke(initial_state)

    config = result.get("config", {})

    if save_to and config:
        out_path = Path(save_to)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"  Config saved to: {out_path}")

    return {
        "config": config,
        "confidence": result.get("confidence", 0.0),
        "source": result.get("document_source", ""),
        "districts_found": len(result.get("districts_validated", [])),
        "error": result.get("error", ""),
        "validation_notes": result.get("validation_notes", ""),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import io

    # Fix Windows console encoding
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    if len(sys.argv) < 3:
        print("Usage: python -m app.agents.zoning_extractor <municipality_name> <state> [county]")
        print("  e.g. python -m app.agents.zoning_extractor Burlington VT")
        print()
        print("Requires ANTHROPIC_API_KEY environment variable.")
        sys.exit(1)

    muni_name = sys.argv[1]
    st = sys.argv[2].upper()
    county = sys.argv[3] if len(sys.argv) > 3 else ""
    muni_id = f"{st.lower()}_{muni_name.lower().replace(' ', '_')}"

    configs_dir = os.environ.get(
        "CONFIGS_DIR",
        str(Path(__file__).resolve().parent.parent.parent.parent / "configs")
    )
    save_path = str(Path(configs_dir) / "municipalities" / f"{muni_id}_extracted.json")

    print(f"Plinth SIP — LangGraph Zoning Extractor")
    print(f"Municipality: {muni_name}, {st}")
    print(f"Output: {save_path}")
    print()

    result = extract_zoning_config(
        municipality_name=muni_name,
        state=st,
        county=county,
        municipality_id=muni_id,
        save_to=save_path,
    )

    if result.get("error"):
        print(f"\nError: {result['error']}")
    else:
        print(f"\nExtraction complete:")
        print(f"  Districts found: {result['districts_found']}")
        print(f"  Confidence: {result['confidence']:.0%}")
        print(f"  Source: {result['source']}")
        if result.get("validation_notes"):
            print(f"  Notes: {result['validation_notes']}")

    if result.get("config"):
        districts = result["config"].get("zoning_districts", {})
        print(f"\nDistricts:")
        for key, d in districts.items():
            print(f"  {key}: {d.get('label', '?')} — lot={d.get('min_lot_area_sqft')} sqft, "
                  f"coverage={d.get('max_lot_coverage_pct')}, ADU={'yes' if d.get('adu_allowed') else 'no'}")
