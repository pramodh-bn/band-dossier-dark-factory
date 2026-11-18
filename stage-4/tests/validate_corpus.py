"""Check the Appendix A implementation against a corpus key.

The key's `supports` list is the organizer's own reading of Appendix A for the
set, and its `expected` values are what a stage-1 build may answer. Run this
against any corpus set that ships a `key.json`:

    python -m tests.validate_corpus --corpus <corpus dir> --challenge <dossier challenge dir>

It reports three things per company and field: a key support the engine does
not find (a false negative), a value the engine supports that the key does not
allow (a false positive), and any field with no support at all.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import evidence  # noqa: E402

FIELDS = evidence.FIELDS


def load(corpus: pathlib.Path, challenge: pathlib.Path):
    """The corpus set, read through the mock web's own loader, and its key."""
    sys.path.insert(0, str(challenge / "web"))
    from mockweb import corpus as mock_corpus  # pylint: disable=import-outside-toplevel

    loaded = mock_corpus.load(corpus)
    key = json.loads((corpus / "key.json").read_text(encoding="utf-8"))
    return loaded, key


def allowed(key_entry: dict, field: str, epoch: int = 0) -> set:
    """The values the key allows for a field at stage 1 in `epoch`; `{None}` means unknown."""
    expected = key_entry["expected"]["1"][str(epoch)][field]
    return set(expected) if isinstance(expected, list) else {expected}


def report(corpus: pathlib.Path, challenge: pathlib.Path, epoch: int = 0) -> int:
    """Print every disagreement between the engine and the key; return the exit code."""
    loaded, key = load(corpus, challenge)
    problems = 0
    for domain, company in sorted(loaded.companies.items()):
        found: dict[str, set] = {field: set() for field in FIELDS}
        for page in company.pages:
            if epoch not in page.epochs:
                continue
            for field, pairs in evidence.page_values(page.served, domain).items():
                found[field].update(value for value, _ in pairs)
        entry = key.get(domain, {})
        supports = [s for s in entry.get("supports", ()) if s["field"] in FIELDS]
        for support in supports:
            sentence = support["excerpt"]
            if epoch not in next(
                    (page.epochs for page in company.pages if page.url == support["url"]), ()):
                continue
            value = evidence.supported_value(sentence, support["field"], domain)
            if value != support["value"] and not evidence._differs(value, support["value"]):
                continue
            if value is None or evidence._differs(value, support["value"]):
                print(f"MISS {domain} {support['field']}: {sentence!r} -> {value!r}, "
                      f"key says {support['value']!r}")
                problems += 1
        if "expected" not in entry:
            continue
        for field in FIELDS:
            want = allowed(entry, field, epoch)
            if None in want:
                want = want - {None}
                if found[field]:
                    print(f"EXTRA {domain} {field}: {sorted(map(repr, found[field]))} "
                          f"but the key allows only null")
                    problems += 1
                continue
            extra = {value for value in found[field] if value not in want}
            if extra:
                print(f"EXTRA {domain} {field}: {sorted(map(repr, extra))} "
                      f"not in {sorted(map(repr, want))}")
                problems += 1
            missing = want - found[field]
            if missing:
                print(f"ABSENT {domain} {field}: {sorted(map(repr, missing))} not supported "
                      f"by any page's prose (found {sorted(map(repr, found[field]))})")
                problems += 1
    return 1 if problems else 0


def main() -> int:
    """Parse arguments and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--challenge", required=True,
                        help="the dossier challenge folder (for the mock web's loader)")
    parser.add_argument("--epoch", type=int, default=0)
    args = parser.parse_args()
    return report(pathlib.Path(args.corpus), pathlib.Path(args.challenge), args.epoch)


if __name__ == "__main__":
    raise SystemExit(main())
