"""Tax ID maths and the checksum check. A failed checksum is unresolved (a likely typo), never a rejection."""
import random

import pytest

from db.seed import SEED_VENDORS
from logic.checks import check_tax_id_checksum, evaluate_submission
from logic.decision import decide
from logic.taxid import (EIN_PREFIXES, EIN_PREFIXES_BY_GROUP, ca_bn_valid, ein_prefix_valid, luhn_valid,
                         uk_vat_variant)

INVALID_PREFIXES = [0, 7, 8, 9, 17, 18, 19, 28, 29, 49, 69, 70, 78, 79, 89, 96, 97]
UK_CLASSIC, UK_9755 = "GB987654353", "GB987654493"
CA_VALID = "987654324RT0001"


def checksum(country, tax_id):
    return check_tax_id_checksum({"country": country, "tax_id": tax_id}, None, [])


# --- EIN prefixes ----------------------------------------------------------------------------------

def test_ein_prefix_list_has_the_83_prefixes_the_irs_publishes():
    assert len(EIN_PREFIXES) == 83
    assert sorted(set(range(100)) - EIN_PREFIXES) == INVALID_PREFIXES


def test_prefixes_listed_under_several_irs_groups_appear_once():
    assert 33 in EIN_PREFIXES_BY_GROUP["Philadelphia"] and 33 in EIN_PREFIXES_BY_GROUP["Internet"]
    assert sum(len(g) for g in EIN_PREFIXES_BY_GROUP.values()) > len(EIN_PREFIXES)


@pytest.mark.parametrize("ein,expected", [("12-3456789", True), ("98-7654321", True), ("31-0000000", True),
                                          ("07-1234567", False), ("00-1234567", False), ("49-1234567", False),
                                          ("12-345678", False), ("", False)])
def test_ein_prefix(ein, expected):
    assert ein_prefix_valid(ein) is expected


# --- UK VAT ------------------------------------------------------------------------------------------

def test_uk_vat_accepts_either_variant_and_names_which():
    assert uk_vat_variant(UK_CLASSIC) == "mod 97"
    assert uk_vat_variant(UK_9755) == "mod 97 55"


def test_uk_vat_rejects_a_mistyped_digit_and_wrong_lengths():
    for bad in ("GB987654352", "GB987654354", "GB887654353", "98765435", "GB9876543530"):
        assert uk_vat_variant(bad) is None


def test_uk_vat_ignores_spacing():
    assert uk_vat_variant("GB 987 6543 53") == "mod 97"


# --- Luhn / Canada --------------------------------------------------------------------------------------

def test_luhn_known_values():
    assert luhn_valid("79927398713") and not luhn_valid("79927398710")
    assert not luhn_valid("") and not luhn_valid("12a4")


@pytest.mark.parametrize("bn,expected", [("987654324", True), (CA_VALID, True), ("987654323", False),
                                         ("987654323RT0001", False), ("12345678", False)])
def test_canadian_bn(bn, expected):
    assert ca_bn_valid(bn) is expected


def test_the_program_account_suffix_is_not_checked():
    assert ca_bn_valid("987654324RT0099") and ca_bn_valid("987654324RT0001")


# --- the check ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("country,tax_id", [("United States", "12-3456789"), ("UK", UK_CLASSIC), ("UK", UK_9755),
                                            ("Canada", CA_VALID), ("Canada", "987654324")])
def test_valid_ids_pass(country, tax_id):
    assert checksum(country, tax_id)["result"] == "pass"


@pytest.mark.parametrize("country,tax_id,words", [
    ("United States", "07-1234567", "IRS"), ("UK", "GB987654352", "check digits"),
    ("Canada", "987654323RT0001", "check digit"),
])
def test_invalid_ids_are_unresolved_and_ask_for_a_correction(country, tax_id, words):
    finding = checksum(country, tax_id)
    assert finding["result"] == "fail" and words in finding["vendor_request"] and not finding["human_review"]
    assert tax_id not in finding["vendor_request"]


def test_a_checksum_failure_leads_to_pending_never_rejected(submission, extracted, vendors):
    submission["tax_id"] = "07-1234567"
    extracted["tax_id"] = "07-1234567"
    findings = evaluate_submission(submission, extracted, vendors)
    decision = decide(findings)
    assert decision["status"] == "PENDING" and "check_tax_id_checksum" in decision["unresolved"]
    assert not decision["blocking"]


def test_lowercase_input_is_accepted():
    assert checksum("UK", UK_CLASSIC.lower())["result"] == "pass"


@pytest.mark.parametrize("country,tax_id", [("United States", "123456789"), ("UK", "GB12345"), ("Canada", "12345"),
                                            ("Germany", "DE123456789"), ("UK", ""), ("UK", None)])
def test_unusable_input_is_skipped_so_the_format_check_owns_the_request(country, tax_id):
    assert checksum(country, tax_id)["result"] == "skipped"
    assert checksum(country, tax_id)["vendor_request"] is None


def test_every_seed_vendor_has_a_valid_tax_id():
    for vendor in SEED_VENDORS:
        assert checksum(vendor["country"], vendor["tax_id"])["result"] == "pass", vendor["legal_name"]


# --- cross-check against an independent reference (runs when python-stdnum is installed) ---------------

def test_matches_the_python_stdnum_reference_except_for_two_documented_cases():
    ein = pytest.importorskip("stdnum.us.ein")
    gb = pytest.importorskip("stdnum.gb.vat")
    ca = pytest.importorskip("stdnum.ca.bn")
    luhn = pytest.importorskip("stdnum.luhn")
    rng = random.Random(11)
    assert all(ein_prefix_valid(f"{p:02d}-1234567") == ein.is_valid(f"{p:02d}-1234567") for p in range(100))
    assert all(luhn_valid(str(n)) == luhn.is_valid(str(n)) for n in range(1000, 20000))
    for _ in range(5000):
        bn = "".join(rng.choice("0123456789") for _ in range(9))
        assert ca_bn_valid(bn) == ca.is_valid(bn)
    weights = (8, 7, 6, 5, 4, 3, 2, 10, 1)
    for _ in range(30000):
        number = "".join(rng.choice("0123456789") for _ in range(9))
        mine, theirs = uk_vat_variant(number), gb.is_valid(number)
        if (mine is not None) == theirs:
            continue
        remainder = sum(w * int(d) for w, d in zip(weights, number)) % 97
        high = int(number[:3]) >= 100
        stdnum_only = theirs and remainder == 55 and high  # stdnum accepts remainder 55 from 100 000 000
        ours_only = mine == "mod 97 55" and remainder == 42 and not high  # we accept 9755 in every range
        assert stdnum_only or ours_only, (number, mine, theirs, remainder)
