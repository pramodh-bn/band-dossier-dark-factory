"""Check the Stage 2 build's candidates and selection against a corpus key.

For every company it replays the build's gathering offline -- the searches the
build makes, plus the pages a Nimble research run cites -- reads each page's
browser text (the mock's rendered view), derives candidates, and compares:

* the candidate values with the key's stage-1 expected set (every supported value),
* the selected value and status with the key's stage-2 expectation.

    python -m tests.validate_candidates --corpus <set> --challenge <dossier challenge dir>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import candidates, evidence, pages  # noqa: E402
from app.builds import queries  # noqa: E402

FIELDS = evidence.FIELDS


def load(corpus: pathlib.Path, challenge: pathlib.Path):
    """The corpus set through the mock web's loader, and its key."""
    sys.path.insert(0, str(challenge / "web"))
    from mockweb import corpus as mock_corpus  # pylint: disable=import-outside-toplevel
    from mockweb.search import rank  # pylint: disable=import-outside-toplevel

    return mock_corpus.load(corpus), json.loads((corpus / "key.json").read_text("utf-8")), rank


def gathered(loaded, key, rank, domain: str, epoch: int) -> list[str]:
    """The URLs a build reads: the priority prefix that the 80-unit budget affords."""
    from app import providers  # noqa: PLC0415 - the cost table
    from app.builds import page_priority  # noqa: PLC0415 - the shipped order

    found: list[str] = []
    snippets: dict[str, str] = {}
    for query in queries(domain):
        for page in rank(loaded.pages(epoch), query, 10, provider="nimble",
                         traits=loaded.traits):
            if page.url not in found:
                found.append(page.url)
            snippets.setdefault(page.url, page.snippet)
    spent = len(queries(domain)) + providers.RESEARCH_COST
    cited = [claim["url"] for claim in
             loaded.companies[domain].research.get("nimble", {}).get(str(epoch), [])
             if claim["url"] not in found]
    slots = max((80 - spent) // providers.RENDER_COST, 0)
    return page_priority(found + cited, snippets, cited, domain)[:slots]


def expected(entry: dict, stage: str, field: str, epoch: int = 0):
    """The key's expected value (or list of allowed values) for one field."""
    block = (entry.get("expected") or {}).get(stage, {}).get(str(epoch), {})
    return block.get(field, "absent")


def report(corpus: pathlib.Path, challenge: pathlib.Path, epoch: int = 0,
           all_companies: bool = False) -> int:
    """Print every disagreement between the build's outcome and the key."""
    loaded, key, rank = load(corpus, challenge)
    problems = 0
    for domain in sorted(loaded.companies):
        entry = key.get(domain, {})
        plain = "expected" in entry and "plain" in entry.get("tags", ())
        if not plain and not all_companies:
            continue
        rows = {name: [] for name in FIELDS}
        for url in gathered(loaded, key, rank, domain, epoch):
            page = loaded.page(epoch, url)
            if page is None:
                continue
            meta = pages.metadata(page.initial, url, domain)
            for name, pairs in evidence.page_values(page.initial, domain).items():
                for value, excerpt in pairs:
                    rows[name].append({"value": value, "url": url, "excerpt": excerpt,
                                       "provider": "nimble", "kind": meta["kind"],
                                       "published_at": meta["published_at"],
                                       "retrieved_at": None, "selected": False})
        for name in FIELDS:
            field = candidates.field(rows[name])
            want_stage1 = expected(entry, "1", name, epoch)
            want_stage2 = expected(entry, "2", name, epoch)
            found = {c["value"] for c in field["candidates"]}
            if plain and want_stage1 != "absent":
                allowed = set(want_stage1) if isinstance(want_stage1, list) else {want_stage1}
                allowed = {value for value in allowed if value is not None}
                if found != allowed:
                    print(f"VALUES {domain} {name}: {sorted(map(repr, found))} != "
                          f"{sorted(map(repr, allowed))}")
                    problems += 1
            if plain and want_stage2 != "absent":
                allowed2 = set(want_stage2) if isinstance(want_stage2, list) else {want_stage2}
                if field["value"] not in allowed2:
                    print(f"SELECT {domain} {name}: {field['value']!r} not in "
                          f"{sorted(map(repr, allowed2))}")
                    problems += 1
            print(f"  {domain:26s} {name:16s} {field['status']:9s} "
                  f"{field['value']!r:32s} candidates={len(field['candidates'])}")
    return 1 if problems else 0


def main() -> int:
    """Parse arguments and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--epoch", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="print every company, not just plain")
    args = parser.parse_args()
    return report(pathlib.Path(args.corpus), pathlib.Path(args.challenge), args.epoch, args.all)


if __name__ == "__main__":
    raise SystemExit(main())
