"""Remembered names for the Debts & Loans forms.

A person is identified exactly as before: (normalized name, normalized
contact). The Contact input is gone from the UI, but stored contacts and
identity links stay valid; here they act as the hidden disambiguator.

- `suggest()` powers autocomplete: names (starting with the typed text) from
  every borrowing and lending of this user, settled or not, one entry per
  identity, case/whitespace-insensitive.
- `resolve_person()` decides which identity a submitted name belongs to, never
  silently merging two different same-named people.
"""

from django.db.models.functions import Lower, Trim

from .models import Borrowing, Lending
from .settlements import clean_name, norm

SUGGESTION_LIMIT = 8
_SCAN_LIMIT = 300  # DB rows examined per side before in-Python de-duplication


class AmbiguousPerson(ValueError):
    pass


def _rows(user, prefix=None):
    """(name, contact, date, is_borrowing) rows for this user only, newest first."""
    out = []
    for model, name_f, date_f, flag in (
        (Borrowing, "lender_name", "date_borrowed", True),
        (Lending, "borrower_name", "date_lent", False),
    ):
        qs = model.objects.filter(user=user)
        if prefix:
            qs = qs.annotate(_n=Lower(Trim(name_f))).filter(_n__startswith=prefix.casefold())
        for name, contact, date in qs.order_by("-" + date_f, "-id").values_list(name_f, "contact", date_f)[:_SCAN_LIMIT]:
            out.append((name, contact, date, flag))
    out.sort(key=lambda r: r[2], reverse=True)
    return out


def identities(user, prefix=None) -> dict:
    """{(name_norm, contact_norm): {name, contact, sides}}; the display name and
    contact come from the most recent record of that identity."""
    found = {}
    for name, contact, _date, is_borrowing in _rows(user, prefix):
        key = (norm(name), norm(contact))
        entry = found.setdefault(key, {"name": clean_name(name), "contact": clean_name(contact), "sides": set()})
        entry["sides"].add("lender" if is_borrowing else "borrower")
    return found


def suggest(user, query, limit=SUGGESTION_LIMIT) -> list:
    prefix = norm(query)
    if not prefix:
        return []
    found = identities(user, prefix)
    by_name = {}
    for (name_key, _c), entry in found.items():
        by_name.setdefault(name_key, []).append(entry)
    results = []
    for name_key in sorted(by_name):
        entries = by_name[name_key]
        for entry in sorted(entries, key=lambda e: e["contact"].casefold()):
            label = entry["name"]
            if len(entries) > 1:  # same name, different people: show the stored contact to tell them apart
                label += f" · {entry['contact']}" if entry["contact"] else " · no contact"
            results.append({"name": entry["name"], "contact": entry["contact"], "label": label})
    return results[:limit]


def resolve_person(user, name, selected_contact="", *, side) -> tuple:
    """Return (display_name, contact) for a submitted name.

    1. A suggestion the user picked (name + its contact) wins if that identity exists.
    2. Otherwise typing a name that matches exactly one known identity (this side
       first, then the other) reuses that identity, so the person groups together.
    3. Several identities share the name -> AmbiguousPerson: the user must pick one
       from the suggestions instead of the app guessing.
    4. Unknown name -> a new person with no contact.
    """
    display = clean_name(name)
    if not display:
        raise AmbiguousPerson("Enter a name.")
    known = identities(user, norm(display))
    same_name = {k: v for k, v in known.items() if k[0] == norm(display)}
    selected = norm(selected_contact)
    if (norm(display), selected) in same_name:
        entry = same_name[(norm(display), selected)]
        return entry["name"], entry["contact"]
    if not same_name:
        return display, ""
    mine = {k: v for k, v in same_name.items() if side in v["sides"]} or same_name
    if len(mine) == 1:
        entry = next(iter(mine.values()))
        return entry["name"], entry["contact"]
    raise AmbiguousPerson(
        f"More than one person is named {display}. Pick the right one from the suggestions, "
        "or add something to the name (for example a surname) to tell them apart."
    )
