"""
Extract person items (P2 = Q7) from a FactGrid Wikibase JSON dump, plus a
labels-only companion file for items referenced by those persons.

Two passes over the dump:
  1. Stream the dump, write all person items (P2=Q7) to data/subset_P2_Q7.json
     and collect all QIDs referenced through claim properties relevant for
     conversion (family/given names, places, occupations) plus their
     qualifier entity-id values.
  2. Stream the dump again, write label-only stubs ({id, type, labels}) for
     every referenced QID that is NOT itself a person, into
     data/subset_referenced_labels.json. These stubs let
     build_factgrid_db.py populate the `labels` table for non-person items
     (e.g. family-name and given-name items used by P247/P248), without
     bloating `entities` with full claims for those items.

Usage:
    python extract_persons_from_dump.py [<dump-file>]

If no argument is given, DEFAULT_DUMP is used.
"""

import gzip
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

DEFAULT_DUMP = os.path.join(DATA_DIR, "2026-04-16.json.gz")
OUTPUT_PERSONS = os.path.join(DATA_DIR, "subset_P2_Q7.json")
OUTPUT_LABELS = os.path.join(DATA_DIR, "subset_referenced_labels.json")

PERSON_CLASS = "Q7"

# Properties whose values (and qualifier entity-ids) need label resolution
# during MARC conversion. Keep in sync with utils.collect_referenced_entity_ids.
RELEVANT_PROPS = {"P247", "P248", "P82", "P168", "P83", "P1372", "P165"}


def is_person(item):
    """True if the item has a P2 claim pointing to Q7."""
    if item.get("type") != "item":
        return False
    for claim in item.get("claims", {}).get("P2", []):
        mainsnak = claim.get("mainsnak", {})
        if mainsnak.get("snaktype") != "value":
            continue
        value = mainsnak.get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and value.get("id") == PERSON_CLASS:
            return True
    return False


def collect_referenced(item):
    """QIDs referenced by RELEVANT_PROPS mainsnaks and any qualifier entity-id."""
    ids = set()
    for prop_id, statements in item.get("claims", {}).items():
        if prop_id not in RELEVANT_PROPS:
            continue
        for stmt in statements:
            snak = stmt.get("mainsnak", {})
            dv = snak.get("datavalue", {})
            if dv.get("type") == "wikibase-entityid":
                val = dv.get("value", {})
                if "id" in val:
                    ids.add(val["id"])
            for qual_snaks in stmt.get("qualifiers", {}).values():
                for qs in qual_snaks:
                    qdv = qs.get("datavalue", {})
                    if qdv.get("type") == "wikibase-entityid":
                        qval = qdv.get("value", {})
                        if "id" in qval:
                            ids.add(qval["id"])
    return ids


def stream_entities(path):
    """Yield one parsed entity per line from a Wikibase JSON dump.

    Dumps are formatted as a JSON array with one entity per line plus a
    leading '[' and trailing ']' on their own lines. Each entity line ends
    with ',' except the last.
    """
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line in ("[", "]"):
                continue
            if line.endswith(","):
                line = line[:-1]
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  ! skip malformed line: {exc}", flush=True)


def main():
    dump_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DUMP
    if not os.path.exists(dump_path):
        sys.exit(f"Dump not found: {dump_path}")

    print(f"Reading  {dump_path}")
    print(f"Writing  {OUTPUT_PERSONS}")
    print(f"Writing  {OUTPUT_LABELS}")

    # --- Pass 1: write persons, collect referenced QIDs ---
    start = time.time()
    scanned = 0
    matched = 0
    person_qids = set()
    referenced_qids = set()

    print("\n==> Pass 1/2: extracting persons + collecting references")
    with open(OUTPUT_PERSONS, "w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        for item in stream_entities(dump_path):
            scanned += 1
            if scanned % 50000 == 0:
                print(
                    f"  ... scanned {scanned}, persons: {matched}, "
                    f"refs: {len(referenced_qids)}",
                    flush=True,
                )
            if not is_person(item):
                continue
            qid = item.get("id", "")
            if qid:
                person_qids.add(qid)
            referenced_qids.update(collect_referenced(item))
            if not first:
                out.write(",\n")
            json.dump(item, out, ensure_ascii=False)
            first = False
            matched += 1
        out.write("\n]\n")

    pass1_elapsed = time.time() - start
    needed = referenced_qids - person_qids
    print(
        f"  Pass 1 done in {pass1_elapsed:.1f}s. "
        f"persons: {matched}, distinct refs needed: {len(needed)}"
    )

    # --- Pass 2: write label stubs for referenced non-person QIDs ---
    print("\n==> Pass 2/2: extracting labels for referenced items")
    pass2_start = time.time()
    pass2_scanned = 0
    written_labels = 0

    with open(OUTPUT_LABELS, "w", encoding="utf-8") as out:
        out.write("[\n")
        first = True
        for item in stream_entities(dump_path):
            pass2_scanned += 1
            if pass2_scanned % 50000 == 0:
                print(
                    f"  ... scanned {pass2_scanned}, label stubs: {written_labels}",
                    flush=True,
                )
            qid = item.get("id", "")
            if not qid or qid not in needed:
                continue
            stub = {
                "id": qid,
                "type": item.get("type", "item"),
                "labels": item.get("labels", {}),
            }
            if not first:
                out.write(",\n")
            json.dump(stub, out, ensure_ascii=False)
            first = False
            written_labels += 1
        out.write("\n]\n")

    pass2_elapsed = time.time() - pass2_start
    total_elapsed = time.time() - start
    print(
        f"  Pass 2 done in {pass2_elapsed:.1f}s. label stubs written: {written_labels}"
    )
    missing = len(needed) - written_labels
    if missing > 0:
        print(f"  Note: {missing} referenced QIDs not found in dump (skipped)")

    print(f"\nDone in {total_elapsed:.1f}s.")
    print(f"  Persons:        {OUTPUT_PERSONS} ({matched} items)")
    print(f"  Label stubs:    {OUTPUT_LABELS} ({written_labels} items)")


if __name__ == "__main__":
    main()
