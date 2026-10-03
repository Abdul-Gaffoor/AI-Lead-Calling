"""Reading spoken quantities (MVP §37).

The corpus in `evaluation/scenarios/03-numbers-and-units.yaml` covers the
forms a customer uses. These cover the edges a corpus should not have to:
what the function does with a bool, a None, a figure it half-recognises.
"""

import pytest

from backend.ai.numbers import NUMERIC_FIELDS, normalise_extracted, to_int, to_number


@pytest.mark.parametrize(
    "spoken,expected",
    [
        # The three forms MVP §37 names, kept here as well as in the corpus
        # so that deleting the corpus cannot quietly remove them.
        ("ఆరు వేలు", 6000),
        ("6000", 6000),
        ("six thousand", 6000),
    ],
)
def test_the_three_forms_section_37_names(spoken, expected):
    assert to_number(spoken) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (6000, 6000.0),
        (6000.5, 6000.5),
        (None, None),
        (True, None),       # a bool is an answer, not a quantity
        (False, None),
        ("", None),
        ("   ", None),
        ("abc", None),
    ],
)
def test_non_string_and_empty_inputs(value, expected):
    assert to_number(value) == expected


def test_a_bare_scale_word_means_one_of_it():
    assert to_number("వేలు") == 1000
    assert to_number("lakh") == 100000


def test_figures_are_not_joined_across_a_boundary():
    """Two answers must not be added into a third that nobody said."""
    assert to_number("6000, 7000") == 6000
    assert to_number("6000 7000") == 6000
    assert to_number("ఆరు వేలు, ఏడు వేలు") == 6000


def test_to_int_rounds():
    assert to_int("1.5 lakh") == 150000
    assert to_int("2.4") == 2
    assert to_int("2.6") == 3
    assert to_int("nonsense") is None


def test_normalise_only_touches_quantity_fields():
    """
    A free-text answer must never be mangled into a number. Only the fields
    in NUMERIC_FIELDS are reinterpreted.
    """
    extracted = {
        "monthly_bill": "ఆరు వేలు",
        "location": "Miyapur 500049",      # has digits, but is not a quantity
        "installation_timeline": "30_days",
        "property_owned": True,
        "remarks": "customer said 6000 but was unsure",
    }

    result = normalise_extracted(extracted)

    assert result["monthly_bill"] == 6000
    assert result["location"] == "Miyapur 500049"
    assert result["installation_timeline"] == "30_days"
    assert result["property_owned"] is True
    assert result["remarks"] == "customer said 6000 but was unsure"


def test_an_unreadable_quantity_is_left_alone_not_dropped():
    """
    The raw answer is still worth showing a reviewer, and the next turn may
    clarify it. Dropping it would lose both.
    """
    result = normalise_extracted({"monthly_bill": "ఎంతో గుర్తు లేదు"})
    assert result["monthly_bill"] == "ఎంతో గుర్తు లేదు"


def test_booleans_in_quantity_fields_survive():
    """`roof_available: true` must not become 1."""
    result = normalise_extracted({"roof_area": True})
    assert result["roof_area"] is True


def test_whole_numbers_do_not_become_floats():
    """A bill of 6000 should read back as 6000, not 6000.0, in the payload."""
    result = normalise_extracted({"monthly_bill": "six thousand"})
    assert result["monthly_bill"] == 6000
    assert isinstance(result["monthly_bill"], int)


def test_decimals_are_kept():
    result = normalise_extracted({"land_area": "2.5 acres"})
    assert result["land_area"] == 2.5


def test_empty_extraction_is_returned_unchanged():
    assert normalise_extracted({}) == {}
    assert normalise_extracted(None) is None


def test_the_scoring_fields_are_covered():
    """
    The whole point is that scoring can read these. If a field the default
    rules compare numerically is missing from NUMERIC_FIELDS, a spoken
    answer silently scores zero.
    """
    from backend.scoring.defaults import DEFAULT_RULES

    numeric_ops = {"gte", "lte"}
    compared = {
        rule["field"]
        for rules in DEFAULT_RULES.values()
        for rule in rules
        if rule.get("op") in numeric_ops
    }

    assert compared <= NUMERIC_FIELDS, (
        "scoring compares these numerically but they are not normalised: "
        f"{sorted(compared - NUMERIC_FIELDS)}"
    )
