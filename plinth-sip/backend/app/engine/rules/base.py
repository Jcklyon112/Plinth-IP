from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuleResult:
    rule_id: str
    rule_category: str          # dimensional | use | physical | septic | deployment
    result: str                 # pass | conditional | fail | unknown
    explanation: str
    assumptions_used: dict = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_category": self.rule_category,
            "result": self.result,
            "explanation": self.explanation,
            "assumptions_used": self.assumptions_used,
            "confidence": self.confidence,
        }


RESULT_PASS = "pass"
RESULT_CONDITIONAL = "conditional"
RESULT_FAIL = "fail"
RESULT_UNKNOWN = "unknown"
