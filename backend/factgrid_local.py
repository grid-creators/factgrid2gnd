"""
Local FactGrid data access via SQLite database (built from JSON dump).

Provides the same interface as the server-based functions in utils.py:
  - fetch_entity(qid)
  - resolve_labels(entity_ids, lang)
  - resolve_gnd_ids(entity_ids)
"""

import json
import os
import sqlite3

FACTGRID_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "factgrid.db")

_conn = None
_conn_ino = None


def _get_db():
    """Get a connection to the local FactGrid database.

    Der naechtliche Refresh ersetzt factgrid.db atomar (os.replace); die
    Verbindung wird daher neu geoeffnet, sobald sich der Datei-Inode aendert.
    Die alte Verbindung wird nicht explizit geschlossen — laufende Abfragen
    halten sie ueber ihre Cursor am Leben, danach raeumt der GC auf.
    """
    global _conn, _conn_ino
    db_path = os.path.normpath(FACTGRID_DB_PATH)
    try:
        ino = os.stat(db_path).st_ino
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Lokale FactGrid-Datenbank nicht gefunden: {db_path}\n"
            "Bitte zuerst scripts/build_factgrid_db.py ausfuehren."
        )
    if _conn is not None and ino == _conn_ino:
        return _conn
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn_ino = ino
    return _conn


def fetch_entity(qid):
    """Fetch entity data from local SQLite database."""
    conn = _get_db()
    row = conn.execute(
        "SELECT data FROM entities WHERE qid = ?", (qid,)
    ).fetchone()
    if not row:
        raise ValueError(f"Entity {qid} nicht in lokaler Datenbank gefunden")
    return json.loads(row["data"])


def resolve_labels(entity_ids, lang="de"):
    """Batch-resolve entity QIDs to labels from local database."""
    if not entity_ids:
        return {}

    conn = _get_db()
    ids = list(set(entity_ids))
    labels = {}

    for i in range(0, len(ids), 500):
        batch = ids[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT qid, label FROM labels WHERE lang = ? AND qid IN ({placeholders})",
            [lang] + batch,
        ).fetchall()
        for row in rows:
            labels[row["qid"]] = row["label"]

    # Fallback to English for missing labels
    missing = [qid for qid in ids if qid not in labels]
    if missing and lang != "en":
        for i in range(0, len(missing), 500):
            batch = missing[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT qid, label FROM labels WHERE lang = 'en' AND qid IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                if row["qid"] not in labels:
                    labels[row["qid"]] = row["label"]

    return labels


def resolve_gnd_ids(entity_ids):
    """Batch-resolve entity QIDs to GND IDs (P76) from local database."""
    if not entity_ids:
        return {}

    conn = _get_db()
    ids = list(set(entity_ids))
    gnd_ids = {}

    for i in range(0, len(ids), 500):
        batch = ids[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT qid, gnd_id FROM gnd_ids WHERE qid IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            qid = row["qid"]
            if qid not in gnd_ids:
                gnd_ids[qid] = []
            gnd_ids[qid].append(row["gnd_id"])

    return gnd_ids
