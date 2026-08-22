"""Small, reviewed Amex partner catalogue.

This module deliberately does not scrape Amex at runtime.  A supplier is a
partner only when its normalized name is present here (or in the explicit alias
list), in the same market and category.  That conservative rule prevents an
ordinary travel supplier from acquiring an Amex badge through fuzzy matching.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Optional, Tuple


LAST_VERIFIED_AT = "2026-08-22"


@dataclass(frozen=True)
class AmexPartner:
    name: str
    aliases: Tuple[str, ...]
    category: str
    market: str
    program: str
    official_url: str
    last_verified_at: str = LAST_VERIFIED_AT

    def public(self) -> Dict[str, Any]:
        value = asdict(self)
        value["aliases"] = list(self.aliases)
        return value


# The URLs are the official Amex programme/listing pages used for the review.
# Do not add a record based only on a supplier's own marketing page.
PARTNERS: Tuple[AmexPartner, ...] = (
    AmexPartner(
        name="Hilton Tokyo Bay",
        aliases=("Hilton Tokyo Bay Hotel",),
        category="lodging",
        market="JP",
        program="Amex Travel Hotels",
        official_url=(
            "https://www.americanexpress.com/en-sg/travel/discover/"
            "property-results/c/27/dt/4/d/Tokyo%2CJapan"
        ),
    ),
    AmexPartner(
        name="SATS Premier Lounge Terminal 3",
        aliases=("SATS Premier Lounge T3", "SATS Premier Lounge - Terminal 3"),
        category="lounge",
        market="SG",
        program="Global Lounge Collection",
        official_url=(
            "https://www.americanexpress.com/en-sg/travel/lounges/"
            "the-platinum-card/SIN/sats-premier-lounge-terminal3-RL47nYNPjn/"
        ),
    ),
)


def normalize_name(value: str) -> str:
    """Normalize punctuation and spacing, but never perform fuzzy matching."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def match_partner(name: str, *, category: str, market: str) -> Optional[Dict[str, Any]]:
    """Return an exact/alias catalogue match in the requested category/market."""
    needle = normalize_name(name)
    category_key = category.casefold().strip()
    market_key = market.upper().strip()
    for partner in PARTNERS:
        if partner.category.casefold() != category_key or partner.market != market_key:
            continue
        accepted: Iterable[str] = (partner.name, *partner.aliases)
        if needle in {normalize_name(candidate) for candidate in accepted}:
            return partner.public()
    return None

