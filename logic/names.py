"""Normalisation helpers. Originals are kept by callers for audit evidence."""
import re
import unicodedata

from rapidfuzz import fuzz

_COUNTRY_ALIASES = {
    "us": "US", "usa": "US", "united states": "US", "united states of america": "US",
    "uk": "UK", "gb": "UK", "united kingdom": "UK", "great britain": "UK",
    "ca": "CA", "canada": "CA",
}

# Legal suffix variants -> canonical form, per country.
_SUFFIXES = {
    "US": {"incorporated": "inc", "inc": "inc", "corporation": "corp", "corp": "corp",
           "company": "co", "co": "co", "limited": "ltd", "ltd": "ltd",
           "llc": "llc", "lp": "lp", "llp": "llp"},
    "UK": {"limited": "ltd", "ltd": "ltd", "plc": "plc", "llp": "llp",
           "incorporated": "inc", "inc": "inc"},
    "CA": {"incorporated": "inc", "inc": "inc", "corporation": "corp", "corp": "corp",
           "limited": "ltd", "ltd": "ltd", "ltee": "ltd", "limitee": "ltd"},
}
_ALL_SUFFIXES = {k: v for table in _SUFFIXES.values() for k, v in table.items()}


def normalize_country(country):
    return _COUNTRY_ALIASES.get((country or "").strip().lower())


def normalize_tax_id(tax_id):
    return re.sub(r"[\s\-]", "", tax_id or "").upper()


def normalize_account(account):
    return re.sub(r"[^0-9A-Za-z]", "", account or "").upper()


def squash(text):
    """Lowercase ASCII letters and digits only, so spacing, hyphens and accents cannot hide a match."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in ascii_text.lower() if ch.isalnum())


def split_name(name, country=None):
    """Return (core, suffixes): the name without its trailing legal-form words, and those
    words in canonical form. Only trailing suffix words are touched, and at least one
    word always stays in the core ("Limited Brands" keeps "limited")."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace(".", "").replace("'", "")
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    table = _SUFFIXES.get(normalize_country(country) or country, _ALL_SUFFIXES)
    end = len(tokens)
    while end > 1 and tokens[end - 1] in table:
        end -= 1
    return " ".join(tokens[:end]), [table[t] for t in tokens[end:]]


def normalize_name(name, country=None):
    core, suffixes = split_name(name, country)
    return " ".join([core, *suffixes]).strip()


def name_similarity(a, b, country=None):
    """Similarity of the core names (legal-form suffixes excluded), 0-100."""
    core_a, _ = split_name(a, country)
    core_b, _ = split_name(b, country)
    if not core_a or not core_b:
        return 0.0
    return fuzz.token_sort_ratio(core_a, core_b)


def entity_type_conflict(a, b, country=None):
    """True when both names carry a legal form and the forms differ (e.g. LLC vs Inc)."""
    _, suffix_a = split_name(a, country)
    _, suffix_b = split_name(b, country)
    return bool(suffix_a and suffix_b and suffix_a[-1] != suffix_b[-1])
