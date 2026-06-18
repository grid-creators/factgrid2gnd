"""
Utility functions for FactGrid entity data extraction and formatting.
"""

import os
import sqlite3
import requests
from datetime import datetime
from lxml import etree

SPARQL_ENDPOINT = "https://database.factgrid.de/sparql"
ENTITY_API = "https://database.factgrid.de/entity/{qid}.json"

# Path to local GND SQLite database (built by scripts/build_gnd_db.py)
GND_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "gnd_persons.db")
# Path to local GND Sachbegriffe database (built by scripts/build_gnd_sachbegriff_db.py)
GND_SACHBEGRIFF_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "gnd_sachbegriffe.db")
# Path to lobid cache database (auto-created, caches API results for non-person entities)
LOBID_CACHE_PATH = os.path.join(os.path.dirname(__file__), "lobid_cache.db")

# Module-level DB connections (lazy init)
_gnd_db_conn = None
_gnd_sachbegriff_conn = None
_lobid_cache_conn = None


def _get_gnd_db():
    """Get a connection to the local GND database, or None if not available."""
    global _gnd_db_conn
    if _gnd_db_conn is not None:
        return _gnd_db_conn
    db_path = os.path.normpath(GND_DB_PATH)
    if os.path.exists(db_path):
        _gnd_db_conn = sqlite3.connect(db_path, check_same_thread=False)
        _gnd_db_conn.row_factory = sqlite3.Row
        return _gnd_db_conn
    return None


def _get_gnd_sachbegriff_db():
    """Get a connection to the local GND Sachbegriffe database, or None if not available."""
    global _gnd_sachbegriff_conn
    if _gnd_sachbegriff_conn is not None:
        return _gnd_sachbegriff_conn
    db_path = os.path.normpath(GND_SACHBEGRIFF_DB_PATH)
    if os.path.exists(db_path):
        _gnd_sachbegriff_conn = sqlite3.connect(db_path, check_same_thread=False)
        _gnd_sachbegriff_conn.row_factory = sqlite3.Row
        return _gnd_sachbegriff_conn
    return None


def _get_lobid_cache():
    """Get a connection to the lobid cache database, creating it if needed."""
    global _lobid_cache_conn
    if _lobid_cache_conn is not None:
        return _lobid_cache_conn
    db_path = os.path.normpath(LOBID_CACHE_PATH)
    _lobid_cache_conn = sqlite3.connect(db_path, check_same_thread=False)
    _lobid_cache_conn.row_factory = sqlite3.Row
    _lobid_cache_conn.execute("""
        CREATE TABLE IF NOT EXISTS lobid_cache (
            gnd_id TEXT PRIMARY KEY,
            preferred_name TEXT NOT NULL DEFAULT ''
        )
    """)
    _lobid_cache_conn.commit()
    return _lobid_cache_conn


def _clean_marc_name(name):
    """Remove MARC sort control characters (&#152;/&#156; or \x98/\x9c) from names."""
    if not name:
        return name
    import re
    name = re.sub(r'&#15[26];', '', name)
    name = name.replace('\x98', '').replace('\x9c', '')
    return name.strip()


def lookup_gnd_preferred_name(gnd_id):
    """Look up the preferred name for a GND ID from the local database.

    Returns the preferred name string, or empty string if not found.
    """
    conn = _get_gnd_db()
    if conn is None:
        return ""
    row = conn.execute(
        "SELECT preferred_name FROM gnd_records WHERE gnd_id = ?", (gnd_id,)
    ).fetchone()
    return _clean_marc_name(row["preferred_name"]) if row else ""


def lookup_gnd_country_code(gnd_id):
    """Look up the country code for a GND ID from the local database.

    Returns the country code string (e.g. 'XA-DE'), or empty string if not found.
    """
    conn = _get_gnd_db()
    if conn is None:
        return ""
    row = conn.execute(
        "SELECT country_code FROM gnd_records WHERE gnd_id = ?", (gnd_id,)
    ).fetchone()
    return row["country_code"] if row else ""


def lookup_gnd_batch(gnd_ids):
    """Batch lookup of GND records from the local database.

    Returns dict: {gnd_id: {"preferred_name": ..., "country_code": ...}}
    """
    conn = _get_gnd_db()
    if conn is None:
        return {}
    result = {}
    # SQLite has a limit on variables, process in batches of 500
    gnd_list = list(set(gnd_ids))
    for i in range(0, len(gnd_list), 500):
        batch = gnd_list[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT gnd_id, preferred_name, country_code FROM gnd_records WHERE gnd_id IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            result[row["gnd_id"]] = {
                "preferred_name": _clean_marc_name(row["preferred_name"] or ""),
                "country_code": row["country_code"] or "",
            }
    return result

SPARQL_HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "FactGrid2GND/1.0",
    "Content-Type": "application/x-www-form-urlencoded",
}


def fetch_entity(qid, source="server"):
    """Fetch a FactGrid entity via the Wikibase REST API or local database."""
    if source == "local":
        from factgrid_local import fetch_entity as _local_fetch
        return _local_fetch(qid)
    url = ENTITY_API.format(qid=qid)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    entities = data.get("entities", {})
    if qid in entities:
        return entities[qid]
    raise ValueError(f"Entity {qid} not found in response")


def resolve_labels(entity_ids, lang="de", source="server"):
    """Batch-resolve entity QIDs to their labels via SPARQL or local database.

    Returns dict: {QID: label_string}
    """
    if not entity_ids:
        return {}

    if source == "local":
        from factgrid_local import resolve_labels as _local_resolve
        return _local_resolve(entity_ids, lang)

    # Deduplicate
    ids = list(set(entity_ids))
    labels = {}

    # Process in batches of 50
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        values = " ".join(f"wd:{qid}" for qid in batch)
        query = f"""
        PREFIX wd: <https://database.factgrid.de/entity/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?item ?label WHERE {{
          VALUES ?item {{ {values} }}
          ?item rdfs:label ?label.
          FILTER(LANG(?label) = '{lang}')
        }}
        """
        r = requests.post(
            SPARQL_ENDPOINT,
            data={"query": query},
            headers=SPARQL_HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        for b in r.json()["results"]["bindings"]:
            qid = b["item"]["value"].split("/")[-1]
            labels[qid] = b["label"]["value"]

    # Fallback to English for missing labels
    missing = [qid for qid in ids if qid not in labels]
    if missing and lang != "en":
        en_labels = resolve_labels(missing, lang="en", source=source)
        for qid, label in en_labels.items():
            if qid not in labels:
                labels[qid] = label

    return labels


def resolve_gnd_ids(entity_ids, source="server"):
    """Batch-resolve entity QIDs to their GND IDs (P76) via SPARQL or local database.

    Returns dict: {QID: [list of gnd_id strings]}
    If an entity has multiple GND IDs, all are returned for warning purposes.
    """
    if not entity_ids:
        return {}

    if source == "local":
        from factgrid_local import resolve_gnd_ids as _local_resolve
        return _local_resolve(entity_ids)

    ids = list(set(entity_ids))
    gnd_ids = {}

    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        values = " ".join(f"wd:{qid}" for qid in batch)
        query = f"""
        PREFIX wd: <https://database.factgrid.de/entity/>
        PREFIX wdt: <https://database.factgrid.de/prop/direct/>
        SELECT ?item ?gndId WHERE {{
          VALUES ?item {{ {values} }}
          ?item wdt:P76 ?gndId.
        }}
        """
        r = requests.post(
            SPARQL_ENDPOINT,
            data={"query": query},
            headers=SPARQL_HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        for b in r.json()["results"]["bindings"]:
            qid = b["item"]["value"].split("/")[-1]
            gnd_id = b["gndId"]["value"]
            if qid not in gnd_ids:
                gnd_ids[qid] = []
            gnd_ids[qid].append(gnd_id)

    return gnd_ids


def resolve_gnd_preferred_names(gnd_ids_map):
    """Resolve GND IDs to their preferred names.

    Uses the local GND database first (fast), falls back to lobid.org API
    for any IDs not found locally.

    Args:
        gnd_ids_map: dict {QID: [gnd_id, ...]} from resolve_gnd_ids()

    Returns dict: {gnd_id: preferred_name_string}
    """
    result = {}
    # Collect all unique GND IDs
    all_gnd_ids = set()
    for gnd_list in gnd_ids_map.values():
        all_gnd_ids.update(gnd_list)

    if not all_gnd_ids:
        return result

    # Step 1: Try local GND Sachbegriffe database (batch lookup)
    sachbegriff_conn = _get_gnd_sachbegriff_db()
    if sachbegriff_conn:
        sachbegriff_list = list(all_gnd_ids)
        for i in range(0, len(sachbegriff_list), 500):
            batch = sachbegriff_list[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = sachbegriff_conn.execute(
                f"SELECT gnd_id, preferred_name FROM gnd_records WHERE gnd_id IN ({placeholders})",
                batch,
            ).fetchall()
            for row in rows:
                if row["preferred_name"]:
                    result[row["gnd_id"]] = row["preferred_name"]

    missing = all_gnd_ids - set(result.keys())
    if not missing:
        return result

    # Step 2: Try local GND persons database (batch lookup)
    db_data = lookup_gnd_batch(list(missing))
    for gnd_id, data in db_data.items():
        if data["preferred_name"]:
            result[gnd_id] = data["preferred_name"]

    missing = all_gnd_ids - set(result.keys())
    if not missing:
        return result

    # Step 3: Try lobid cache (for entities not in local databases)
    cache = _get_lobid_cache()
    cache_list = list(missing)
    for i in range(0, len(cache_list), 500):
        batch = cache_list[i:i + 500]
        placeholders = ",".join("?" * len(batch))
        rows = cache.execute(
            f"SELECT gnd_id, preferred_name FROM lobid_cache WHERE gnd_id IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            if row["preferred_name"]:
                result[row["gnd_id"]] = row["preferred_name"]

    missing = all_gnd_ids - set(result.keys())
    if not missing:
        return result

    # Step 4: Fallback to lobid.org API for still-missing IDs, cache results
    new_cache_entries = []
    for gnd_id in missing:
        try:
            r = requests.get(
                f"https://lobid.org/gnd/{gnd_id}.json",
                headers={"Accept": "application/json", "User-Agent": "FactGrid2GND/1.0"},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                name = data.get("preferredName", "")
                new_cache_entries.append((gnd_id, name))
                if name:
                    result[gnd_id] = name
        except Exception:
            pass

    # Save new entries to cache for next time
    if new_cache_entries:
        cache.executemany(
            "INSERT OR REPLACE INTO lobid_cache (gnd_id, preferred_name) VALUES (?, ?)",
            new_cache_entries,
        )
        cache.commit()

    return result


def extract_claim_values(entity, property_id):
    """Extract all values for a property from entity claims.

    Returns list of dicts with keys: value, type, rank, qualifiers, raw_datavalue.
    For wikibase-entityid, value is the QID string.
    For time, value is the raw time dict.
    For string/external-id, value is the string.
    """
    claims = entity.get("claims", {})
    if property_id not in claims:
        return []

    results = []
    for stmt in claims[property_id]:
        snak = stmt.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        dv = snak.get("datavalue", {})
        val_type = dv.get("type", "")
        value = dv.get("value")

        if val_type == "wikibase-entityid":
            extracted = value.get("id", "")
        elif val_type == "string":
            extracted = value
        elif val_type == "time":
            extracted = value  # keep full dict for date processing
        elif val_type == "monolingualtext":
            extracted = value.get("text", "")
        elif val_type == "quantity":
            extracted = value.get("amount", "")
        else:
            extracted = str(value) if value else ""

        results.append(
            {
                "value": extracted,
                "type": val_type,
                "datatype": snak.get("datatype", ""),
                "rank": stmt.get("rank", "normal"),
                "qualifiers": stmt.get("qualifiers", {}),
                "raw_datavalue": dv,
            }
        )
    return results


def format_wikibase_date(time_value, as_exact=False):
    """Convert Wikibase time dict to a formatted date string.

    time_value: dict with 'time' (e.g. '+1749-08-28T00:00:00Z') and 'precision'
    as_exact: if True, return DD.MM.YYYY format; otherwise YYYY
    """
    if isinstance(time_value, str):
        # Already formatted
        return time_value
    if not isinstance(time_value, dict):
        return ""

    time_str = time_value.get("time", "")
    precision = time_value.get("precision", 11)

    if not time_str:
        return ""

    # Remove leading +/- sign
    sign = ""
    if time_str.startswith("-"):
        sign = "-"
    time_str = time_str.lstrip("+-")

    # Parse parts
    parts = time_str.split("T")[0].split("-")
    year = parts[0] if len(parts) > 0 else ""
    month = parts[1] if len(parts) > 1 else "00"
    day = parts[2] if len(parts) > 2 else "00"

    if as_exact:
        if precision >= 11:
            return f"{sign}{day}.{month}.{year}"
        elif precision >= 10:
            # Month + year known, day unknown: render the day explicitly as XX
            # (e.g. "XX.07.1785") so it is unambiguous and consistent with the
            # XX.XX.XXXX placeholder for fully missing dates.
            return f"{sign}XX.{month}.{year}"
        elif precision >= 9:
            return f"{sign}{year}"
        elif precision == 8:
            # Decade: e.g. 1810 -> 181X
            return f"{sign}{year[:3]}X" if len(year) >= 3 else f"{sign}XXXX"
        elif precision == 7:
            # Century: e.g. 1810 -> 18XX (19th century = 18XX)
            return f"{sign}{year[:2]}XX" if len(year) >= 2 else f"{sign}XXXX"
        else:
            return f"{sign}XXXX"
    else:
        if precision >= 9:
            return f"{sign}{year}"
        elif precision == 8:
            # Decade: e.g. 1810 -> 181X
            return f"{sign}{year[:3]}X" if len(year) >= 3 else f"{sign}XXXX"
        elif precision == 7:
            # Century: e.g. 1810 -> 18XX (19th century = 18XX)
            return f"{sign}{year[:2]}XX" if len(year) >= 2 else f"{sign}XXXX"
        else:
            return f"{sign}XXXX"


def date_has_month_or_day(time_value):
    """Return True if a Wikibase time value carries month- or day-level info.

    Precision >= 10 means at least the month is known (10 = month, 11 = day).
    Year-only (9) or coarser values have no month/day info. None/strings are
    treated as not exact. Used to keep year-only dates out of field 548 datx
    ("exakte Lebensdaten"), where they carry no exact (month/day) information.
    """
    return isinstance(time_value, dict) and time_value.get("precision", 11) >= 10


def format_date_range(birth_value, death_value):
    """Create a date range string like '1749-1832' or '1892-XXXX'.

    Missing death date is replaced with XXXX.
    Missing/unknown birth date: returned as '-death' (e.g. '-1880').
    """
    birth = format_wikibase_date(birth_value) if birth_value else ""
    death = format_wikibase_date(death_value) if death_value else "XXXX"

    if not birth or birth == "XXXX":
        return f"-{death}" if death and death != "XXXX" else ""
    return f"{birth}-{death}"


def format_exact_date_range(birth_value, death_value):
    """Create exact date range like '28.08.1749-22.03.1832'.

    Missing death date is replaced with XX.XX.XXXX.
    Missing birth date: field is omitted (returned empty).
    """
    birth = format_wikibase_date(birth_value, as_exact=True) if birth_value else ""
    death = format_wikibase_date(death_value, as_exact=True) if death_value else "XX.XX.XXXX"

    if not birth or birth == "XX.XX.XXXX":
        return f"-{death}" if death and death != "XX.XX.XXXX" else ""
    return f"{birth}-{death}"


def build_preferred_name(entity, resolved_labels):
    """Build the preferred name in 'Nachname, Vorname Namenszusatz' format.

    Uses P247 (family name) and P248 (given name) properties.
    P248 values have P499 qualifier for ordering.
    P247 may have multiple values with temporal qualifiers (P49/P50);
    we pick the latest one (most recent start date).
    Name prefixes (von, van, de, ...) are extracted from the family name
    and moved after the given names, e.g. "Goethe, Johann Wolfgang von".
    Falls back to German label if P247/P248 missing.
    """
    family_name = _get_family_name(entity, resolved_labels)
    given_names = _get_given_names(entity, resolved_labels)

    if family_name and given_names:
        core, prefix = _split_name_prefix(family_name)
        if prefix:
            return f"{core}, {given_names} \x98{prefix}\x9c"
        return f"{core}, {given_names}"
    elif family_name:
        return family_name
    elif given_names:
        return given_names

    # Fallback: use German or English label, reformatted to preferred name style
    labels = entity.get("labels", {})
    for lang in ["de", "en"]:
        if lang in labels:
            return reformat_name_to_preferred(labels[lang]["value"])
    return entity.get("id", "")


# Name prefixes to extract, longest first to match multi-word prefixes before single-word
_NAME_PREFIXES = [
    "von der", "von dem", "von den",
    "van der", "van den", "van het",
    "de la", "de le", "de los", "de las", "de l'",
    "du", "de", "di", "da", "del", "della", "dei", "degli",
    "von", "vom", "zum", "zur", "zu",
    "van", "ver", "ten", "ter",
    "le", "la", "les", "l'",
    "el", "al", "ul",
    "dos", "das", "do",
    "af", "av",
]


def _split_name_prefix(family_name):
    """Split a family name into (core_name, prefix).

    E.g. "von Goethe" -> ("Goethe", "von")
         "van der Waals" -> ("Waals", "van der")
         "Mueller" -> ("Mueller", "")
    """
    lower = family_name.lower()
    for prefix in _NAME_PREFIXES:
        if lower.startswith(prefix + " "):
            core = family_name[len(prefix) + 1:]
            original_prefix = family_name[:len(prefix)]
            return core.strip(), original_prefix.strip()
    return family_name, ""


def reformat_name_to_preferred(name):
    """Reformat a free-form name variant into 'Nachname, Vorname Namenszusatz'.

    Used for field 400 variants coming from aliases/labels/P34, which are
    free-form strings (not structured P247/P248 claims like field 100).

    Heuristic: the last whitespace-separated token is the family name; any
    trailing tokens in the preceding part that match a known name prefix
    (von, van der, de la, ...) are moved behind the given names.

    Examples:
        "Johann Wolfgang von Goethe" -> "Goethe, Johann Wolfgang von"
        "Ludwig van Beethoven"       -> "Beethoven, Ludwig van"
        "Goethe, Johann Wolfgang"    -> "Goethe, Johann Wolfgang"  (already formatted)
        "Aristoteles"                -> "Aristoteles"              (single token)
    """
    if not name:
        return name
    if "," in name:
        return name
    tokens = name.split()
    if len(tokens) < 2:
        return name

    family = tokens[-1]
    before = tokens[:-1]
    lower_before = [t.lower() for t in before]

    for prefix in _NAME_PREFIXES:
        prefix_tokens = prefix.split()
        n = len(prefix_tokens)
        if len(lower_before) >= n and lower_before[-n:] == prefix_tokens:
            given_tokens = before[:-n]
            prefix_tokens_orig = before[-n:]
            if not given_tokens:
                return name
            return f"{family}, {' '.join(given_tokens)} \x98{' '.join(prefix_tokens_orig)}\x9c"

    return f"{family}, {' '.join(before)}"


def _get_family_name(entity, resolved_labels):
    """Extract the current family name from P247."""
    claims = extract_claim_values(entity, "P247")
    if not claims:
        return ""

    if len(claims) == 1:
        qid = claims[0]["value"]
        return resolved_labels.get(qid, qid)

    # Multiple family names: pick the one with the latest P49 (start date)
    best = None
    best_date = ""
    for claim in claims:
        qid = claim["value"]
        start_dates = claim["qualifiers"].get("P49", [])
        if start_dates:
            dv = start_dates[0].get("datavalue", {}).get("value", {})
            date_str = dv.get("time", "") if isinstance(dv, dict) else ""
        else:
            date_str = ""

        if date_str > best_date or best is None:
            best_date = date_str
            best = qid

    return resolved_labels.get(best, best) if best else ""


def _get_given_names(entity, resolved_labels):
    """Extract given names from P248, ordered by P499 qualifier."""
    claims = extract_claim_values(entity, "P248")
    if not claims:
        return ""

    # Sort by P499 (ordinal) qualifier
    ordered = []
    for claim in claims:
        qid = claim["value"]
        ordinal = 999
        p499 = claim["qualifiers"].get("P499", [])
        if p499:
            dv = p499[0].get("datavalue", {}).get("value", {})
            amount = dv.get("amount", "999") if isinstance(dv, dict) else "999"
            try:
                ordinal = int(amount.lstrip("+"))
            except (ValueError, AttributeError):
                pass
        label = resolved_labels.get(qid, qid)
        ordered.append((ordinal, label))

    ordered.sort(key=lambda x: x[0])
    return " ".join(name for _, name in ordered)


def format_005_timestamp():
    """Generate MARC 005 timestamp in YYYYMMDDHHMMSS.0 format."""
    return datetime.now().strftime("%Y%m%d%H%M%S") + ".0"


def format_008_field():
    """Generate the 40-character MARC 008 fixed-length field for person records."""
    date_entered = datetime.now().strftime("%y%m%d")
    # Positions: 0-5=date, 6=geo subdiv, 7=romanization, 8=lang, 9=kind,
    # 10=rules, 11-16=padding, rest=fixed
    return f"{date_entered}n||azznnaabn           | aaa    |c"


def resolve_country_code_from_gnd(gnd_id):
    """Extract the ISO 3166 country code for a GND record from field 043.

    Uses the local GND database first (fast), falls back to fetching from
    d-nb.info API if not found locally.

    Returns the country code string (e.g. 'XA-DE') or empty string if not found.
    """
    # Try local database first
    code = lookup_gnd_country_code(gnd_id)
    if code:
        return code

    # Fallback to API
    url = f"https://d-nb.info/gnd/{gnd_id}/about/marcxml"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return ""
        tree = etree.fromstring(r.content)
        ns = {"marc": "http://www.loc.gov/MARC21/slim"}
        subfields = tree.xpath(
            '//marc:datafield[@tag="043"]/marc:subfield[@code="c"]', namespaces=ns
        )
        if not subfields:
            subfields = tree.xpath('//datafield[@tag="043"]/subfield[@code="c"]')
        if subfields:
            code = subfields[0].text or ""
            parts = code.split("-")
            if len(parts) >= 2:
                return f"{parts[0]}-{parts[1]}"
            return code
    except Exception:
        pass
    return ""


def resolve_country_code_from_coordinates(place_qid, source="server"):
    """Determine ISO 3166 country code from a FactGrid place entity's coordinates.

    Fetches the place entity, extracts P48 (coordinate location) geo-coordinates,
    and uses Nominatim reverse geocoding to find the country code.
    Returns a GND-style country code (e.g. 'XA-DE') or empty string.
    """
    # Fetch the place entity to get coordinates
    try:
        entity = fetch_entity(place_qid, source=source)
    except Exception:
        return ""

    # Try P48 (coordinate location) - standard FactGrid geo property
    coord_value = None
    for prop in ["P48", "P625"]:
        claims = extract_claim_values(entity, prop)
        if claims:
            val = claims[0]["value"]
            if isinstance(val, dict) and "latitude" in val:
                coord_value = val
                break

    if not coord_value:
        return ""

    lat = coord_value["latitude"]
    lon = coord_value["longitude"]

    # Reverse geocode via Nominatim
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 3},
            headers={"User-Agent": "FactGrid2GND/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        iso_code = data.get("address", {}).get("country_code", "").upper()
        if iso_code:
            return ISO2_TO_GND_COUNTRY.get(iso_code, "")
    except Exception:
        pass
    return ""


# Mapping from ISO 3166-1 alpha-2 to GND country codes
# XA = Europe, XB = Asia, XC = Africa, XD = Americas, XE = Oceania
ISO2_TO_GND_COUNTRY = {
    "DE": "XA-DE",
    "AT": "XA-AT",
    "CH": "XA-CH",
    "FR": "XA-FR",
    "GB": "XA-GB",
    "IT": "XA-IT",
    "ES": "XA-ES",
    "NL": "XA-NL",
    "BE": "XA-BE",
    "PL": "XA-PL",
    "CZ": "XA-CZ",
    "SK": "XA-SK",
    "HU": "XA-HU",
    "RO": "XA-RO",
    "BG": "XA-BG",
    "HR": "XA-HR",
    "SI": "XA-SI",
    "RS": "XA-RS",
    "BA": "XA-BA",
    "ME": "XA-ME",
    "MK": "XA-MK",
    "AL": "XA-AL",
    "GR": "XA-GR",
    "TR": "XA-TR",
    "DK": "XA-DK",
    "SE": "XA-SE",
    "NO": "XA-NO",
    "FI": "XA-FI",
    "IS": "XA-IS",
    "IE": "XA-IE",
    "PT": "XA-PT",
    "LU": "XA-LU",
    "LI": "XA-LI",
    "LT": "XA-LT",
    "LV": "XA-LV",
    "EE": "XA-EE",
    "BY": "XA-BY",
    "UA": "XA-UA",
    "MD": "XA-MD",
    "RU": "XA-RU",
    "US": "XD-US",
    "CA": "XD-CA",
    "MX": "XD-MX",
    "BR": "XD-BR",
    "AR": "XD-AR",
    "CL": "XD-CL",
    "CO": "XD-CO",
    "PE": "XD-PE",
    "VE": "XD-VE",
    "CN": "XB-CN",
    "JP": "XB-JP",
    "IN": "XB-IN",
    "KR": "XB-KR",
    "IL": "XB-IL",
    "IR": "XB-IR",
    "IQ": "XB-IQ",
    "SA": "XB-SA",
    "AU": "XE-AU",
    "NZ": "XE-NZ",
    "EG": "XC-EG",
    "ZA": "XC-ZA",
    "NG": "XC-NG",
    "KE": "XC-KE",
    "MA": "XC-MA",
    "TN": "XC-TN",
    "DZ": "XC-DZ",
    "ET": "XC-ET",
}


def resolve_country_code_for_place(place_qid, resolved_gnd_ids, source="server"):
    """Try to determine the GND country code for a place entity.

    Strategy:
    1. If the place has a GND ID, fetch the GND record and extract field 043.
    2. Fallback: use the place's geo-coordinates with reverse geocoding.

    Returns the country code string (e.g. 'XA-DE') or empty string.
    """
    # Try GND first
    gnd_list = resolved_gnd_ids.get(place_qid, [])
    for gnd_id in gnd_list:
        code = resolve_country_code_from_gnd(gnd_id)
        if code:
            return code

    # Fallback: geo-coordinates
    return resolve_country_code_from_coordinates(place_qid, source=source)


def collect_referenced_entity_ids(entity):
    """Collect entity QIDs referenced in claims that need label resolution.

    Only collects from properties actually used in conversion (P247, P248,
    P154, P82, P168, P83) to avoid unnecessary SPARQL queries.
    """
    RELEVANT_PROPS = {"P247", "P248", "P82", "P168", "P83", "P1372", "P165"}
    ids = set()
    claims = entity.get("claims", {})
    for prop_id, statements in claims.items():
        if prop_id not in RELEVANT_PROPS:
            continue
        for stmt in statements:
            snak = stmt.get("mainsnak", {})
            dv = snak.get("datavalue", {})
            if dv.get("type") == "wikibase-entityid":
                val = dv.get("value", {})
                if "id" in val:
                    ids.add(val["id"])
            # Also check qualifiers (needed for P499 ordinal on given names)
            for qual_snaks in stmt.get("qualifiers", {}).values():
                for qs in qual_snaks:
                    qdv = qs.get("datavalue", {})
                    if qdv.get("type") == "wikibase-entityid":
                        qval = qdv.get("value", {})
                        if "id" in qval:
                            ids.add(qval["id"])
    return ids
