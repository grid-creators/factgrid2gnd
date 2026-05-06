#!/usr/bin/env bash
# Download the latest FactGrid dump, extract person items, and rebuild factgrid.db.
#
# Steps:
#   1. Find the most recent YYYY-MM-DD.json.gz at https://database.factgrid.de/dumps/
#   2. Download it to data/dump.json.gz
#   3. Run extract_persons_from_dump.py to produce data/subset_P2_Q7.json
#   4. Run build_factgrid_db.py against that subset to rebuild factgrid.db

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"
DUMPS_URL="https://database.factgrid.de/dumps/"
DUMP_FILE="$DATA_DIR/dump.json.gz"
SUBSET_FILE="$DATA_DIR/subset_P2_Q7.json"
LABELS_FILE="$DATA_DIR/subset_referenced_labels.json"

PYTHON="${PYTHON:-python3}"

mkdir -p "$DATA_DIR"

echo "==> 1/4 Fetching dump index from $DUMPS_URL"
LATEST=$(curl -sSf "$DUMPS_URL" \
    | grep -oE 'href="[0-9]{4}-[0-9]{2}-[0-9]{2}\.json\.gz"' \
    | sed 's/^href="//; s/"$//' \
    | sort -u \
    | tail -n 1)

if [ -z "$LATEST" ]; then
    echo "Error: no dump file matching YYYY-MM-DD.json.gz found at $DUMPS_URL" >&2
    exit 1
fi
echo "    Latest dump: $LATEST"

echo "==> 2/4 Downloading $LATEST"
curl -fL --progress-bar \
    --retry 5 --retry-delay 5 --retry-all-errors \
    --speed-time 30 --speed-limit 1024 \
    -o "$DUMP_FILE.tmp" "$DUMPS_URL$LATEST"

EXPECTED=$(curl -sfI "$DUMPS_URL$LATEST" | awk 'tolower($1)=="content-length:" {print $2+0}' | tr -d '\r')
ACTUAL=$(stat -c%s "$DUMP_FILE.tmp")
if [ -n "$EXPECTED" ] && [ "$ACTUAL" -ne "$EXPECTED" ]; then
    echo "Error: download size mismatch (expected $EXPECTED, got $ACTUAL)" >&2
    exit 1
fi
if ! gzip -t "$DUMP_FILE.tmp"; then
    echo "Error: downloaded gzip file is corrupt" >&2
    exit 1
fi
mv -f "$DUMP_FILE.tmp" "$DUMP_FILE"
echo "    Saved to $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

echo "==> 3/4 Extracting person items (P2 = Q7) + referenced label stubs"
"$PYTHON" "$SCRIPT_DIR/extract_persons_from_dump.py" "$DUMP_FILE"

echo "==> 4/4 Building factgrid.db from $SUBSET_FILE + $LABELS_FILE"
"$PYTHON" "$SCRIPT_DIR/build_factgrid_db.py" "$SUBSET_FILE" "$LABELS_FILE"

echo "Done."
