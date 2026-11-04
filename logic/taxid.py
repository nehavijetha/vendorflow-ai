"""Tax ID validity maths: no AI, no lookups. A failed check means "most likely a typo", so the
callers treat it as unresolved (PENDING), never as a reason to reject."""

# Valid EIN prefixes as published by the IRS ("How EINs are assigned and valid EIN prefixes",
# irs.gov, page last reviewed 09-Apr-2026), grouped as the page groups them. Prefixes that the page
# lists under more than one group (such as 33 and 85-88) appear once here. Cross-checked against
# python-stdnum's independent list: 83 prefixes, no differences.
EIN_PREFIXES_BY_GROUP = {
    "Andover": (10, 12),
    "Atlanta": (60, 67),
    "Austin": (50, 53),
    "Brookhaven": (1, 2, 3, 4, 5, 6, 11, 13, 14, 16, 21, 22, 23, 25, 34, 51, 52, 54, 55, 56, 57, 58, 59, 65),
    "Cincinnati": (30, 32, 35, 36, 37, 38, 61),
    "Fresno": (15, 24),
    "Kansas City": (40, 44),
    "Memphis": (94, 95),
    "Ogden": (80, 90),
    "Philadelphia": (33, 39, 41, 42, 43, 46, 48, 62, 63, 64, 66, 68, 71, 72, 73, 74, 75, 76, 77, 85, 86, 87,
                     88, 91, 92, 93, 98, 99),
    "Internet": (20, 26, 27, 33, 39, 41, 42, 45, 46, 47, 81, 82, 83, 84, 85, 86, 87, 88, 92, 93, 99),
    "Small Business Administration": (31,),
}
EIN_PREFIXES = frozenset(p for group in EIN_PREFIXES_BY_GROUP.values() for p in group)

_VAT_WEIGHTS = (8, 7, 6, 5, 4, 3, 2)


def ein_prefix_valid(ein):
    """True if the first two digits of an EIN (NN-NNNNNNN) are a prefix the IRS assigns."""
    digits = "".join(ch for ch in ein if ch.isdigit())
    return len(digits) == 9 and int(digits[:2]) in EIN_PREFIXES


def _vat_total(digits):
    return sum(w * int(d) for w, d in zip(_VAT_WEIGHTS, digits[:7]))


def uk_vat_variant(number):
    """Which HMRC check passes for a 9-digit VAT number: "mod 97", "mod 97 55", or None.

    Both algorithms are in real use: numbers issued since 2010 may use the 55 offset (the "9755"
    variant), older ones do not. A number is valid if either passes, in any number range.

    Known differences from python-stdnum, checked on 300,000 random numbers: stdnum also accepts a
    weighted remainder of 55 for numbers starting 100 or higher (not covered by the two variants
    specified here), and stdnum rejects the 9755 variant for numbers below 100 000 000, which this
    function accepts. HMRC's own specification was not available to settle either point.
    """
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) != 9:
        return None
    total, check = _vat_total(digits), int(digits[7:9])
    if (total + check) % 97 == 0:
        return "mod 97"
    if (total + check + 55) % 97 == 0:
        return "mod 97 55"
    return None


def luhn_valid(digits):
    """Standard Luhn check over a string of digits."""
    if not digits or not digits.isdigit():
        return False
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2 == 1:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def ca_bn_valid(number):
    """A Canadian Business Number is valid if its first nine digits pass Luhn. An RT0001-style
    program account suffix carries no check digit of its own."""
    digits = "".join(ch for ch in number if ch.isdigit())
    return len(digits) >= 9 and luhn_valid(digits[:9])
