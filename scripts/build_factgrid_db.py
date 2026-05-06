"""
Build a SQLite database from the FactGrid JSON dump for fast local lookups.

Streams the large JSON file item by item, extracts key data, and inserts
into a SQLite database with three tables:

  - entities:  qid (PK), type, data (full JSON for entity reconstruction)
  - labels:    qid, lang, label (for fast label resolution)
  - gnd_ids:   qid, gnd_id (P76 claims, for GND ID resolution)

Usage:
    python build_factgrid_db.py [<input.json> ...]

Input:  data/2026-04-16.json (~6.4GB) by default, or one-or-more paths passed
        on the CLI. Multiple inputs are streamed in order — typical refresh
        run passes both data/subset_P2_Q7.json (full person items) and
        data/subset_referenced_labels.json (label-only stubs for items
        referenced by P247/P248/P82/... so name/place labels resolve in
        offline mode).
Output: factgrid.db
"""

import json
import os
import sqlite3
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DATA_DIR = os.path.join(ROOT_DIR, "data")

DEFAULT_JSON_INPUT = os.path.join(DATA_DIR, "2026-04-16.json")
DB_OUTPUT = os.path.join(ROOT_DIR, "factgrid.db")

BATCH_SIZE = 5000


def stream_items(path):
    """Stream JSON array items one by one from a large file."""
    with open(path, "r", encoding="utf-8") as f:
        # Skip to opening '['
        ch = ""
        while ch != "[":
            ch = f.read(1)

        buf = ""
        brace_depth = 0
        in_string = False
        escape = False
        while True:
            ch = f.read(1)
            if not ch:
                break
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1
            if brace_depth > 0 or (ch == "}" and not in_string):
                buf += ch
            if brace_depth == 0 and buf:
                try:
                    item = json.loads(buf)
                except json.JSONDecodeError:
                    buf = ""
                    continue
                buf = ""
                yield item


def extract_labels(item):
    """Extract all language labels from an item."""
    labels = item.get("labels", {})
    for lang, entry in labels.items():
        yield lang, entry.get("value", "")


def extract_gnd_ids(item):
    """Extract GND IDs (P76) from an item's claims."""
    claims = item.get("claims", {})
    for stmt in claims.get("P76", []):
        snak = stmt.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        dv = snak.get("datavalue", {})
        if dv.get("type") == "string":
            yield dv.get("value", "")


def main():
    json_inputs = sys.argv[1:] if len(sys.argv) > 1 else [DEFAULT_JSON_INPUT]
    for path in json_inputs:
        if not os.path.exists(path):
            print(f"Error: Input file not found: {path}")
            return

    if os.path.exists(DB_OUTPUT):
        os.remove(DB_OUTPUT)
        print(f"Removed existing {DB_OUTPUT}")

    conn = sqlite3.connect(DB_OUTPUT)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute("""
        CREATE TABLE entities (
            qid TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE labels (
            qid TEXT NOT NULL,
            lang TEXT NOT NULL,
            label TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE gnd_ids (
            qid TEXT NOT NULL,
            gnd_id TEXT NOT NULL
        )
    """)
    conn.commit()

    start = time.time()
    total = 0
    entity_batch = []
    label_batch = []
    gnd_batch = []

    for json_input in json_inputs:
        print(f"Streaming {json_input} ...")
        for item in stream_items(json_input):
            qid = item.get("id", "")
            item_type = item.get("type", "")
            if not qid:
                continue

            total += 1
            entity_batch.append((qid, item_type, json.dumps(item, ensure_ascii=False)))

            for lang, label in extract_labels(item):
                if label:
                    label_batch.append((qid, lang, label))

            for gnd_id in extract_gnd_ids(item):
                if gnd_id:
                    gnd_batch.append((qid, gnd_id))

            if total % BATCH_SIZE == 0:
                conn.executemany("INSERT OR REPLACE INTO entities VALUES (?, ?, ?)", entity_batch)
                conn.executemany("INSERT INTO labels VALUES (?, ?, ?)", label_batch)
                conn.executemany("INSERT INTO gnd_ids VALUES (?, ?)", gnd_batch)
                conn.commit()
                entity_batch.clear()
                label_batch.clear()
                gnd_batch.clear()
                elapsed = time.time() - start
                print(f"  ... {total:,} items ({elapsed:.0f}s)", flush=True)

    # Flush remaining
    if entity_batch:
        conn.executemany("INSERT OR REPLACE INTO entities VALUES (?, ?, ?)", entity_batch)
        conn.executemany("INSERT INTO labels VALUES (?, ?, ?)", label_batch)
        conn.executemany("INSERT INTO gnd_ids VALUES (?, ?)", gnd_batch)
        conn.commit()

    print(f"\nBuilding indexes ...")
    conn.execute("CREATE INDEX idx_labels_qid_lang ON labels (qid, lang)")
    conn.execute("CREATE INDEX idx_gnd_ids_qid ON gnd_ids (qid)")
    conn.commit()
    conn.close()

    elapsed = time.time() - start
    size_mb = os.path.getsize(DB_OUTPUT) / (1024 * 1024)
    print(f"\nDone. {total:,} entities in {elapsed:.0f}s")
    print(f"Database: {DB_OUTPUT} ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
