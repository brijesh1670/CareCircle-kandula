"""A small, curated, informational interaction check. It is NOT medical advice and
deliberately errs on the side of 'ask your pharmacist'."""
from __future__ import annotations

from itertools import combinations

_KNOWN: list[tuple[frozenset[str], str]] = [
    (frozenset({"warfarin", "aspirin"}), "Warfarin with aspirin can raise bleeding risk."),
    (frozenset({"warfarin", "ibuprofen"}), "Warfarin with ibuprofen can raise bleeding risk."),
    (frozenset({"lisinopril", "spironolactone"}), "Lisinopril with spironolactone can raise potassium levels."),
    (frozenset({"sildenafil", "nitroglycerin"}), "Sildenafil with nitroglycerin can cause a dangerous drop in blood pressure."),
]
_DISCLAIMER = " Informational only: please confirm with a pharmacist or doctor."


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def check_new_medication(name: str, existing: list[str]) -> list[str]:
    """Warnings for adding `name` next to the `existing` medication names."""
    new, warnings = _norm(name), []
    for other in existing:
        other_n = _norm(other)
        if other_n == new:
            warnings.append(f"{other} is already on the list (possible duplicate).")
            continue
        for pair, message in _KNOWN:
            a, b = sorted(pair)
            if (a in new and b in other_n) or (b in new and a in other_n):
                warnings.append(message + _DISCLAIMER)
    return warnings


def scan_all(names: list[str]) -> list[str]:
    """Every interaction across a whole medication list (deduplicated)."""
    found: list[str] = []
    for a, b in combinations(names, 2):
        for w in check_new_medication(a, [b]):
            if w not in found:
                found.append(w)
    return found
