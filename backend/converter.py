"""
Core conversion logic: FactGrid entity → GND MARC 21 record.
"""

from datetime import datetime

from lxml import etree
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils import (
    fetch_entity,
    resolve_labels,
    resolve_gnd_ids,
    resolve_gnd_preferred_names,
    extract_claim_values,
    format_wikibase_date,
    format_date_range,
    format_exact_date_range,
    date_has_month_or_day,
    build_preferred_name,
    reformat_name_to_preferred,
    format_005_timestamp,
    format_008_field,
    collect_referenced_entity_ids,
    resolve_country_code_for_place,
)
from mappings_config import (
    ISIL,
    LEADER,
    CONSTANT_CONTROLFIELDS,
    CONSTANT_DATAFIELDS,
    PROP_GND_ID,
    PROP_BIRTH_DATE,
    PROP_DEATH_DATE,
    PROP_FAMILY_NAME,
    PROP_GIVEN_NAME,
    PROP_OCCUPATION,
    PROP_BIRTH_PLACE,
    PROP_DEATH_PLACE,
    PROP_PLACE_OF_ACTIVITY,
    PROP_PLACE_OF_ACTIVITY_2,
    PROP_ACTIVITY_START,
    PROP_ACTIVITY_END,
    ACTIVITY_EVENT_PROPS,
    GND_PERIOD_OF_ACTIVITY,
    MANDATORY_TAGS,
    INDIVIDUALIZATION_GROUP1,
    INDIVIDUALIZATION_GROUP2,
    INDIVIDUALIZATION_SUBTYPE_CODES,
    MIN_INDIVIDUALIZATION_TOTAL,
    MIN_INDIVIDUALIZATION_GROUP1,
    FIELD_DESCRIPTIONS,
)

MARC_NS = "http://www.loc.gov/MARC21/slim"


def convert_entities(qids, source="server", field079q=("d",), field667a="Historisches Datenzentrum Sachsen-Anhalt"):
    """Convert a list of FactGrid QIDs to MARC 21 records.

    field079q may be a string or a sequence of strings; each value becomes one
    repeated $q subfield in the single 079 datafield.

    Returns: {"records": [...], "errors": [...]}
    """
    records = []
    errors = []
    for event in convert_entities_stream(qids, source=source, field079q=field079q, field667a=field667a):
        if event["type"] == "record":
            records.append(event["record"])
        elif event["type"] == "error":
            errors.append({"qid": event["qid"], "error": event["error"]})
    return {"records": records, "errors": errors}


def convert_entities_stream(qids, source="server", field079q=("d",), field667a="Historisches Datenzentrum Sachsen-Anhalt"):
    """Convert QIDs to MARC 21 records, yielding progress events.

    Yields dicts: {"type": "progress", "message": "..."} or
                  {"type": "record", "record": {...}} or
                  {"type": "error", "qid": "...", "error": "..."} or
                  {"type": "done"}
    """
    total = len(qids)
    source_label = "lokaler Datenbank" if source == "local" else "FactGrid"

    # Fetch all entities in parallel
    entities = {}
    yield {"type": "progress", "message": f"Lade {total} Entitaet(en) von {source_label}..."}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_entity, qid, source): qid for qid in qids}
        fetched = 0
        for future in as_completed(futures):
            qid = futures[future]
            fetched += 1
            try:
                entities[qid] = future.result()
                yield {"type": "progress", "message": f"Entitaet {qid} geladen ({fetched}/{total})"}
            except Exception as e:
                yield {"type": "error", "qid": qid, "error": str(e)}

    # Collect all referenced entity IDs for batch label resolution
    all_ref_ids = set()
    for entity in entities.values():
        all_ref_ids.update(collect_referenced_entity_ids(entity))

    yield {"type": "progress", "message": f"Loese {len(all_ref_ids)} Labels auf..."}
    resolved_labels = resolve_labels(list(all_ref_ids), lang="de", source=source)

    yield {"type": "progress", "message": f"Loese GND-IDs auf..."}
    resolved_gnd_ids = resolve_gnd_ids(list(all_ref_ids), source=source)

    yield {"type": "progress", "message": f"Lade GND-Vorzugsbenennungen..."}
    gnd_preferred_names = resolve_gnd_preferred_names(resolved_gnd_ids)

    # Convert each entity
    converted = 0
    for qid in qids:
        if qid not in entities:
            continue
        converted += 1
        yield {"type": "progress", "message": f"Konvertiere {qid} ({converted}/{len(entities)})..."}
        try:
            record = convert_entity_to_marc(entities[qid], resolved_labels, resolved_gnd_ids, gnd_preferred_names, source=source, field079q=field079q, field667a=field667a)
            record["validation"] = validate_record(record)
            record["validation"]["warnings"] = record.pop("warnings") + record["validation"]["warnings"]
            yield {"type": "record", "record": record}
        except Exception as e:
            yield {"type": "error", "qid": qid, "error": str(e)}

    yield {"type": "done"}


def _get_gnd_id(resolved_gnd_ids, ref_qid, warnings, tag="", display_name="", gnd_preferred_names=None):
    """Get the first GND ID for a referenced entity, adding a warning if multiple exist."""
    gnd_list = resolved_gnd_ids.get(ref_qid, [])
    if not gnd_list:
        return ""
    if len(gnd_list) > 1:
        if gnd_preferred_names:
            alt_labels = [f"{gid} ({gnd_preferred_names.get(gid, '?')})" for gid in gnd_list]
        else:
            alt_labels = gnd_list
        prefix = f"Feld {tag}" if tag else ref_qid
        name_part = f" \"{display_name}\"" if display_name else ""
        warnings.append(
            f"{prefix}{name_part} hat mehrere GND-IDs: {', '.join(alt_labels)} — verwende {gnd_list[0]}"
        )
    return gnd_list[0]


def convert_entity_to_marc(entity, resolved_labels, resolved_gnd_ids=None, gnd_preferred_names=None, source="server", field079q=("d",), field667a="Historisches Datenzentrum Sachsen-Anhalt"):
    """Convert a single FactGrid entity to a MARC 21 record dict."""
    qid = entity.get("id", "")
    gnd_warnings = []

    # Get label for display
    labels = entity.get("labels", {})
    display_label = ""
    for lang in ["de", "en"]:
        if lang in labels:
            display_label = labels[lang]["value"]
            break

    # Build controlfields
    controlfields = [
        {"tag": "001", "value": qid},
        {"tag": "003", "value": CONSTANT_CONTROLFIELDS["003"]},
        {"tag": "005", "value": format_005_timestamp()},
        {"tag": "008", "value": format_008_field()},
    ]

    # Build datafields
    datafields = []

    # --- GND system number (035) ---
    gnd_claims = extract_claim_values(entity, PROP_GND_ID)
    if gnd_claims:
        gnd_id = gnd_claims[0]["value"]
        gnd_warnings.append(
            f"Person hat bereits eine GND-ID: {gnd_id}"
        )
    else:
        gnd_id = "null"
    datafields.append(
        {
            "tag": "035",
            "ind1": " ",
            "ind2": " ",
            "subfields": [
                {"code": "a", "value": f"(DE-588){gnd_id}"},
            ],
        }
    )

    # --- Constant datafields (040, 042, 075, 079) ---
    # Normalise field079q into a list of non-empty strings (allows passing
    # a single string for back-compat with old callers and tests).
    if isinstance(field079q, str):
        q_values = [field079q] if field079q else []
    else:
        q_values = [v for v in field079q if v]
    if not q_values:
        q_values = ["d"]

    for field in CONSTANT_DATAFIELDS:
        subfields = []
        for sf in field["subfields"]:
            # Expand 079 $q into one subfield per user-selected Teilbestandskennzeichen
            if field["tag"] == "079" and sf["code"] == "q":
                for q in q_values:
                    subfields.append({"code": "q", "value": q})
                continue
            subfields.append({"code": sf["code"], "value": sf["value"]})
        datafields.append(
            {
                "tag": field["tag"],
                "ind1": field["ind1"],
                "ind2": field["ind2"],
                "subfields": subfields,
            }
        )

    # --- Helper: GND preferred name lookup ---
    if resolved_gnd_ids is None:
        resolved_gnd_ids = {}
    if gnd_preferred_names is None:
        gnd_preferred_names = {}

    def _gnd_name(ref_qid):
        """Get display name: prefer GND preferred name, fallback to FactGrid label."""
        gnd_list = resolved_gnd_ids.get(ref_qid, [])
        if gnd_list:
            name = gnd_preferred_names.get(gnd_list[0], "")
            if name:
                return name
        return resolved_labels.get(ref_qid, ref_qid)

    # --- Country code (043) ---
    # Priority: Wirkungsort (P1372) → Sterbeort (P168) → Geburtsort (P82)
    country_code = ""
    country_code_source = ""
    for prop, label in [
        (PROP_PLACE_OF_ACTIVITY_2, "Wirkungsort"),
        (PROP_PLACE_OF_ACTIVITY, "Wirkungsort"),
        (PROP_DEATH_PLACE, "Sterbeort"),
        (PROP_BIRTH_PLACE, "Geburtsort"),
    ]:
        if country_code:
            break
        place_claims = extract_claim_values(entity, prop)
        for claim in place_claims:
            place_qid = claim["value"]
            code = resolve_country_code_for_place(place_qid, resolved_gnd_ids or {}, source=source)
            if code:
                country_code = code
                place_name = _gnd_name(place_qid)
                country_code_source = f"{label} {place_name}"
                break

    # Always emit field 043 so the user can enter the country code manually when
    # it could not be resolved automatically ($c stays empty in that case).
    datafields.append(
        {
            "tag": "043",
            "ind1": " ",
            "ind2": " ",
            "subfields": [{"code": "c", "value": country_code}],
        }
    )
    if not country_code:
        gnd_warnings.append(
            "Ländercode (043) konnte nicht ermittelt werden — "
            "bitte manuell eintragen (kein Ort mit GND-ID oder Koordinaten gefunden)"
        )

    # --- Preferred name (100) ---
    preferred_name = build_preferred_name(entity, resolved_labels)
    birth_claims = extract_claim_values(entity, PROP_BIRTH_DATE)
    death_claims = extract_claim_values(entity, PROP_DEATH_DATE)
    birth_val = next((c["value"] for c in birth_claims if c["rank"] == "preferred"),
                     birth_claims[0]["value"] if birth_claims else None)
    death_val = next((c["value"] for c in death_claims if c["rank"] == "preferred"),
                     death_claims[0]["value"] if death_claims else None)
    # Life-date alternatives (shared by the datl picker and the name fields).
    has_multiple_dates = len(birth_claims) > 1 or len(death_claims) > 1
    date_alternatives = []
    if has_multiple_dates:
        # Only offer alternatives without a birth date when there is genuinely no
        # birth claim. If a birth date exists, every alternative keeps it (no
        # "-death"-only options). Death keeps its None (unknown death is valid).
        birth_values = [c["value"] for c in birth_claims] or [None]
        death_values = [c["value"] for c in death_claims] + [None]
        seen_ranges = set()
        for bv in birth_values:
            for dv in death_values:
                dr = format_date_range(bv, dv)
                if dr and dr not in seen_ranges:
                    seen_ranges.add(dr)
                    b_str = format_wikibase_date(bv) if bv else ""
                    d_str = format_wikibase_date(dv) if dv else "XXXX"
                    if not b_str or b_str == "XXXX":
                        b_str = ""
                    date_alternatives.append({"value": dr, "label": f"{b_str}–{d_str}"})

    # datl is pre-selected only when BOTH birth and death have a preferred-rank
    # claim; otherwise the picker is shown without pre-selection (datl $a empty).
    show_picker = has_multiple_dates and len(date_alternatives) > 1
    both_preferred = (
        any(c["rank"] == "preferred" for c in birth_claims)
        and any(c["rank"] == "preferred" for c in death_claims)
    )
    datl_no_preselection = show_picker and not both_preferred

    # Life-date range carried in $d of the name fields (100/400). Kept in sync
    # with datl: when datl has no pre-selection, $d is left empty too.
    date_range = "" if datl_no_preselection else format_date_range(birth_val, death_val)

    field_100_subfields = [{"code": "a", "value": preferred_name}]
    if date_range:
        field_100_subfields.append({"code": "d", "value": date_range})
    datafields.append(
        {
            "tag": "100",
            "ind1": "1",
            "ind2": " ",
            "subfields": field_100_subfields,
        }
    )

    # --- Variant names (400) from P34 ---
    seen_variants = set()

    def _add_variant(name):
        name = reformat_name_to_preferred(name)
        if name and name != preferred_name and name not in seen_variants:
            seen_variants.add(name)
            subfields_400 = [{"code": "a", "value": name}]
            if date_range:
                subfields_400.append({"code": "d", "value": date_range})
            datafields.append(
                {
                    "tag": "400",
                    "ind1": "1",
                    "ind2": " ",
                    "subfields": subfields_400,
                }
            )

    for claim in extract_claim_values(entity, "P34"):
        _add_variant(claim["value"])

    # --- Life dates (548) ---
    if birth_val or death_val:
        # Approximate dates (datl). Field 548/datl is NOT repeatable, so the
        # frontend offers the alternatives as a single-select picker when
        # multiple date claims exist. datl_no_preselection (computed above) leaves
        # $a empty when not both dates have a preferred rank; the "bitte ein Datum
        # waehlen" warning is produced (regenerably) by validate_record().
        year_range = format_date_range(birth_val, death_val)
        if year_range:
            datl_a = "" if datl_no_preselection else year_range
            field_548_datl = {
                    "tag": "548",
                    "ind1": " ",
                    "ind2": " ",
                    "subfields": [
                        {"code": "a", "value": datl_a},
                        {"code": "4", "value": "datl"},
                        {
                            "code": "4",
                            "value": "https://d-nb.info/standards/elementset/gnd#dateOfBirthAndDeath",
                        },
                        {"code": "w", "value": "r"},
                        {"code": "i", "value": "Lebensdaten"},
                    ],
                }
            if show_picker:
                field_548_datl["date_alternatives"] = date_alternatives
            datafields.append(field_548_datl)

        # Exact dates (datx) — build all alternatives from all claims.
        # Skip combinations that carry no month/day info at all (pure year
        # ranges like 1731-1809): those belong in datl, not in the "exakte
        # Lebensdaten" field.
        datx_alternatives = []
        if has_multiple_dates:
            seen_exact = set()
            for bv in birth_values:
                for dv in death_values:
                    if not (date_has_month_or_day(bv) or date_has_month_or_day(dv)):
                        continue
                    er = format_exact_date_range(bv, dv)
                    if er and er not in seen_exact:
                        seen_exact.add(er)
                        b_ex = format_wikibase_date(bv, as_exact=True) if bv else ""
                        d_ex = format_wikibase_date(dv, as_exact=True) if dv else "XX.XX.XXXX"
                        datx_alternatives.append({"value": er, "label": f"{b_ex}–{d_ex}"})

        if not has_multiple_dates:
            has_exact = date_has_month_or_day(birth_val) or date_has_month_or_day(death_val)
            exact_range = format_exact_date_range(birth_val, death_val) if has_exact else ""
            if exact_range and exact_range != year_range:
                datafields.append(
                    {
                        "tag": "548",
                        "ind1": " ",
                        "ind2": " ",
                        "subfields": [
                            {"code": "a", "value": exact_range},
                            {"code": "4", "value": "datx"},
                            {
                                "code": "4",
                                "value": "https://d-nb.info/standards/elementset/gnd#dateOfBirthAndDeath",
                            },
                            {"code": "w", "value": "r"},
                            {"code": "i", "value": "Exakte Lebensdaten"},
                        ],
                    }
                )
        # The "bitte zutreffende Werte waehlen" prompt for the datx picker is
        # produced (regenerably) by validate_record() so it reappears when the
        # user deselects all options again.
        if has_multiple_dates and datx_alternatives:
            field_548_datx = {
                "tag": "548",
                "ind1": " ",
                "ind2": " ",
                "subfields": [
                    {"code": "a", "value": ""},
                    {"code": "4", "value": "datx"},
                    {
                        "code": "4",
                        "value": "https://d-nb.info/standards/elementset/gnd#dateOfBirthAndDeath",
                    },
                    {"code": "w", "value": "r"},
                    {"code": "i", "value": "Exakte Lebensdaten"},
                ],
                "date_alternatives": datx_alternatives,
            }
            datafields.append(field_548_datx)

    # --- Activity dates (548) — fallback when no life dates exist ---
    # Only used if the person has neither a birth (P77) nor a death (P38) date.
    # Mirrors the datl/datx logic: activity dates are emitted as datw (non-exact,
    # year level — like datl) and datz (exact, month/day — like datx). When
    # several activity dates exist they are offered for selection (datw radio,
    # datz checkboxes), just like the life-date pickers.
    if not birth_val and not death_val:
        start_claims = extract_claim_values(entity, PROP_ACTIVITY_START)
        end_claims = extract_claim_values(entity, PROP_ACTIVITY_END)
        event_claims = []
        for prop in ACTIVITY_EVENT_PROPS:
            event_claims.extend(extract_claim_values(entity, prop))

        # Like datl/datx: only offer a start-less alternative when there is no
        # Wirkungsbeginn claim. End keeps None (open/unknown end is valid).
        start_values = [c["value"] for c in start_claims] or [None]
        end_values = [c["value"] for c in end_claims] + [None]
        has_multiple_activity = (
            len(start_claims) > 1 or len(end_claims) > 1 or len(event_claims) > 1
            or (bool(event_claims) and bool(start_claims or end_claims))
        )

        # datw (non-exact, year level — like datl)
        datw_alternatives = []
        seen_w = set()

        def _add_w(value):
            if value and value not in seen_w:
                seen_w.add(value)
                datw_alternatives.append({"value": value, "label": value})

        for sv in start_values:
            for ev in end_values:
                if sv is None and ev is None:
                    continue
                _add_w(format_date_range(sv, ev))
        for claim in event_claims:
            _add_w(format_wikibase_date(claim["value"]))

        if datw_alternatives:
            field_548_datw = {
                "tag": "548",
                "ind1": " ",
                "ind2": " ",
                "subfields": [
                    {"code": "a", "value": datw_alternatives[0]["value"]},
                    {"code": "4", "value": "datw"},
                    {"code": "4", "value": GND_PERIOD_OF_ACTIVITY},
                    {"code": "w", "value": "r"},
                    {"code": "i", "value": "Wirkungsdaten"},
                ],
            }
            if len(datw_alternatives) > 1:
                field_548_datw["date_alternatives"] = datw_alternatives
            datafields.append(field_548_datw)

        # datz (exact, month/day — like datx). Skip combinations without any
        # month/day info (those carry no exact info and are covered by datw).
        datz_alternatives = []
        seen_z = set()

        def _add_z(value):
            if value and value not in seen_z:
                seen_z.add(value)
                datz_alternatives.append({"value": value, "label": value})

        for sv in start_values:
            for ev in end_values:
                if not (date_has_month_or_day(sv) or date_has_month_or_day(ev)):
                    continue
                _add_z(format_exact_date_range(sv, ev))
        for claim in event_claims:
            if date_has_month_or_day(claim["value"]):
                _add_z(format_wikibase_date(claim["value"], as_exact=True))

        datw_year = datw_alternatives[0]["value"] if datw_alternatives else None
        if datz_alternatives and not has_multiple_activity:
            # Single exact activity date: pre-fill (mirrors the datx single path)
            exact = datz_alternatives[0]["value"]
            if exact and exact != datw_year:
                datafields.append({
                    "tag": "548",
                    "ind1": " ",
                    "ind2": " ",
                    "subfields": [
                        {"code": "a", "value": exact},
                        {"code": "4", "value": "datz"},
                        {"code": "4", "value": GND_PERIOD_OF_ACTIVITY},
                        {"code": "w", "value": "r"},
                        {"code": "i", "value": "Exakte Wirkungsdaten"},
                    ],
                })
        elif datz_alternatives:
            # Multiple activity dates: offer datz as multi-select (mirrors datx).
            # The selection prompt is produced (regenerably) by validate_record().
            datafields.append({
                "tag": "548",
                "ind1": " ",
                "ind2": " ",
                "subfields": [
                    {"code": "a", "value": ""},
                    {"code": "4", "value": "datz"},
                    {"code": "4", "value": GND_PERIOD_OF_ACTIVITY},
                    {"code": "w", "value": "r"},
                    {"code": "i", "value": "Exakte Wirkungsdaten"},
                ],
                "date_alternatives": datz_alternatives,
            })

    # --- Occupation (550) - only those with GND ID ---
    occupation_claims = extract_claim_values(entity, PROP_OCCUPATION)
    # Filter to only those with GND ID
    occ_with_gnd = []
    for claim in occupation_claims:
        occ_qid = claim["value"]
        occ_name_preview = _gnd_name(occ_qid)
        gnd_id = _get_gnd_id(resolved_gnd_ids, occ_qid, gnd_warnings,
                             tag="550", display_name=occ_name_preview,
                             gnd_preferred_names=gnd_preferred_names)
        if gnd_id:
            occ_with_gnd.append((claim, gnd_id))

    # Exactly one occupation may carry the non-repeatable code "berc" — the
    # one hochgerankt (preferred rank) in FactGrid, or the sole occupation if
    # there is only one. All others get "beru". If multiple claims are preferred
    # (unexpected), only the first wins.
    berc_index = next(
        (i for i, (c, _) in enumerate(occ_with_gnd) if c["rank"] == "preferred"),
        None,
    )
    if berc_index is None and len(occ_with_gnd) == 1:
        berc_index = 0

    # Sort so that berc (charakteristischer Beruf) comes before beru
    if berc_index is not None and berc_index > 0:
        berc_item = occ_with_gnd.pop(berc_index)
        occ_with_gnd.insert(0, berc_item)
        berc_index = 0

    for i, (claim, gnd_id) in enumerate(occ_with_gnd):
        occ_qid = claim["value"]
        occ_name = _gnd_name(occ_qid)
        gnd_list = resolved_gnd_ids.get(occ_qid, [])
        is_berc = i == berc_index
        code4 = "berc" if is_berc else "beru"
        label_i = "Charakteristischer Beruf" if is_berc else "Beruf"
        field = {
            "tag": "550",
            "ind1": " ",
            "ind2": " ",
            "subfields": [
                {"code": "0", "value": f"(DE-588){gnd_id}"},
                {"code": "0", "value": f"https://d-nb.info/gnd/{gnd_id}"},
                {"code": "a", "value": occ_name},
                {"code": "4", "value": code4},
                {
                    "code": "4",
                    "value": "https://d-nb.info/standards/elementset/gnd#professionOrOccupation",
                },
                {"code": "w", "value": "r"},
                {"code": "i", "value": label_i},
            ],
        }
        if len(gnd_list) > 1:
            field["gnd_alternatives"] = [
                {"id": gid, "label": gnd_preferred_names.get(gid, gid)}
                for gid in gnd_list
            ]
        datafields.append(field)

    # --- Places (551): birth, death, activity ---

    place_configs = [
        (PROP_BIRTH_PLACE, "ortg", "https://d-nb.info/standards/elementset/gnd#placeOfBirth", "Geburtsort"),
        (PROP_DEATH_PLACE, "orts", "https://d-nb.info/standards/elementset/gnd#placeOfDeath", "Sterbeort"),
        (PROP_PLACE_OF_ACTIVITY_2, "ortw", "https://d-nb.info/standards/elementset/gnd#placeOfActivity", "Wirkungsort"),
    ]
    for prop, code4, url4, label_i in place_configs:
        place_claims = extract_claim_values(entity, prop)
        for claim in place_claims:
            place_qid = claim["value"]
            place_name = _gnd_name(place_qid)
            gnd_id = _get_gnd_id(resolved_gnd_ids, place_qid, gnd_warnings,
                                 tag="551", display_name=place_name,
                                 gnd_preferred_names=gnd_preferred_names)
            gnd_list = resolved_gnd_ids.get(place_qid, [])
            subfields_551 = []
            if gnd_id:
                subfields_551.append({"code": "0", "value": f"(DE-588){gnd_id}"})
                subfields_551.append({"code": "0", "value": f"https://d-nb.info/gnd/{gnd_id}"})
            subfields_551.extend([
                {"code": "a", "value": place_name},
                {"code": "4", "value": code4},
                {"code": "4", "value": url4},
                {"code": "w", "value": "r"},
                {"code": "i", "value": label_i},
            ])
            field = {
                "tag": "551",
                "ind1": " ",
                "ind2": " ",
                "subfields": subfields_551,
            }
            if len(gnd_list) > 1:
                field["gnd_alternatives"] = [
                    {"id": gid, "label": gnd_preferred_names.get(gid, gid)}
                    for gid in gnd_list
                ]
            datafields.append(field)

    # --- Source note (670) ---
    datafields.append(
        {
            "tag": "670",
            "ind1": " ",
            "ind2": " ",
            "subfields": [
                {"code": "a", "value": "FactGrid"},
                {"code": "b", "value": f"Stand: {datetime.now().strftime('%d.%m.%Y')}"},
                {
                    "code": "u",
                    "value": f"https://database.factgrid.de/wiki/Item:{qid}",
                },
            ],
        }
    )

    # --- Editorial note (667) ---
    if field667a:
        datafields.append(
            {
                "tag": "667",
                "ind1": " ",
                "ind2": " ",
                "subfields": [
                    {"code": "a", "value": field667a},
                    {"code": "5", "value": ISIL},
                ],
            }
        )

    # Sort datafields by tag
    datafields.sort(key=lambda f: f["tag"])

    return {
        "qid": qid,
        "label": display_label,
        "leader": LEADER,
        "controlfields": controlfields,
        "datafields": datafields,
        "warnings": gnd_warnings,
    }


def _field_is_exported(df):
    """Return True if a datafield survives the MARC XML export filter.

    Mirrors the filtering applied in ``_build_record_element`` so that
    validation (e.g. individualization counting) only considers fields that
    actually end up in the exported record:
      * fields with an empty ``$a`` are dropped,
      * 551 fields without a valid ``(DE-588)`` GND reference are dropped.
    """
    sf_a = next((sf for sf in df.get("subfields", []) if sf["code"] == "a"), None)
    if sf_a is not None and sf_a["value"] == "":
        return False
    if df.get("tag") == "551":
        has_gnd = any(
            sf["code"] == "0"
            and sf["value"].startswith("(DE-588)")
            and sf["value"][len("(DE-588)"):].strip()
            for sf in df.get("subfields", [])
        )
        if not has_gnd:
            return False
    return True


def validate_record(record):
    """Validate a MARC 21 record against GND Level 1 requirements.

    Returns validation dict with status info.
    """
    # Collect all tags present
    present_tags = set()
    for cf in record.get("controlfields", []):
        present_tags.add(cf["tag"])
    for df in record.get("datafields", []):
        present_tags.add(df["tag"])

    # Check mandatory fields
    mandatory_missing = [tag for tag in MANDATORY_TAGS if tag not in present_tags]

    # Derive individualization criteria from the data fields. Field 548 is split
    # by its $4 sub-type so that approximate life dates (datl), exact life dates
    # (datx) and activity dates (datw) each count as a distinct criterion.
    # Derive criteria as "<tag>-<$4-code>" so the group assignment follows the
    # $4 sub-type as required by EH-P-16 (datw is Group 2, only 550 berc is
    # Group 1, each 551 place type counts separately, etc.).
    present_criteria = set()
    for df in record.get("datafields", []):
        tag = df["tag"]
        allowed = INDIVIDUALIZATION_SUBTYPE_CODES.get(tag)
        if not allowed:
            continue
        # Only count criteria from fields that will actually be exported
        # (non-empty $a, 551 with GND reference) so the displayed count matches
        # the exported record.
        if not _field_is_exported(df):
            continue
        for sf in df.get("subfields", []):
            if sf["code"] == "4" and sf["value"] in allowed:
                present_criteria.add(f"{tag}-{sf['value']}")

    # Count individualization attributes
    group1_present = [
        key for key in INDIVIDUALIZATION_GROUP1 if key in present_criteria
    ]
    group2_present = [
        key for key in INDIVIDUALIZATION_GROUP2 if key in present_criteria
    ]
    total_indiv = len(group1_present) + len(group2_present)

    # Build warnings
    warnings = []
    for tag in mandatory_missing:
        desc = FIELD_DESCRIPTIONS.get(tag, tag)
        warnings.append(f"Pflichtfeld {tag} ({desc}) fehlt")

    if total_indiv < MIN_INDIVIDUALIZATION_TOTAL:
        warnings.append(
            f"Nur {total_indiv} von {MIN_INDIVIDUALIZATION_TOTAL} "
            f"Individualisierungsmerkmalen vorhanden"
        )
    # A 548 datl field with an empty $a means no life-date range was selected
    # (e.g. picker without pre-selection). Regenerable so it clears once filled.
    for df in record.get("datafields", []):
        if df["tag"] != "548":
            continue
        if not any(sf["code"] == "4" and sf["value"] == "datl" for sf in df.get("subfields", [])):
            continue
        sf_a = next((sf["value"] for sf in df.get("subfields", []) if sf["code"] == "a"), "")
        if sf_a.strip() == "":
            warnings.append("Feld 548 (datl): bitte ein Datum waehlen")

    # Multi-select date pickers (datx = exact life dates, datz = exact activity
    # dates): warn while the picker is offered (a field carries date_alternatives)
    # but nothing is selected yet (no field of that sub-type has a non-empty $a).
    # Regenerable, so the warning reappears if the user deselects all options.
    for sub in ("datx", "datz"):
        fields = [
            df for df in record.get("datafields", [])
            if df.get("tag") == "548"
            and any(sf["code"] == "4" and sf["value"] == sub for sf in df.get("subfields", []))
        ]
        if not fields:
            continue
        has_alternatives = any(df.get("date_alternatives") for df in fields)
        has_selected = any(
            (next((sf["value"] for sf in df.get("subfields", []) if sf["code"] == "a"), "")).strip()
            for df in fields
        )
        if has_alternatives and not has_selected:
            warnings.append(f"Feld 548 ({sub}): bitte zutreffende Werte waehlen")

    # Check for 550/551 fields missing $0 (GND reference)
    for df in record.get("datafields", []):
        if df["tag"] in ("550", "551"):
            has_gnd_ref = any(sf["code"] == "0" for sf in df.get("subfields", []))
            if not has_gnd_ref:
                sf_a = next((sf["value"] for sf in df["subfields"] if sf["code"] == "a"), "?")
                desc = "Beruf/Beschäftigung" if df["tag"] == "550" else "Geografikum"
                warnings.append(f"Feld {df['tag']} ({desc} \"{sf_a}\") hat keine GND-Referenz ($0)")

    # Duplicate detection within the record: same GND $0 or same $a within a tag
    DUP_TAGS = ("400", "550", "551", "670")
    seen_by_tag = {}  # tag -> {"gnd": set[gnd_id], "name": set[name]}
    for df in record.get("datafields", []):
        tag = df["tag"]
        if tag not in DUP_TAGS:
            continue
        entry = seen_by_tag.setdefault(tag, {"gnd": set(), "name": set(), "dup_gnd": set(), "dup_name": set()})
        gnd_id = next(
            (sf["value"].replace("(DE-588)", "") for sf in df.get("subfields", [])
             if sf["code"] == "0" and sf["value"].startswith("(DE-588)")),
            "",
        )
        name = next((sf["value"] for sf in df.get("subfields", []) if sf["code"] == "a"), "")
        if gnd_id:
            if gnd_id in entry["gnd"] and gnd_id not in entry["dup_gnd"]:
                entry["dup_gnd"].add(gnd_id)
                warnings.append(f"Dublette in Feld {tag}: GND {gnd_id} mehrfach vorhanden")
            entry["gnd"].add(gnd_id)
        elif name:
            if name in entry["name"] and name not in entry["dup_name"]:
                entry["dup_name"].add(name)
                warnings.append(f"Dublette in Feld {tag}: \"{name}\" mehrfach vorhanden")
            entry["name"].add(name)

    if len(group1_present) < MIN_INDIVIDUALIZATION_GROUP1:
        missing_g1 = [
            desc
            for key, desc in INDIVIDUALIZATION_GROUP1.items()
            if key not in present_criteria
        ]
        warnings.append(
            f"Mindestens {MIN_INDIVIDUALIZATION_GROUP1} Merkmal(e) aus Gruppe 1 "
            f"erforderlich: {', '.join(missing_g1)}"
        )

    # Determine status
    if mandatory_missing:
        status = "error"
    elif warnings:
        status = "warning"
    else:
        status = "ok"

    return {
        "status": status,
        "mandatory_missing": mandatory_missing,
        "individualization_count": total_indiv,
        "group1_count": len(group1_present),
        "group2_count": len(group2_present),
        "group1_present": group1_present,
        "group2_present": group2_present,
        "warnings": warnings,
    }


def records_to_marc_xml(records):
    """Serialize a list of MARC 21 record dicts to XML string.

    Returns well-formed MARC 21 XML with proper namespace.
    """
    nsmap = {None: MARC_NS}

    if len(records) == 1:
        root = _build_record_element(records[0], nsmap)
    else:
        root = etree.Element("collection", nsmap=nsmap)
        for record in records:
            rec_elem = _build_record_element(record, nsmap)
            root.append(rec_elem)

    xml_str = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    ).decode("utf-8")
    # Ensure MARC sort control characters appear as numeric XML references
    xml_str = xml_str.replace("\x98", "&#152;").replace("\x9c", "&#156;")
    return xml_str


def _build_record_element(record, nsmap):
    """Build a single <record> element from a record dict."""
    rec = etree.Element("record", nsmap=nsmap, type="Authority")

    # Leader
    leader = etree.SubElement(rec, "leader")
    leader.text = record.get("leader", LEADER)

    # Control fields
    for cf in record.get("controlfields", []):
        elem = etree.SubElement(rec, "controlfield", tag=cf["tag"])
        elem.text = cf["value"]

    # Data fields (skip fields with empty $a, and 551 without GND reference)
    for df in record.get("datafields", []):
        if not _field_is_exported(df):
            continue
        elem = etree.SubElement(
            rec,
            "datafield",
            tag=df["tag"],
            ind1=df.get("ind1", " "),
            ind2=df.get("ind2", " "),
        )
        for sf in df.get("subfields", []):
            sub = etree.SubElement(elem, "subfield", code=sf["code"])
            sub.text = sf["value"]

    return rec
