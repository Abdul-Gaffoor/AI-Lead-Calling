import pytest

from backend.leads.phone import normalize_indian_mobile


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("98765-43210", "+919876543210"),
        ("(98765) 43210", "+919876543210"),
        ("6000000000", "+916000000000"),
    ],
)
def test_valid_numbers(raw, expected):
    assert normalize_indian_mobile(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "12345",
        "5876543210",  # starts with 5
        "98765432101",  # 11 digits, no leading 0
        "9876543210x",  # letters
        "+929876543210",  # wrong country code
        "0000000000",
    ],
)
def test_invalid_numbers(raw):
    assert normalize_indian_mobile(raw) is None
