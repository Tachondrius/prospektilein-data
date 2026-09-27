#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import osmium


TILE_FACTOR = 100.0

NUTS2_WFS_URL = "https://www.statistik.gv.at/gs-open/GEODATA/ows"
NUTS2_TYPE_NAME = "GEODATA:STATISTIK_AUSTRIA_NUTS2_20210101"

STATE_BY_NUTS2 = {
    "AT11": ("AT-1", "Burgenland"),
    "AT12": ("AT-3", "Niederösterreich"),
    "AT13": ("AT-9", "Wien"),
    "AT21": ("AT-2", "Kärnten"),
    "AT22": ("AT-6", "Steiermark"),
    "AT31": ("AT-4", "Oberösterreich"),
    "AT32": ("AT-5", "Salzburg"),
    "AT33": ("AT-7", "Tirol"),
    "AT34": ("AT-8", "Vorarlberg"),
}

# Häufige österreichische Handelsmarken.
# Wichtig: längere/spezifischere Namen stehen vor allgemeineren Namen.
KNOWN_RETAILERS = [
    ("billa plus", "billa_plus"),
    ("billa corso", "billa"),
    ("billa", "billa"),
    ("eurospar", "eurospar"),
    ("interspar", "interspar"),
    ("spar gourmet", "spar"),
    ("spar express", "spar"),
    ("spar", "spar"),
    ("penny", "penny"),
    ("hofer", "hofer"),
    ("lidl", "lidl"),
    ("dm drogerie markt", "dm"),
    ("dm", "dm"),
    ("bipa", "bipa"),
    ("muller", "mueller"),
    ("action", "action"),
    ("tedi", "tedi"),
    ("kik", "kik"),
    ("nkd", "nkd"),
    ("takko", "takko"),
    ("mediamarkt", "mediamarkt"),
    ("media markt", "mediamarkt"),
    ("saturn", "saturn"),
    ("obi", "obi"),
    ("hornbach", "hornbach"),
    ("bauhaus", "bauhaus"),
    ("hagebau", "hagebau"),
    ("xxxlutz", "xxxlutz"),
    ("momax", "moemax"),
    ("mömax", "moemax"),
    ("möbelix", "moebelix"),
    ("moebelix", "moebelix"),
    ("ikea", "ikea"),
    ("fressnapf", "fressnapf"),
    ("dehner", "dehner"),
    ("libro", "libro"),
    ("pagro", "pagro"),
    ("pepco", "pepco"),
    ("woolworth", "woolworth"),
    ("hervis", "hervis"),
    ("intersport", "intersport"),
    ("humanic", "humanic"),
    ("deichmann", "deichmann"),
    ("c&a", "c_and_a"),
    ("h&m", "h_and_m"),
    ("zara", "zara"),
    ("mpreis", "mpreis"),
    ("unimarkt", "unimarkt"),
    ("nah&frisch", "nah_und_frisch"),
    ("nah und frisch", "nah_und_frisch"),
    ("adeg", "adeg"),
    ("denn's biomarkt", "denns"),
    ("denns biomarkt", "denns"),
]


def normalize_for_match(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("+", " plus ")
    value = value.replace("&", " and ")
    value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = normalize_for_match(value)
    value = value.replace(" and ", "_und_")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def canonical_retailer(tags: dict[str, str]) -> str:
    # Name zuerst: OSM kann z. B. name=INTERSPAR, brand=SPAR enthalten.
    candidates = [
        tags.get("name", ""),
        tags.get("brand", ""),
        tags.get("operator", ""),
    ]

    for candidate in candidates:
        if not candidate:
            continue

        normalized = normalize_for_match(candidate)

        for alias, retailer in KNOWN_RETAILERS:
            alias_normalized = normalize_for_match(alias)
            if (
                normalized == alias_normalized
                or normalized.startswith(alias_normalized + " ")
            ):
                return retailer

    fallback = next(
        (
            value
            for value in candidates
            if value and value.strip()
        ),
        "",
    )

    if fallback:
        return slugify(fallback)

    shop_type = tags.get("shop", "shop")
    return f"shop_{slugify(shop_type)}"


def display_name(tags: dict[str, str]) -> str:
    for key in ("name", "brand", "operator"):
        value = tags.get(key, "").strip()
        if value:
            return value

    shop_type = tags.get("shop", "shop")
    return shop_type.replace("_", " ").strip().title()


def representative_point_from_nodes(nodes) -> tuple[float, float] | None:
    coordinates: list[tuple[float, float]] = []

    for node in nodes:
        try:
            if node.location.valid():
                coordinates.append((node.location.lat, node.location.lon))
        except Exception:
            continue

    if not coordinates:
        return None

    lat = sum(item[0] for item in coordinates) / len(coordinates)
    lon = sum(item[1] for item in coordinates) / len(coordinates)
    return lat, lon


class ShopHandler(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.shops: list[dict] = []
        self.seen_sources: set[str] = set()

    def _add(
        self,
        tags,
        latitude: float,
        longitude: float,
        source: str,
    ) -> None:
        if source in self.seen_sources:
            return

        copied_tags = dict(tags)
        shop_type = copied_tags.get("shop", "").strip()

        if not shop_type:
            return

        if shop_type.lower() in {"no", "vacant"}:
            return

        if not (-90.0 <= latitude <= 90.0):
            return

        if not (-180.0 <= longitude <= 180.0):
            return

        self.seen_sources.add(source)

        entry = {
            "name": display_name(copied_tags),
            "retailer": canonical_retailer(copied_tags),
            "shop": shop_type,
            "latitude": round(latitude, 7),
            "longitude": round(longitude, 7),
            "_source": source,
        }

        brand = copied_tags.get("brand", "").strip()
        if brand:
            entry["brand"] = brand

        postcode = copied_tags.get("addr:postcode", "").strip()
        if postcode:
            entry["postcode"] = postcode

        city = copied_tags.get("addr:city", "").strip()
        if city:
            entry["city"] = city

        self.shops.append(entry)

    def node(self, node) -> None:
        if "shop" not in node.tags:
            return

        if not node.location.valid():
            return

        self._add(
            node.tags,
            node.location.lat,
            node.location.lon,
            f"n{node.id}",
        )

    def way(self, way) -> None:
        if "shop" not in way.tags:
            return

        point = representative_point_from_nodes(way.nodes)
        if point is None:
            return

        self._add(
            way.tags,
            point[0],
            point[1],
            f"w{way.id}",
        )

    def area(self, area) -> None:
        if "shop" not in area.tags:
            return

        source_prefix = "w" if area.from_way() else "r"
        source = f"{source_prefix}{area.orig_id()}"

        # Ein geschlossener Way wurde bereits im way()-Callback erfasst.
        if source in self.seen_sources:
            return

        coordinates: list[tuple[float, float]] = []

        for outer_ring in area.outer_rings():
            for node in outer_ring:
                try:
                    if node.location.valid():
                        coordinates.append(
                            (node.location.lat, node.location.lon)
                        )
                except Exception:
                    continue

        if not coordinates:
            return

        latitude = sum(item[0] for item in coordinates) / len(coordinates)
        longitude = sum(item[1] for item in coordinates) / len(coordinates)

        self._add(
            area.tags,
            latitude,
            longitude,
            source,
        )


def first_position(coordinates):
    current = coordinates
    while isinstance(current, list) and current:
        if (
            len(current) >= 2
            and isinstance(current[0], (int, float))
            and isinstance(current[1], (int, float))
        ):
            return float(current[0]), float(current[1])
        current = current[0]
    return None


def swap_geojson_axes(coordinates):
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and isinstance(coordinates[1], (int, float))
    ):
        rest = coordinates[2:]
        return [coordinates[1], coordinates[0], *rest]

    if isinstance(coordinates, list):
        return [swap_geojson_axes(item) for item in coordinates]

    return coordinates


def extract_nuts2_code(properties: dict) -> str | None:
    normalized = {
        str(key).upper(): str(value).strip()
        for key, value in properties.items()
        if value is not None
    }

    for key in ("ID", "NUTS_ID", "NUTS_CODE", "CODE"):
        value = normalized.get(key, "")
        if value in STATE_BY_NUTS2:
            return value

    for value in normalized.values():
        match = re.search(r"\bAT(?:11|12|13|21|22|31|32|33|34)\b", value)
        if match:
            return match.group(0)

    return None


def coordinate_pairs(coordinates):
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and isinstance(coordinates[1], (int, float))
    ):
        yield float(coordinates[0]), float(coordinates[1])
        return

    if isinstance(coordinates, list):
        for item in coordinates:
            yield from coordinate_pairs(item)


def geometry_bbox(geometry: dict) -> tuple[float, float, float, float]:
    pairs = list(coordinate_pairs(geometry.get("coordinates", [])))
    if not pairs:
        raise ValueError("Leere Bundesland-Geometrie")

    xs = [item[0] for item in pairs]
    ys = [item[1] for item in pairs]
    return min(xs), min(ys), max(xs), max(ys)


def point_on_segment(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> bool:
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > 1e-10:
        return False

    return (
        min(x1, x2) - 1e-10 <= x <= max(x1, x2) + 1e-10
        and min(y1, y2) - 1e-10 <= y <= max(y1, y2) + 1e-10
    )


def point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    count = len(ring)

    if count < 3:
        return False

    j = count - 1

    for i in range(count):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])

        if point_on_segment(x, y, xi, yi, xj, yj):
            return True

        intersects = (
            (yi > y) != (yj > y)
            and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi
        )

        if intersects:
            inside = not inside

        j = i

    return inside


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    if not polygon:
        return False

    if not point_in_ring(x, y, polygon[0]):
        return False

    for hole in polygon[1:]:
        if point_in_ring(x, y, hole):
            return False

    return True


def point_in_geometry(x: float, y: float, geometry: dict) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])

    if geometry_type == "Polygon":
        return point_in_polygon(x, y, coordinates)

    if geometry_type == "MultiPolygon":
        return any(
            point_in_polygon(x, y, polygon)
            for polygon in coordinates
        )

    return False


def load_state_regions() -> list[dict]:
    params = urlencode(
        {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": NUTS2_TYPE_NAME,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
        }
    )

    url = f"{NUTS2_WFS_URL}?{params}"

    print("Lade Bundesland-Grenzen von Statistik Austria ...")

    request = Request(
        url,
        headers={
            "User-Agent": "Prospektilein store tile generator",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=90) as response:
        data = json.load(response)

    regions: list[dict] = []

    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        nuts2_code = extract_nuts2_code(properties)

        if nuts2_code not in STATE_BY_NUTS2:
            continue

        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates", [])
        position = first_position(coordinates)

        # Sicherheitsnetz für vertauschte Achsen.
        if position is not None:
            first_x, first_y = position
            if abs(first_x) > 30 and abs(first_y) < 30:
                geometry = dict(geometry)
                geometry["coordinates"] = swap_geojson_axes(coordinates)

        state_code, state_name = STATE_BY_NUTS2[nuts2_code]

        regions.append(
            {
                "nuts2": nuts2_code,
                "stateCode": state_code,
                "state": state_name,
                "geometry": geometry,
                "bbox": geometry_bbox(geometry),
            }
        )

    if len(regions) != 9:
        raise RuntimeError(
            "Bundesland-Grenzen konnten nicht vollständig geladen werden: "
            f"{len(regions)} von 9 Regionen gefunden."
        )

    print("Bundesland-Grenzen geladen: 9")
    return regions


def assign_states(shops: list[dict], regions: list[dict]) -> int:
    missing = 0

    for shop in shops:
        x = float(shop["longitude"])
        y = float(shop["latitude"])
        assigned = False

        for region in regions:
            min_x, min_y, max_x, max_y = region["bbox"]

            if not (
                min_x <= x <= max_x
                and min_y <= y <= max_y
            ):
                continue

            if point_in_geometry(x, y, region["geometry"]):
                shop["stateCode"] = region["stateCode"]
                shop["state"] = region["state"]
                assigned = True
                break

        if not assigned:
            missing += 1

    return missing


def haversine_meters(a: dict, b: dict) -> float:
    radius = 6_371_000.0

    lat1 = math.radians(a["latitude"])
    lat2 = math.radians(b["latitude"])
    d_lat = lat2 - lat1
    d_lon = math.radians(b["longitude"] - a["longitude"])

    h = (
        math.sin(d_lat / 2.0) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(d_lon / 2.0) ** 2
    )

    return 2.0 * radius * math.asin(min(1.0, math.sqrt(h)))


def deduplicate_shops(shops: list[dict]) -> list[dict]:
    """
    Entfernt nur sehr wahrscheinliche Doppel-Erfassungen.

    OSM enthält manchmal sowohl einen POI-Punkt als auch die Shop-Fläche.
    Wir deduplizieren ausschließlich bei gleichem normalisiertem Namen
    und höchstens 20 m Abstand. Damit werden nahe, aber echte Filialen
    nicht aggressiv zusammengelegt.
    """
    result: list[dict] = []
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)

    for shop in sorted(
        shops,
        key=lambda item: (
            item["latitude"],
            item["longitude"],
            item["name"].lower(),
        ),
    ):
        normalized_name = normalize_for_match(shop["name"])

        # ca. 100-m-Buckets, nur für schnellen Nachbarschaftsvergleich
        lat_bucket = math.floor(shop["latitude"] * 1000.0)
        lon_bucket = math.floor(shop["longitude"] * 1000.0)

        duplicate = False

        if normalized_name:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    key = (lat_bucket + dy, lon_bucket + dx)

                    for index in buckets.get(key, []):
                        other = result[index]

                        if (
                            normalize_for_match(other["name"])
                            != normalized_name
                        ):
                            continue

                        if haversine_meters(shop, other) <= 20.0:
                            duplicate = True
                            break

                    if duplicate:
                        break

                if duplicate:
                    break

        if duplicate:
            continue

        result_index = len(result)
        result.append(shop)
        buckets[(lat_bucket, lon_bucket)].append(result_index)

    return result


def tile_file_name(latitude: float, longitude: float) -> str:
    lat_index = math.floor(latitude * TILE_FACTOR)
    lon_index = math.floor(longitude * TILE_FACTOR)
    return f"{lat_index}_{lon_index}.json"


def write_tiles(shops: list[dict], output_dir: Path) -> int:
    if output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    tiles: dict[str, list[dict]] = defaultdict(list)

    for shop in shops:
        file_name = tile_file_name(
            shop["latitude"],
            shop["longitude"],
        )

        public_entry = {
            key: value
            for key, value in shop.items()
            if key != "_source"
        }

        tiles[file_name].append(public_entry)

    for file_name, entries in sorted(tiles.items()):
        entries.sort(
            key=lambda item: (
                item["name"].lower(),
                item["latitude"],
                item["longitude"],
            )
        )

        path = output_dir / file_name

        with path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as file:
            json.dump(
                entries,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

    return len(tiles)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Erzeugt Prospektilein-Store-Tiles aus einem "
            "OpenStreetMap-PBF-Extrakt."
        )
    )

    parser.add_argument(
        "pbf",
        type=Path,
        help="Pfad zur .osm.pbf-Datei",
    )

    parser.add_argument(
        "output",
        type=Path,
        help="Ausgabeordner für die JSON-Tiles",
    )

    args = parser.parse_args()

    if not args.pbf.exists():
        raise SystemExit(
            f"PBF-Datei nicht gefunden: {args.pbf}"
        )

    print(f"Lese OSM-Daten: {args.pbf}")

    handler = ShopHandler()

    handler.apply_file(
        str(args.pbf),
        locations=True,
        idx="flex_mem",
    )

    print(f"Rohe Shop-Objekte: {len(handler.shops):,}")

    shops = deduplicate_shops(handler.shops)

    print(f"Nach vorsichtiger Deduplizierung: {len(shops):,}")

    regions = load_state_regions()
    missing_states = assign_states(shops, regions)

    print(
        "Bundesland-Zuordnung ohne Treffer: "
        f"{missing_states:,} von {len(shops):,} Shops"
    )

    tile_count = write_tiles(
        shops,
        args.output,
    )

    retailer_counts = Counter(
        shop["retailer"]
        for shop in shops
    )

    state_counts = Counter(
        shop.get("state", "UNBEKANNT")
        for shop in shops
    )

    print(f"Erzeugte Tiles: {tile_count:,}")
    print("Shops nach Bundesland:")

    for state, count in sorted(state_counts.items()):
        print(f"  {state}: {count:,}")

    print("Häufigste Händler/Keys:")

    for retailer, count in retailer_counts.most_common(30):
        print(f"  {retailer}: {count:,}")

    print("Fertig.")


if __name__ == "__main__":
    main()



if __name__ == "__main__":
    main()
