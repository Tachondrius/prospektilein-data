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

import osmium


TILE_FACTOR = 100.0

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
    candidates = [
        tags.get("brand", ""),
        tags.get("name", ""),
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

    tile_count = write_tiles(
        shops,
        args.output,
    )

    retailer_counts = Counter(
        shop["retailer"]
        for shop in shops
    )

    print(f"Erzeugte Tiles: {tile_count:,}")
    print("Häufigste Händler/Keys:")

    for retailer, count in retailer_counts.most_common(30):
        print(f"  {retailer}: {count:,}")

    print("Fertig.")


if __name__ == "__main__":
    main()
