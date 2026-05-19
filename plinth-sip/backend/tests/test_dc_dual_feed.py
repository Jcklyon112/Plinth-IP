"""Unit tests for the dual-feed decision logic.

The DB-touching `dual_feed_feasible(session, ...)` is integration-tested
in Phase 2's PostGIS fixture (followup); here we lock down the pure
algorithm in `is_dual_feed_from_line_sets`, which is what determines
the answer once the two substations and their connected lines have
been pulled from the DB.
"""
from __future__ import annotations

import pytest

from app.engine.datacenter.proximity import (
    TRANSMISSION_VOLT_THRESHOLD_KV,
    is_dual_feed_from_line_sets,
)


def test_threshold_constant_is_115kv():
    assert TRANSMISSION_VOLT_THRESHOLD_KV == 115


# --- pure decision logic --------------------------------------------

def test_two_substations_with_disjoint_lines_are_dual_feed():
    sub_line_sets = [{"line-A", "line-B"}, {"line-X", "line-Y"}]
    assert is_dual_feed_from_line_sets(sub_line_sets) is True


def test_two_substations_sharing_a_line_are_not_dual_feed():
    # Single shared corridor is the failure case the heuristic targets:
    # a contingency that takes out line-A drops both substations.
    sub_line_sets = [{"line-A", "line-B"}, {"line-A", "line-Y"}]
    assert is_dual_feed_from_line_sets(sub_line_sets) is False


def test_two_substations_completely_overlapping_are_not_dual_feed():
    sub_line_sets = [{"line-A", "line-B"}, {"line-A", "line-B"}]
    assert is_dual_feed_from_line_sets(sub_line_sets) is False


def test_one_substation_alone_is_not_dual_feed():
    assert is_dual_feed_from_line_sets([{"line-A", "line-B"}]) is False


def test_zero_substations_is_not_dual_feed():
    assert is_dual_feed_from_line_sets([]) is False


def test_substation_with_empty_line_set_is_not_dual_feed():
    # No lines linked to a substation in our buffer -- treat as a
    # data-quality issue, refuse to claim dual-feed.
    assert is_dual_feed_from_line_sets([set(), {"line-X"}]) is False
    assert is_dual_feed_from_line_sets([{"line-A"}, set()]) is False


def test_three_substations_only_first_two_considered():
    # The DB query uses LIMIT 2; this test confirms the pure helper
    # uses only the first two even if more get passed in by mistake.
    third = {"line-A"}  # would taint set-A if the function over-reached
    sub_line_sets = [{"line-X"}, {"line-Y"}, third]
    assert is_dual_feed_from_line_sets(sub_line_sets) is True


def test_single_line_per_substation_disjoint_is_dual_feed():
    """The minimal positive case: each substation has just one line and
    they're different lines. The heuristic must say yes."""
    assert is_dual_feed_from_line_sets([{"line-A"}, {"line-B"}]) is True


@pytest.mark.parametrize("sub_a,sub_b,expected", [
    ({"a", "b"}, {"c", "d"},   True),    # fully disjoint
    ({"a"},      {"a"},        False),   # identical singletons
    ({"a", "b"}, {"b"},        False),   # subset overlap
    ({"a", "b", "c"}, {"d", "e"}, True), # disjoint with cardinality difference
    (set(),      {"a"},        False),   # empty A
    ({"a"},      set(),        False),   # empty B
])
def test_parametrized_pairs(sub_a, sub_b, expected):
    assert is_dual_feed_from_line_sets([sub_a, sub_b]) is expected
