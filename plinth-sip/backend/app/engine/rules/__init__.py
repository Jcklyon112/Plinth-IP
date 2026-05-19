from app.engine.rules.base import RuleResult, RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL, RESULT_UNKNOWN
from app.engine.rules.dimensional import (
    min_lot_size_rule, adu_max_size_rule, lot_coverage_rule, buildable_envelope_rule
)
from app.engine.rules.use import use_allowed_rule, adu_permitted_rule
from app.engine.rules.physical import (
    overlay_constraints_rule, access_likely_rule, slope_buildability_rule, electrical_service_rule
)
from app.engine.rules.septic import sewer_available_rule, septic_capacity_rule
from app.engine.rules.deployment import delivery_access_rule, existing_structures_rule
