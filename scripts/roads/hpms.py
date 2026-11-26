"""Traffic counts from HPMS (the US Federal Highway Administration's Highway Performance Monitoring System),
joined to the road groups of scripts/roads/build.py.

`fetch` and `fetch_state` read the pinned BTS NTAD HPMS 2024 zip locally,
filtered by box and cached by source, state, boxes and vintage. `join` and
`join_by_state` give each road group the yearly average daily traffic (AADT)
and truck share of a segment on the same road with aligned geometry.
Licence: HPMS is a US federal public-domain dataset. Field meanings: the HPMS Field Manual (FHWA)."""
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the app's roads.py holds the geometry helpers
from roads import EARTH_R_M, haversine_m, local_xy, seg_distance  # noqa: E402
from us_geometry import GeometryClipper  # noqa: E402

# BTS NTAD ArcGIS item 5e6a977c2d7c4ec1bdc82e684d3384f2; HPMS2024.zip is 2,623,940,113 bytes.
HPMS_FILE_SHA256 = "129b868c0c6d684f63ea137e6a1658495e5a6b520af2bbfc62785c4f31b8c83f"
HPMS_FILE_BYTES = 2_623_940_113  # bytes of the pinned national download; reject truncated or substituted files
HPMS_HASH_CHUNK_BYTES = 1024 * 1024  # bytes per hash read: bound memory for the 2.6 GB archive
HPMS_SOURCE = "BTS NTAD HPMS (FHWA, distributed by BTS (NTAD))"
HPMS_DOWNLOAD_PAGE = "https://geodata.bts.gov/datasets/5e6a977c2d7c4ec1bdc82e684d3384f2/about"
HPMS_COLUMNS = {"route_id": "route_id", "routename": "route_name", "routenumber": "route_number", "routesigning": "route_signing",
                "aadt": "aadt", "aadt_combination": "aadt_combination", "aadt_single_unit": "aadt_single_unit",
                "f_system": "f_system", "facility_type": "facility_type"}
HPMS_JOIN_M = 30.0  # metres: a segment counts for a road only where it runs within this distance of the road
ALIGN_MIN_M = 100.0  # metres: avoid attributing a long HPMS row from a brief local contact
ALIGN_MIN_SHARE = 0.8  # fraction of an HPMS row: allow short rows when almost all of their line runs along the group
ALIGN_FLOOR_M = 25.0  # metres: sub-junction fragments cannot supply enough evidence to identify a road
ALIGN_MAX_DEG = 15.0  # degrees, direction-free: tolerate digitization/curves while excluding the reviewed 47° and 89° crossings
NAME_ALIGN_M = 10.0  # metres: name-only support must exclude the reviewed parallel Kenilworth road 26 m away
NAME_ALIGN_MIN_M = 30.0  # metres: close support must be sustained, but allow the reviewed 24th Street's offset at its junction
HPMS_EDGE_BUCKET_DEG = .001  # degrees: roughly 100 m cells prune US edge-distance checks
HPMS_RAMP_TYPE = 4  # FHWA Facility_Type code: ramp counts describe separate facilities, excluded from road groups
# https://www.fhwa.dot.gov/policyinformation/hpms/fieldmanual/page04.cfm (Item 3)
SIGNING = {"I": 2, "US": 3}   # HPMS route_signing: 2 Interstate, 3 US route, 4 state route (the data's I95/US9/NY 7 rows)
STATE_SIGNING = 4
BBOX_PAD_DEG = 0.0005   # degrees (about 45 m in latitude, 40 m in longitude): the box test before the exact distance
WORDS = {"ave": "avenue", "st": "street", "expy": "expressway"}   # abbreviations HPMS and OpenStreetMap spell both ways
US_WORDS = {"rd": "road", "blvd": "boulevard", "dr": "drive", "ln": "lane", "pkwy": "parkway",
            "hwy": "highway", "ter": "terrace", "ct": "court", "pl": "place", "cir": "circle",
            "fwy": "freeway", "tpke": "turnpike", "trl": "trail", "av": "avenue",
            "n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast",
            "nw": "northwest", "se": "southeast", "sw": "southwest"}  # spellings observed in the US join study
DC_QUADRANTS = frozenset(("northeast", "northwest", "southeast", "southwest"))
ROUTE_VARIANTS = {"bus": "business", "br": "business", "bl": "business",
                  "alt": "alternate", "byp": "bypass", "by": "bypass", "tr": "truck",
                  "hist": "historic", "unco": "unconstructed",
                  "svc": "service", "frtg": "frontage", "trk": "truck", "cnctr": "connector"}  # equivalent suffix spellings (NC writes TRK and CNCTR); all other suffixes stay literal
HPMS_VARIANT_WORDS = frozenset(ROUTE_VARIANTS) | frozenset(ROUTE_VARIANTS.values()) | frozenset(
    ("spur", "truck", "loop", "detour", "old", "upper", "lower", "cd", "connector", "couplet",
     "service", "frontage"))  # a frontage road coded with its highway's number is never the highway (WY rows 1678, 71408)
# These short codes only qualify an adjacent route token, not an arbitrary street name.
HPMS_NAME_VARIANTS = HPMS_VARIANT_WORDS - {"bl", "br", "by", "tr", "upper", "lower", "cd"}
HPMS_ROUTE_DIRECTIONS = frozenset("n s e w ne nw se sw north south east west northeast northwest southeast southwest nb sb eb wb nbound sbound ebound wbound northbound southbound eastbound westbound".split())  # HPMS route-name directions do not designate a separate route
# Signed lettered routes and separate Caltrans alignments verified in BTS HPMS
# 2024. State context keeps, for example, Delaware's US9W travel direction
# distinct from the signed US 9W in New Jersey and New York.
ROUTE_LETTERS = {
    (2, 5): {"s": ("CA",)},                              # Caltrans Postmile System: 005S
    (2, 35): {"e": ("MN", "TX"), "w": ("MN", "TX")},     # FHWA Interstate Route Numbering
    (2, 69): {"c": ("TX",), "e": ("TX",), "w": ("TX",)},  # TxDOT I-69 Corridor Designation Map
    (3, 9): {"w": ("NJ", "NY")},                        # NYSDOT Touring Route Number System
    (3, 25): {"e": ("KY", "TN", "VA"), "w": ("KY", "TN")},  # KYTC National Highway System
    (3, 31): {"e": ("KY", "TN"), "w": ("KY", "TN")},   # KYTC National Highway System
    (3, 49): {"e": ("MS",), "w": ("MS",)},               # MDOT US/MS Numbered Highways
    (4, 5): {"s": ("NY",)},                             # NYSDOT Touring Route Number System
    (4, 6): {"n": ("NY",)},
    (4, 9): {"n": ("NY",), "w": ("MS",)},
    (4, 12): {"e": ("NY",)},
    (4, 99): {"e": ("OR",), "w": ("OR",)},              # ODOT highway route designations
    (4, 178): {"s": ("CA",)},                           # Caltrans Postmile System: 178S
    (4, 200): {"s": ("MT",)},                           # Montana DOT Road Log: N-57A
    (4, 210): {"u": ("CA",)},                           # Caltrans Postmile System: 210U
}
# KYTC's centerline coding uses attached S for Spur, not South, on these routes.
KY_SPURS = frozenset(((3, 119), (3, 641), (4, 3), (4, 15), (4, 80), (4, 90), (4, 194)))
ATTACHED_ROUTE_LETTERS = frozenset(((2, 5), (4, 178), (4, 210)))  # Caltrans postmile suffixes
NATIONAL_INTERSTATE_LETTERS = frozenset((35, 69))  # reserve the branch numbers on refs without a state
SPACED_KY_SPURS = frozenset(((3, 641),))  # BTS names US-641 S with the S route_id qualifier
LETTER_VARIANTS = {"a": "alternate", "b": "business"}
# Compact variants in the pinned rows and the KYTC route-centerline code list.
STATE_SUFFIXES = {
    "GA": {"al": "alternate", "bu": "business", "lo": "loop", "sp": "spur"},
    "KY": {"a": "alternate", "b": "bypass", "c": "connector", "t": "truck", "x": "business"},
}
DC_ROUTE_DIRECTIONS = frozenset({"bn"})  # DC's undivided-route marker, not Business North
HPMS_STREET_TYPES = frozenset("avenue street road boulevard drive lane parkway highway terrace court place circle freeway turnpike trail way expressway".split())  # numeric street names require an explicit type in CA's route_id fallback
# HPMS A-codes -> Alaska state routes; Alaska DOT Table 8 (includes A1's short AK-4 concurrency):
# https://dot.alaska.gov/alaskamoves2050/docs/Appendix-D-Consolidated_Transportation-Assessment-August-2022.pdf
ALASKA_INTERSTATE_ROUTES = {"A1": frozenset((1, 2, 4)), "A2": frozenset((2,)),
                           "A3": frozenset((1,)), "A4": frozenset((3,))}
ALASKA_ALIAS_OVERLAP_M = 50.0  # metres of aligned support: an A-code at a junction alone cannot identify the road
ALASKA_ALIAS_OFFSET_M = 10.0  # metres: both HPMS edge endpoints must be this close to the OSM supporting line
ALASKA_ALIAS_ANGLE_DEG = 15.0  # degrees: maximum difference in edge direction for the crosswalk fallback
HPMS_YEAR = 2024  # vintage of the pinned BTS national file and count caches
US_CACHE_VERSION = 8  # version 8 recovers CA names without a street type; older cached rows omit them
US_STATES = tuple("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())  # 50 states and DC, no territories
ROUTE_TOKEN = re.compile(
    rf"(?:(?<!\w)(?P<prefix>u\.?s\.?(?:\s+(?:highway|hwy|route))?|"
    rf"i(?:nterstate)?|{'|'.join(US_STATES)}|sr)"
    rf"(?:\s+(?:highway|hwy))?\s*[-.]?\s*|(?<=/)\s*)(?P<number>[0-9]+)", re.I)
BARE_ROUTE = re.compile(r"^(?P<prefix>)(?P<number>[0-9]+)")
_ROUTE_SUFFIX_WORDS = "|".join(sorted(HPMS_VARIANT_WORDS, key=lambda s: (-len(s), s)))
JOINED_ROUTE_VARIANT = re.compile(
    rf"(?:(?:[nsew](?:b|bound)?)(?P<last>{_ROUTE_SUFFIX_WORDS})"
    rf"|(?P<first>{_ROUTE_SUFFIX_WORDS}|[ab])[nsew](?:b|bound)?)")
BARE_ROUTE_SUFFIX = re.compile(
    rf"(?:[a-z]+)?(?:\s+(?:[a-z]|{'|'.join(sorted(HPMS_ROUTE_DIRECTIONS))}|{_ROUTE_SUFFIX_WORDS}))*\s*", re.I)
_STATE_CLIPPERS = {}  # one latitude edge index per loaded Census state geometry, reused across regions


def _state_clipper(code, geometry):
    cached = _STATE_CLIPPERS.get(code)
    if cached is None or cached[0] is not geometry:
        cached = _STATE_CLIPPERS[code] = (geometry, GeometryClipper(geometry))
    return cached[1]


@lru_cache(maxsize=1)
def load_states() -> dict:
    """Frozen Census coverage; preparation is explicit, never downloaded during a build."""
    path = Path(__file__).with_name("us-states.json")
    if not path.is_file():
        raise RuntimeError("US regions are not frozen: run python3 scripts/roads/freeze_us.py first.")
    return json.loads(path.read_text(encoding="utf-8"))["states"]


def validate_file(path: Path) -> Path:
    """Check the national archive before reading rows or reusing a count cache."""
    path = Path(path).resolve()
    stat = path.stat()
    if stat.st_size != HPMS_FILE_BYTES:
        raise ValueError(f"HPMS file size mismatch: expected {HPMS_FILE_BYTES} bytes, got {stat.st_size}: {path}")
    digest = _file_digest(path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if digest != HPMS_FILE_SHA256:
        raise ValueError(f"HPMS file sha256 mismatch: expected {HPMS_FILE_SHA256}, got {digest}: {path}")
    return path


@lru_cache(maxsize=1)
def _file_digest(path, size, mtime_ns, ctime_ns):
    """Hash once per unchanged file in a build; stat values invalidate replaced files."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HPMS_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def state_cache(state: str, boxes: list, work: Path) -> Path:
    selection = json.dumps({"source": HPMS_SOURCE, "year": HPMS_YEAR, "sha256": HPMS_FILE_SHA256,
                            "boxes": sorted(boxes), "version": US_CACHE_VERSION}, separators=(",", ":"))
    digest = hashlib.sha256(selection.encode()).hexdigest()
    return Path(work) / f"hpms-bts-{state}-{HPMS_YEAR}-{digest}.json"


def fetch_report(state: str, boxes: list, work: Path) -> dict:
    path = state_cache(state, boxes, work)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))["report"]
    path = path.with_suffix(".error.json")
    return json.loads(path.read_text(encoding="utf-8"))["report"] if path.is_file() else {}


def _read_box(path: Path, state: str, box: list) -> list[dict]:
    """Build-only GDAL reader; OpenFileGDB exposes OBJECTID as the feature ID."""
    try:
        from pyogrio.raw import read
    except ImportError as exc:
        raise RuntimeError("Reading the HPMS file needs pyogrio from requirements-dev.txt.") from exc
    try:
        from shapely import force_2d, from_wkb, to_geojson
    except ImportError as exc:
        raise RuntimeError("Reading HPMS geometry needs shapely from requirements-dev.txt.") from exc
    meta, ids, geometries, fields = read(
        f"/vsizip/{path}/HPMS2024.gdb", layer=f"HPMS_FULL_{state}_{HPMS_YEAR}",
        bbox=tuple(box), columns=list(HPMS_COLUMNS), where="aadt > 0", force_2d=True, return_fids=True)
    rows = []
    for oid, geometry, values in zip(ids, geometries, zip(*fields)):
        props = {}
        for field, value in zip(meta["fields"], values):
            # GDAL uses numpy NaN for nullable numeric columns; JSON and the
            # truck-share calculation need ordinary Python values and None.
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, float) and not math.isfinite(value):
                value = None
            # The national file stores route numbers, signing and codes as doubles (278.0); the join reads
            # whole numbers (str(278.0) would make ".0" a route suffix).
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            props[HPMS_COLUMNS[field]] = value
        if not props.get("aadt") or props["aadt"] <= 0:
            continue
        route_id = props.pop("route_id", None)
        if not (props.get("route_name") or "").strip():
            props["route_name"] = _route_id_name(route_id, state, props.get("facility_type"))
        props["objectid"] = int(oid)
        geom = json.loads(to_geojson(force_2d(from_wkb(geometry)))) if geometry is not None else None
        rows.append({"type": "Feature", "properties": props, "geometry": geom})
    return rows


def _route_id_name(route_id: str | None, state: str, facility_type) -> str | None:
    """Only CA's reviewed jurisdiction/street/_P form supplies a missing name.

    Other states' IDs are opaque codes or need separate parsing. CA's SHS and
    RAMP identifiers also stay unnamed; a street name still needs alignment.
    """
    if state != "CA" or facility_type == HPMS_RAMP_TYPE or not route_id or route_id.upper().startswith("RAMP_"):
        return None
    if not re.fullmatch(r"[A-Z]+_[A-Z]+_.+_P", route_id):
        return None
    name = normalize_us_name(route_id.removesuffix("_P"), state)
    words = name.split()
    street = words[:-1] if words and words[-1] in HPMS_ROUTE_DIRECTIONS else words
    if "ramp" in words or len(street) < 2:
        return None
    # Numbered streets (10th Street) are names; embedded alphanumeric IDs are not.
    if any(re.search(r"\d", word) and not re.fullmatch(r"\d+(?:st|nd|rd|th)?", word) for word in words):
        return None
    if any(word.isdecimal() for word in street) and street[-1] not in HPMS_STREET_TYPES:
        return None  # an ID such as ROAD 123456 is not a numbered street
    return name


def fetch_state(state: str, boxes: list[list[float]], work: Path, hpms_file: Path) -> list[dict]:
    """BTS 2024 counts in the state's boxes, ordered and deduplicated by OBJECTID.

    Each box is one local read. Only a complete state is cached; failures retain
    the per-state error report without presenting partial coverage as success.
    """
    if state not in US_STATES:
        raise ValueError(f"Not a US state or DC: {state}")
    path = validate_file(hpms_file)
    cache = state_cache(state, boxes, work)
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))["features"]
    cache.parent.mkdir(parents=True, exist_ok=True)
    rows, pages, expected_rows = {}, 0, 0
    source = {"source": HPMS_SOURCE, "year": HPMS_YEAR}
    try:
        for box in sorted(boxes):
            pages += 1  # retain the report field; a page now means one bbox read
            got = _read_box(path, state, box)
            expected_rows += len(got)
            for row in got:
                rows.setdefault(row["properties"]["objectid"], row)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        report = {**source, "pages": pages, "rows": len(rows), "status": "error", "error": f"HPMS {state}: {exc}"}
        cache.with_suffix(".error.json").write_text(json.dumps({"report": report}), encoding="utf-8")
        raise RuntimeError(report["error"]) from exc
    features = [rows[oid] for oid in sorted(rows)]
    report = {**source, "pages": pages, "rows": len(features), "expected_query_rows": expected_rows, "status": "ok"}
    temporary = cache.with_suffix(".tmp")
    temporary.write_text(json.dumps({"features": features, "report": report}, separators=(",", ":")), encoding="utf-8")
    temporary.replace(cache)
    cache.with_suffix(".error.json").unlink(missing_ok=True)
    return features


def fetch(state: str, box: list[float], work: Path, hpms_file: Path) -> list[dict]:
    """The metro uses the same pinned source, normalization and cache as the US build."""
    return fetch_state(state, [box], work, hpms_file)


def norm_name(name: str | None) -> str:
    """A road name to compare: no case, no punctuation, no leading "the", "Ave" = "Avenue", "St" = "Street", "Expy" = "Expressway"."""
    words = [WORDS.get(w, w) for w in re.findall(r"[^\W_]+", (name or "").casefold())]
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def _route_suffix(tail, system, number, state, *, qualifier="", strict=False):
    """Read only a route's suffix, not the adjoining street name."""
    attached = bool(tail and tail[0].isalpha())
    key = (system, number)
    letters = {word for word, regions in ROUTE_LETTERS.get(key, {}).items()
               if (not state or state in regions or system == SIGNING["I"] and number in NATIONAL_INTERSTATE_LETTERS)
               and (attached or key not in ATTACHED_ROUTE_LETTERS)}
    # Louisiana uses a second, hyphenated number for separate state highways.
    extension = re.match(r"^-(\d+)\b", tail) if state == "LA" and system == STATE_SIGNING else None
    tail = re.sub(r"\b(north|south|east|west|[nsew])\s+bound\b", r"\1bound", tail.casefold())
    words = re.findall(r"[a-z]+", tail)
    if state == "DC" and system == SIGNING["I"] and words == ["i", "bn"]:
        words = words[1:]  # some DC Interstate labels repeat the system designation
    if words and words[0][:1] in letters and words[0][1:] in HPMS_ROUTE_DIRECTIONS | HPMS_VARIANT_WORDS:
        words[:1] = [words[0][0], words[0][1:]]
    suffix = ["-" + extension[1]] if extension else []
    variants, rest = [qualifier] if qualifier else [], []
    for i, word in enumerate(words):
        if i == 0 and word in letters:
            suffix.append(word)
        elif i == 0 and word == "s" and state == "KY" and (system, number) in KY_SPURS and (attached or (system, number) in SPACED_KY_SPURS):
            variants.append("spur")
        elif word in HPMS_ROUTE_DIRECTIONS or state == "DC" and word in DC_ROUTE_DIRECTIONS:
            continue
        elif i == 0 and (variant := STATE_SUFFIXES.get(state, {}).get(word)):
            variants.append(variant)
        elif word in HPMS_VARIANT_WORDS:
            variants.append(ROUTE_VARIANTS.get(word, word))
        elif i == 0 and word in LETTER_VARIANTS:
            if state == "OR" and (system, number, word) == (3, 30, "b"):
                variants.append("bypass")
            elif system == STATE_SIGNING and state != "DE":
                suffix.append(word)  # e.g. NY 9A, NH 28B
            else:
                variants.append(LETTER_VARIANTS[word])
        elif (compound := JOINED_ROUTE_VARIANT.fullmatch(word)) and (not compound["first"] in LETTER_VARIANTS or i == 0 and attached):
            variant = compound["last"] or compound["first"]
            variants.append(ROUTE_VARIANTS.get(variant, LETTER_VARIANTS.get(variant, variant)))
        elif i == 0 and (attached or len(word) == 1):
            suffix.append(word)  # keep unrecognized attached letters distinct from mainline
        elif strict and word not in {"route", "highway"}:
            rest.append(word)
        else:
            break
    return " ".join(dict.fromkeys(suffix + sorted(set(variants)) + rest)), variants


def _route_tokens(value, *, system=None, state="", name=False, legacy_state=False):
    """Parse signed tokens, or a bare number when its signing supplies the system."""
    value = str(value or "").strip()
    if not value:
        return []
    tokens = list(ROUTE_TOKEN.finditer(value))
    if system is not None and (bare := BARE_ROUTE.match(value)):
        tokens.insert(0, bare)
    routes = []
    inherited = None
    for i, token in enumerate(tokens):
        prefix = re.sub(r"[^a-z]", "", (token["prefix"] or "").casefold())
        if prefix == "sr" and not legacy_state:
            continue  # OSM's generic SR is accepted only for the legacy first ref
        if prefix == "interstate":
            prefix = "i"
        elif prefix.startswith("us"):
            prefix = "us"
        if prefix:
            sign = SIGNING.get(prefix.upper(), STATE_SIGNING)
            # An explicit state prefix supplies context for state route letters.
            region = prefix.upper() if prefix.upper() in US_STATES else state
            inherited = (sign, region)
        elif token.start():
            if inherited is None:
                continue  # a slash-number only inherits an explicit route prefix
            sign, region = inherited
        else:
            sign, region = system, state
        number = int(token["number"])
        if not number:
            continue
        before = value[:token.start()].casefold() if i == 0 else ""
        leading = re.search(r"\b([a-z]+)[\s-]*$", before)
        qualifier = leading[1] if leading and leading[1] in HPMS_VARIANT_WORDS else ""
        qualifier = ROUTE_VARIANTS.get(qualifier, qualifier)
        end = tokens[i + 1].start() if i + 1 < len(tokens) else len(value)
        tail = re.split(r"[/,;]", value[token.end():end], maxsplit=1)[0]
        if name and not prefix and token.start() == 0 and len(tokens) > 1:
            continue
        if name and (re.match(r"(?i)^(?:st|nd|rd|th)\b", tail)
                     or not prefix and tail.strip() and not BARE_ROUTE_SUFFIX.fullmatch(tail)):
            continue  # a street number is not a route number
        if not name and (before.strip() and before.strip(" -") != (leading[1] if qualifier else None)):
            continue  # e.g. CR NY 12 is not a state ref
        suffix, variants = _route_suffix(tail, sign, number, region, qualifier=qualifier, strict=not name)
        route = (sign, number, suffix) if suffix else (sign, number)
        routes.append((route, variants))
    return routes


def route_of_ref(ref: str, *, legacy_state: bool = False, state: str = "") -> tuple | None:
    """Parse an OSM ref without treating an arbitrary prefix as a state route."""
    routes = _route_tokens(ref, legacy_state=legacy_state, state=state)
    return routes[0][0] if len(routes) == 1 else None


def normalize_ca_name(name: str) -> str:
    """Remove HPMS's two uppercase jurisdiction tokens, e.g. LA_CO_ or ALA_OAK_."""
    return re.sub(r"^[A-Z]+_[A-Z]+_", "", name)


def normalize_ak_name(name: str) -> str:
    """Remove HPMS's parenthetical locality, e.g. Chiniak Highway (Kodiak)."""
    return re.sub(r"\s+\([^()]*\)\s*$", "", name)


def normalize_us_name(name: str | None, state: str) -> str:
    """Comparable street spelling; keep directions except for DC's pairwise quadrant rule."""
    name = name or ""
    if state == "CA":
        name = normalize_ca_name(name)
    elif state == "AK":
        name = normalize_ak_name(name)
    words = norm_name(name).split()
    return " ".join(w if i == 0 and w in {"n", "s", "e", "w"} and len(words) > 1
                    and words[1] in {"street", "road", "avenue", "drive", "lane", "court", "place"}
                    else US_WORDS.get(w, w) for i, w in enumerate(words))


def normalize_dc_name(name: str) -> str:
    """Omit one trailing quadrant/BN from an already normalized DC street name."""
    words = name.split()
    if words and words[-1] in DC_QUADRANTS | {"bn"}:
        words.pop()
    return " ".join(words)


def us_names_match(name: str, other: str, state: str) -> bool:
    """Match normalized names; DC may omit a quadrant, but explicit conflicts stay distinct."""
    if not name or not other:
        return False
    if name == other:
        return True
    if state != "DC":
        return False
    ours, theirs = set(name.split()) & DC_QUADRANTS, set(other.split()) & DC_QUADRANTS
    if ours and theirs and ours != theirs:
        return False
    base = normalize_dc_name(name)
    return bool(base) and base == normalize_dc_name(other)


def us_road_identity(road: dict, state: str) -> dict:
    """All recognized refs and state-specific names, prepared once per group."""
    refs = road.get("refs", [])
    routes = {route for ref in refs if (route := route_of_ref(ref, state=state)) is not None}
    if refs and (first := route_of_ref(refs[0], legacy_state=True, state=state)) is not None:
        routes.add(first)  # preserve the study's existing SR-40/SR-17 matches, without admitting arbitrary prefixes
    ak_numbers = {route[1] for ref in refs if re.match(r"^AK\s*-?\s*\d", ref.strip(), re.I)
                  and (route := route_of_ref(ref, state=state)) is not None and len(route) == 2}
    return {"routes": routes, "name": normalize_us_name(road["name"], state),
            "legacy_name": norm_name(road["name"]),
            "alaska_interstates": {code for code, nums in ALASKA_INTERSTATE_ROUTES.items()
                                   if state == "AK" and nums & ak_numbers}}


def us_match_kind(identity: dict, segment: dict, state: str) -> str | None:
    """Identify a candidate; Alaska aliases still need aligned geometry before joining."""
    if segment["route"] is not None and segment["route"] in identity["routes"]:
        return "route"
    if _route_conflicts(identity["routes"], segment):
        return None  # a shared street name cannot override an explicit mainline/variant conflict
    if ((identity["legacy_name"] and identity["legacy_name"] == segment["name"])
            or us_names_match(identity["name"], segment["us_name"], state)):
        return "name"
    if state == "AK" and segment["alaska_interstate"] in identity["alaska_interstates"]:
        return "alaska"
    return None


def _route_conflicts(routes, segment):
    """Name matching cannot erase an explicit mainline/variant distinction."""
    route = segment["route"]
    if route is not None:
        return route not in routes and any(ref[:2] == route[:2] for ref in routes)
    return bool(segment["variant"]) and any(
        len(ref) == 2 or ref[2] in ROUTE_LETTERS.get(ref[:2], {})
        for ref in routes)  # a signed lettered route can also have a separate business or bypass route


def _lines(geom: dict | None) -> list[list]:
    if not geom:
        return []
    if geom.get("type") == "LineString":
        return [geom["coordinates"]]
    if geom.get("type") == "MultiLineString":
        return list(geom["coordinates"])
    return []


def _trucks_pct(p: dict) -> int | None:
    """The share of trucks (combination plus single-unit) in the AADT, in whole percent; None when HPMS gives neither."""
    su, comb = p.get("aadt_single_unit"), p.get("aadt_combination")
    if su is None and comb is None:
        return None
    return round(100 * ((su or 0) + (comb or 0)) / p["aadt"])


@lru_cache(maxsize=16384)  # repeated label/number tuples within a build
def _hpms_route(sign, number, name, state=""):
    """Resolve the row's own route, without adopting a concurrently named route."""
    numbered = _route_tokens(number, system=sign, state=state, legacy_state=True) if sign else []
    named = _route_tokens(name, system=sign, state=state, name=True, legacy_state=True)
    numbered = [route for route, _ in numbered if route[0] == sign]
    named = [(route, variants) for route, variants in named if route[0] == sign]
    route = numbered[0] if numbered else None
    if route:
        named = [(ref, variants) for ref, variants in named if ref[:2] == route[:2]]
    elif sign not in (*SIGNING.values(), STATE_SIGNING) or len({ref[:2] for ref, _ in named}) != 1:
        named = []
    specific = {ref for ref, _ in named if len(ref) == 3}
    if len(specific) == 1:
        route = next(iter(specific))
    elif route is None and named and not specific:
        route = named[0][0]
    # Keep the existing name-match guard when a variant is explicit but no
    # usable route token carries it, e.g. "Bayshore Freeway Business".
    if not named:
        variants = {ROUTE_VARIANTS.get(word, word)
                    for value in (name, number) for word in re.findall(r"[a-z]+", str(value or "").casefold())
                    if word in HPMS_NAME_VARIANTS}
        if route and variants:
            words = route[2].split() if len(route) == 3 else []
            route = (*route[:2], " ".join(dict.fromkeys(words + sorted(variants))))
    else:
        variants = {word for _, qualifiers in named for word in qualifiers}
    return route, bool(variants)


def _prepare(segments: list, state: str = "") -> list[dict]:
    """Segments that have a count and a line: {lines, box, route, name, aadt, trucks_pct}."""
    out = []
    for s in segments:
        p = s.get("properties") or {}
        if p.get("facility_type") == HPMS_RAMP_TYPE:
            continue
        lines = _lines(s.get("geometry"))
        if not p.get("aadt") or p["aadt"] <= 0 or not lines:
            continue
        pts = [c for line in lines for c in line]
        route, variant = _hpms_route(p.get("route_signing"), p.get("route_number"), p.get("route_name"),
                                     s.get("hpms_state", state))
        out.append({"lines": lines, "aadt": int(p["aadt"]), "trucks_pct": _trucks_pct(p), "name": norm_name(p.get("route_name")),
                    "route": route, "variant": variant,
                    "box": (min(c[0] for c in pts), min(c[1] for c in pts), max(c[0] for c in pts), max(c[1] for c in pts))})
    return out


def _along(edges: list, seg: dict, within_m: float) -> tuple[float, float]:
    """(metres of the road's edges whose middle is within within_m of the segment, the sum of those distances)."""
    west, south, east, north = seg["box"]
    metres = dist = 0.0
    for lon, lat, ln in edges:
        if not (west - BBOX_PAD_DEG <= lon <= east + BBOX_PAD_DEG and south - BBOX_PAD_DEG <= lat <= north + BBOX_PAD_DEG):
            continue
        to_xy = local_xy(lat, lon)
        best = min((seg_distance((0.0, 0.0), to_xy(*a[:2]), to_xy(*b[:2]))[0]
                    for line in seg["lines"] for a, b in zip(line, line[1:])), default=math.inf)
        if best <= within_m:
            metres += ln
            dist += best
    return metres, dist


def join(roads: dict, features: list, segments: list, within_m: float = HPMS_JOIN_M) -> int:
    """Set aadt and trucks_pct on each road group from the HPMS segment that is the same road and runs along it.

    Same road: the segment's route (signing and number) is the group's main route number (its first ref), or its
    name equals the group's name (see norm_name).
    Of the matching segments, the one running within within_m of most of the group's length is taken; a group with
    none gets nothing. Every candidate also needs sustained HPMS alignment (and
    close support for name-only matches). The chosen stretch's count applies to the whole group in this file,
    not necessarily the stretch near a home. Returns the number of groups joined."""
    segs = _prepare(segments)
    by_route, by_name = {}, {}
    for s in segs:
        if s["route"]:
            by_route.setdefault(s["route"], []).append(s)
        if s["name"]:
            by_name.setdefault(s["name"], []).append(s)
    edges = {}   # group key -> (middle longitude, middle latitude, length in metres) of each of its edges
    line_edges = defaultdict(list)
    for f in features:
        g = f["properties"].get("g")
        if g not in roads:
            continue
        c = f["geometry"]["coordinates"]
        for a, b in zip(c, c[1:]):
            line_edges[g].append((a, b))
            edges.setdefault(g, []).append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, haversine_m(a[1], a[0], b[1], b[0])))
    joined = 0
    for key, road in roads.items():
        cands = {}
        routes = us_road_identity(road, "")["routes"]
        route = route_of_ref(road["refs"][0], legacy_state=True) if road.get("refs") else None
        for s in (by_route.get(route, []) if route else []) + by_name.get(norm_name(road["name"]), []):
            cands[id(s)] = s
        scored = []
        for s in cands.values():
            if _route_conflicts(routes, s):
                continue
            metres, dist = _along(edges.get(key, []), s, within_m)
            if metres > 0 and _runs_along(line_edges[key], s, within_m, route == s["route"] and route is not None):
                scored.append((-metres, dist, s["aadt"], s))
        if scored:
            best = min(scored, key=lambda t: t[:3])[3]
            road["aadt"], road["trucks_pct"] = best["aadt"], best["trucks_pct"]
            joined += 1
    return joined


def _edge_distance(a, b, c, d):
    """Minimum distance between two line segments in local metres, including crossings."""
    xy = local_xy(a[1], a[0])
    a, b, c, d = (xy(*p[:2]) for p in (a, b, c, d))
    dx, dy, ex, ey = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    cross = dx * ey - dy * ex
    if cross:
        t = ((c[0] - a[0]) * ey - (c[1] - a[1]) * ex) / cross
        u = ((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / cross
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.
    return min(seg_distance(a, c, d)[0], seg_distance(b, c, d)[0],
               seg_distance(c, a, b)[0], seg_distance(d, a, b)[0])


def _prepare_us(rows, state=""):
    """Malformed or empty count rows cannot supply a nearest valid match."""
    prepared = []
    for row in rows:
        try:
            oid = (row.get("properties") or {}).get("objectid", row.get("id"))
            if type(oid) is int:
                for segment in _prepare([row], state):
                    segment["objectid"] = oid
                    p = row.get("properties") or {}
                    segment["us_name"] = normalize_us_name(p.get("route_name"), state)
                    segment["alaska_interstate"] = (p.get("route_name") if state == "AK"
                                                      and p.get("route_signing") == 2 else None)
                    prepared.append(segment)
        except (TypeError, ValueError, OverflowError, IndexError, ZeroDivisionError):
            continue
    return prepared


def _aligned_overlap(a, b, c, d) -> float:
    """Projected overlap for one Alaska alias edge pair, in _edge_distance's affine metric."""
    xy = local_xy(a[1], a[0])
    b, c, d = (xy(*p[:2]) for p in (b, c, d))
    length = math.hypot(*b)
    ex, ey = d[0] - c[0], d[1] - c[1]
    other_length = math.hypot(ex, ey)
    if not length or not other_length:
        return 0.0
    cosine = abs(b[0] * ex + b[1] * ey) / (length * other_length)
    if cosine < math.cos(math.radians(ALASKA_ALIAS_ANGLE_DEG)):
        return 0.0
    offsets = [abs(b[0] * p[1] - b[1] * p[0]) / length for p in (c, d)]
    if max(offsets) > ALASKA_ALIAS_OFFSET_M:
        return 0.0
    projections = [(p[0] * b[0] + p[1] * b[1]) / length for p in (c, d)]
    return max(0.0, min(length, max(projections)) - max(0.0, min(projections)))


def _alignment_interval(a, b, c, d, within_m=HPMS_JOIN_M):
    """Metre interval on HPMS c–d alongside OSM a–b, or None.

    Clip to the OSM edge's longitudinal extent and distance strip, with a local
    direction-free bearing check. Endpoint proximity alone supplies no length.
    Each HPMS edge uses its own fixed metric so intervals from overlapping OSM
    edges can be unioned without counting the same stretch twice.
    """
    xy = local_xy(c[1], c[0])
    a, b, d = (xy(*p[:2]) for p in (a, b, d))
    vx, vy = b[0] - a[0], b[1] - a[1]
    road_length, length = math.hypot(vx, vy), math.hypot(*d)
    if not road_length or not length:
        return None
    if abs(vx * d[0] + vy * d[1]) / (road_length * length) <= math.cos(math.radians(ALIGN_MAX_DEG)):
        return None
    lo, hi = 0.0, 1.0
    # HPMS c is the origin. Clip its parameter to the road's rectangular strip.
    for start, delta, lower, upper in (
        ((-a[0] * vx - a[1] * vy) / road_length, (d[0] * vx + d[1] * vy) / road_length, 0.0, road_length),
        ((-vx * a[1] + vy * a[0]) / road_length, (vx * d[1] - vy * d[0]) / road_length, -within_m, within_m),
    ):
        if delta == 0:
            if not lower <= start <= upper:
                return None
        else:
            first, last = sorted(((lower - start) / delta, (upper - start) / delta))
            lo, hi = max(lo, first), min(hi, last)
    return (lo * length, hi * length) if hi > lo else None


def _support_length(intervals):
    """Union intervals per HPMS edge; duplicate OSM pieces add no support."""
    total = 0.0
    for parts in intervals.values():
        end = 0.0
        for lo, hi in sorted(parts):
            total += max(0.0, hi - max(end, lo))
            end = max(end, hi)
    return total


def _alignment_required(segment):
    length = sum(math.hypot(*local_xy(c[1], c[0])(*d[:2]))
                 for line in segment["lines"] for c, d in zip(line, line[1:]))
    return max(ALIGN_FLOOR_M, min(ALIGN_MIN_M, ALIGN_MIN_SHARE * length))


def _runs_along(edges, segment, within_m, route_match):
    """The metro path uses the same alignment guard as the national path."""
    support, close_support = defaultdict(list), defaultdict(list)
    for line in segment["lines"]:
        for c, d in zip(line, line[1:]):
            key = (tuple(c), tuple(d))
            for a, b in edges:
                interval = _alignment_interval(a, b, c, d, min(within_m, HPMS_JOIN_M))
                if interval:
                    support[key].append(interval)
                if not route_match:
                    interval = _alignment_interval(a, b, c, d, min(within_m, NAME_ALIGN_M))
                    if interval:
                        close_support[key].append(interval)
    required = _alignment_required(segment)
    return (required > 0 and _support_length(support) >= required
            and (route_match or _support_length(close_support) >= min(NAME_ALIGN_MIN_M, required)))


class _EdgeGrid:
    """HPMS edges in geographic buckets; exact scoring still uses local metres."""
    def __init__(self, segments):
        self.cells, self.edges = defaultdict(list), []
        for seg in segments:
            for line in seg["lines"]:
                for c, d in zip(line, line[1:]):
                    box = (min(c[0], d[0]), min(c[1], d[1]), max(c[0], d[0]), max(c[1], d[1]))
                    idx = len(self.edges)
                    self.edges.append((c, d, box, seg))
                    for cell in self._cells(box):
                        self.cells[cell].append(idx)

    @staticmethod
    def _cells(box):
        w, s, e, n = (math.floor(v / HPMS_EDGE_BUCKET_DEG) for v in box)
        for i in range(w, e + 1):
            for j in range(s, n + 1):
                yield i, j

    def nearby(self, a, b):
        # Match _edge_distance's affine projection at a's latitude. Expanding
        # both axes cannot exclude an edge within HPMS_JOIN_M in that metric.
        metres_per_deg = math.pi / 180 * EARTH_R_M
        dy = HPMS_JOIN_M / metres_per_deg
        dx = dy / math.cos(math.radians(a[1]))
        w, s, e, n = (min(a[0], b[0]) - dx, min(a[1], b[1]) - dy,
                       max(a[0], b[0]) + dx, max(a[1], b[1]) + dy)
        found = set()
        for cell in self._cells((w, s, e, n)):
            found.update(self.cells.get(cell, ()))
        for idx in sorted(found):
            c, d, box, seg = self.edges[idx]
            if box[0] <= e and box[2] >= w and box[1] <= n and box[3] >= s:
                yield c, d, seg


def join_by_state(roads: dict, features: list, segments: dict[str, list[dict]]) -> dict[str, dict]:
    """Measure eligibility in frozen coverage; publish one nearest valid match.

    Frozen coverage is simplified and buffered, so border eligibility can overlap.
    Such groups use their geometry from all eligible states against each state's
    own HPMS rows. Each state's report includes the group, with border_groups
    naming this duplication. One nearest valid count is published for the whole group in this file, never a sum
    or a count selected for the stretch near a home.
    Identity uses all recognized refs or normalized names. Every candidate needs
    aligned support; name-only matches need closer support to exclude parallel
    namesakes. Alaska's explicit Interstate crosswalk has its additional guard.
    At equal distances, route matches win over name-only matches, then lower object IDs win.
    """
    states = load_states()
    best, report = {}, {}
    for road in roads.values():
        road["aadt"] = road["trucks_pct"] = None
    state_edges, group_states = {}, defaultdict(set)
    for state in segments:
        local = _state_clipper(state, states[state]["geometry"]).clip(features) if roads else []
        edges = state_edges[state] = {}
        for f in local:
            key = f["properties"].get("g")
            if key in roads:
                line = f["geometry"]["coordinates"]
                edges.setdefault(key, []).extend(zip(line, line[1:]))
                group_states[key].add(state)
    # Union of eligible coverage preserves holes while avoiding a simplified
    # border clipping off the part that has the neighboring state's valid count.
    group_edges = defaultdict(list)
    for edges in state_edges.values():
        for key, parts in edges.items():
            group_edges[key].extend(parts)
    for state, rows in segments.items():
        edges = state_edges[state]
        grid = _EdgeGrid(_prepare_us(rows, state))
        joined = 0
        for key in edges:
            road = roads[key]
            identity, nearest = us_road_identity(road, state), None
            kinds, candidates, support = {}, {}, defaultdict(lambda: defaultdict(list))
            name_support, alias_support = defaultdict(lambda: defaultdict(list)), defaultdict(dict)
            for a, b in group_edges[key]:
                edge = (tuple(a), tuple(b))
                for c, d, seg in grid.nearby(a, b):
                    oid = seg["objectid"]
                    if oid not in kinds:
                        kinds[oid] = us_match_kind(identity, seg, state)
                    kind = kinds[oid]
                    if kind is None:
                        continue
                    distance = _edge_distance(a, b, c, d)
                    if distance > HPMS_JOIN_M:
                        continue
                    hpms_edge = (tuple(c), tuple(d))
                    interval = _alignment_interval(a, b, c, d)
                    if interval:
                        support[oid][hpms_edge].append(interval)
                    if kind == "name":
                        interval = _alignment_interval(a, b, c, d, NAME_ALIGN_M)
                        if interval:
                            name_support[oid][hpms_edge].append(interval)
                    if kind == "alaska":
                        # Max per OSM edge avoids counting duplicate HPMS vertices twice.
                        alias_support[oid][edge] = max(alias_support[oid].get(edge, 0.0), _aligned_overlap(a, b, c, d))
                    if oid not in candidates or distance < candidates[oid][0]:
                        candidates[oid] = distance, seg
            for oid, (distance, seg) in candidates.items():
                kind = kinds[oid]
                required = _alignment_required(seg)
                if not required or _support_length(support[oid]) < required:
                    continue
                if kind == "name" and _support_length(name_support[oid]) < min(NAME_ALIGN_MIN_M, required):
                    continue
                if kind == "alaska" and sum(alias_support[oid].values()) < ALASKA_ALIAS_OVERLAP_M:
                    continue
                score = (distance, kind == "name", oid)
                if nearest is None or score < nearest[0]:
                    nearest = score, seg
            if nearest is not None:
                match_score, seg = nearest
                joined += 1
                score = (*match_score, state)  # states can reuse object IDs
                if key not in best or score < best[key][0]:
                    best[key] = (score, seg)
        eligible = len(edges)
        report[state] = {"eligible_groups": eligible, "joined_groups": joined,
                         "border_groups": sum(len(group_states[key]) > 1 for key in edges),
                         "join_rate": joined / eligible if eligible else None, "status": "ok",
                         "pages": 0, "rows": len(rows)}  # fetch_state's report supplies bbox read counts
    for key, (_, seg) in best.items():
        roads[key]["aadt"], roads[key]["trucks_pct"] = seg["aadt"], seg["trucks_pct"]
    return report
