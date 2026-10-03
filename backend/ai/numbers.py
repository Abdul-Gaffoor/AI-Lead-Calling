"""Reading spoken quantities as figures (MVP section 37).

A customer saying their bill does not say "6000". They say "ఆరు వేలు", or
"six thousand", or "6 వేలు", or "₹7,500". MVP section 37 names those three
forms specifically and requires all of them to be understood.

Nothing downstream can do this for us. Scoring compares `monthly_bill`
against a threshold with `float(...)` and returns False when that raises,
so an unparsed value does not fail loudly — the customer simply loses the
points, and a HOT lead is filed as WARM with no error anywhere. The solar
engine has the same shape of problem: a bill it cannot read is a sizing it
cannot do.

So the orchestrator normalises numeric fields before they are stored, and
this module is where that reading happens. It is deliberately conservative:
it recognises the forms people actually use for money, units, area and
load, and returns None for anything else rather than guessing. A wrong
number is worse than a missing one — a missing one can be asked again.
"""

import re
import unicodedata

#: Telugu digits are a separate Unicode block from ASCII ones.
_TELUGU_DIGITS = str.maketrans("౦౧౨౩౪౫౬౭౮౯", "0123456789")

#: Units of one, in Telugu and English. Written out because transliteration
#: varies by district and this is matched against what the STT returns.
_UNITS: dict[str, int] = {
    "సున్నా": 0,
    "ఒకటి": 1, "ఒక": 1, "one": 1,
    "రెండు": 2, "రెడు": 2, "two": 2,
    "మూడు": 3, "మూడ": 3, "three": 3,
    "నాలుగు": 4, "నాలగు": 4, "four": 4,
    "ఐదు": 5, "అయిదు": 5, "five": 5,
    "ఆరు": 6, "six": 6,
    "ఏడు": 7, "seven": 7,
    "ఎనిమిది": 8, "eight": 8,
    "తొమ్మిది": 9, "nine": 9,
    "పది": 10, "ten": 10,
    "పదకొండు": 11, "eleven": 11,
    "పన్నెండు": 12, "twelve": 12,
    "పదమూడు": 13, "thirteen": 13,
    "పద్నాలుగు": 14, "fourteen": 14,
    "పదిహేను": 15, "fifteen": 15,
    "పదహారు": 16, "sixteen": 16,
    "పదిహేడు": 17, "seventeen": 17,
    "పద్దెనిమిది": 18, "eighteen": 18,
    "పందొమ్మిది": 19, "nineteen": 19,
    "ఇరవై": 20, "twenty": 20,
    "ముప్పై": 30, "thirty": 30,
    "నలభై": 40, "forty": 40,
    "యాభై": 50, "ఏభై": 50, "fifty": 50,
    "అరవై": 60, "sixty": 60,
    "డెబ్బై": 70, "seventy": 70,
    "ఎనభై": 80, "eighty": 80,
    "తొంభై": 90, "ninety": 90,
}

#: Multipliers, smallest first. Indian English counts in lakhs and crores and
#: so does Telugu, so both are first-class here rather than an afterthought.
_SCALES: dict[str, int] = {
    "వంద": 100, "వందల": 100, "వందలు": 100, "hundred": 100,
    "వెయ్యి": 1_000, "వేయి": 1_000, "వేల": 1_000, "వేలు": 1_000,
    "thousand": 1_000, "k": 1_000,
    "లక్ష": 100_000, "లక్షల": 100_000, "లక్షలు": 100_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "కోటి": 10_000_000, "కోట్ల": 10_000_000, "కోట్లు": 10_000_000,
    "crore": 10_000_000, "crores": 10_000_000,
}

#: Dropped before parsing: currency, separators and the words people wrap a
#: figure in. "రూ" and "rupees" carry no magnitude.
_NOISE = re.compile(
    r"(?:₹|rs\.?|inr|rupees?|రూపాయలు|రూపాయి|రూ\.?|per\s+month|monthly|approximately"
    r"|about|around|నెలకి|నెలకు|సుమారు|దాదాపు|గా|అవుతుంది|వస్తుంది|ఉంటుంది)",
    re.IGNORECASE,
)

#: Measurement suffixes. Recognised so "5 kW" reads as 5, but kept separate
#: from the scales above because they do not multiply the figure.
_MEASURES = re.compile(
    r"(?:kwp?|kilo\s*watts?|kva|hp|horse\s*power|hps?|sq\.?\s*ft|square\s+feet|sqft"
    r"|కిలోవాట్లు|కిలోవాట్|వాట్లు|వాట్|హెచ్\s*పి|చదరపు\s*అడుగులు|అడుగులు"
    r"|యూనిట్లు|యూనిట్లకు|యూనిట్|units?|unit|acres?|ఎకరాలు|ఎకరం|గుంటలు|cents?)",
    re.IGNORECASE,
)

_NUMERIC = re.compile(r"\d+(?:\.\d+)?")


def _clean(text: str) -> str:
    """Lowercase, strip currency and measurement words, unify digits."""
    text = unicodedata.normalize("NFC", str(text)).translate(_TELUGU_DIGITS)
    text = _NOISE.sub(" ", text.lower())
    text = _MEASURES.sub(" ", text)
    # Thousands separators, but only between digits: "7,500" is one number,
    # while "6000, 7000" is two and must not become 60007000.
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _words_to_number(text: str) -> float | None:
    """Read a written-out quantity: "ఆరు వేలు", "two lakh fifty thousand"."""
    tokens = [t for t in re.split(r"[\s\-]+", text) if t]

    total = 0.0      # quantities already closed off by a scale word
    current = 0.0    # the quantity being built up before its scale word
    seen = False
    previous_was_figure = False

    for raw in tokens:
        # Punctuation ends a quantity: "6000, 7000" is two answers, not
        # thirteen thousand. Take the first and stop.
        token = raw.strip(".,:;!?")
        ends_quantity = token != raw
        if not token:
            continue

        if _NUMERIC.fullmatch(token):
            # Written-out units accumulate ("twenty five thousand"), but two
            # bare figures in a row are two separate answers.
            if previous_was_figure:
                break
            current += float(token)
            seen = True
            previous_was_figure = True
        elif token in _UNITS:
            current += _UNITS[token]
            seen = True
            previous_was_figure = False
        elif token in _SCALES:
            # A bare scale word means one of it: "వేలు" alone is 1000.
            total += (current or 1) * _SCALES[token]
            current = 0.0
            seen = True
            previous_was_figure = False
        elif token in ("and", "మరియు"):
            continue
        else:
            # An unrecognised word means we are no longer reading one
            # quantity. Stop rather than join unrelated numbers.
            break

        if ends_quantity:
            break

    return (total + current) if seen else None


def to_number(value) -> float | None:
    """Read `value` as a figure, or None when it cannot be read confidently.

    Handles digits, Telugu and English number words, Telugu digits, lakh and
    crore scales, currency symbols, thousands separators, and the "6k" form.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = _clean(value)
    if not text:
        return None

    # "6k", "1.5k" — a figure fused to its scale with no space.
    if match := re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-zA-Zఀ-౿]+)", text):
        digits, suffix = match.groups()
        if suffix in _SCALES:
            return float(digits) * _SCALES[suffix]

    if parsed := _words_to_number(text):
        return parsed

    # Last resort: the first plain figure in the text.
    if match := _NUMERIC.search(text):
        return float(match.group())
    return None


def to_int(value) -> int | None:
    """`to_number`, rounded, for quantities that are counted not measured."""
    number = to_number(value)
    return None if number is None else int(round(number))


#: Extracted fields holding a quantity. Everything else the LLM returns is
#: left exactly as it came back — only these are reinterpreted, so a free-text
#: answer is never silently mangled into a number.
NUMERIC_FIELDS = frozenset(
    {
        "monthly_bill",
        "monthly_units",
        "sanctioned_load",
        "contract_demand",
        "transformer_capacity",
        "maximum_demand",
        "roof_area",
        "available_area",
        "land_area",
        "pump_hp",
        "existing_capacity",
        "expected_capacity",
        "expansion_requirement",
        "installation_year",
    }
)


def normalise_extracted(extracted: dict) -> dict:
    """Return `extracted` with its quantity fields read as numbers.

    A value that cannot be read is left untouched rather than dropped: the
    raw answer is still worth showing a human on the review screen, and the
    next turn may clarify it.
    """
    if not extracted:
        return extracted

    result = dict(extracted)
    for field in NUMERIC_FIELDS & result.keys():
        value = result[field]
        if isinstance(value, bool) or value in (None, ""):
            continue
        if (number := to_number(value)) is not None:
            result[field] = int(number) if number.is_integer() else number
    return result
