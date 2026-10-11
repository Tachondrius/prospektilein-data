#!/usr/bin/env python3
"""Validate phase-1 catalogs; never modify legacy prospekte.json or stores/."""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
FORMATS = {"pdf", "web", "external"}
STATUSES = {"active", "inactive"}
CATEGORIES = {"lebensmittel","drogerie","baumarkt","moebel","elektronik","mode","tierbedarf","sport","garten","sonstiges"}
REGION = re.compile(r"^AT(?:-[1-9])?$")

def load(path):
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        raise ValueError(f"{path.name}: schemaVersion muss 1 sein")
    return data

def valid_https(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password

def validate(folder):
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)

    retailers = load(folder / "retailers.json").get("retailers")
    brochures = load(folder / "brochures.json").get("brochures")
    sources = load(folder / "sources.json").get("sources")
    if not all(isinstance(x, list) for x in (retailers, brochures, sources)):
        raise ValueError("retailers, brochures und sources müssen Listen sein")

    ids = set()
    for i, entry in enumerate(retailers):
        if not isinstance(entry, dict):
            errors.append(f"retailers[{i}]: kein Objekt")
            continue
        rid = entry.get("id")
        require(isinstance(rid,str) and bool(IDENTIFIER.fullmatch(rid)), f"retailers[{i}]: ungültige ID")
        require(rid not in ids, f"retailers[{i}]: doppelte ID {rid}")
        ids.add(rid)
        require(isinstance(entry.get("name"),str) and bool(entry["name"].strip()), f"retailers[{i}]: Name fehlt")
        require(entry.get("category") in CATEGORIES, f"retailers[{i}]: unbekannte Branche")
        require(entry.get("status") in {"candidate","verified","inactive"}, f"retailers[{i}]: ungültiger Status")

    brochure_ids = set()
    for i, entry in enumerate(brochures):
        label = f"brochures[{i}]"
        if not isinstance(entry,dict):
            errors.append(f"{label}: kein Objekt")
            continue
        bid = entry.get("id")
        require(isinstance(bid,str) and bool(IDENTIFIER.fullmatch(bid)), f"{label}: ungültige ID")
        require(bid not in brochure_ids, f"{label}: doppelte ID")
        brochure_ids.add(bid)
        require(entry.get("retailer") in ids, f"{label}: unbekannter Händler")
        require(entry.get("format") in FORMATS, f"{label}: unbekanntes Format")
        require(entry.get("status") in STATUSES, f"{label}: ungültiger Status")
        require(valid_https(entry.get("url")), f"{label}: HTTPS-URL fehlt/ungültig")
        require(isinstance(entry.get("title"),str) and bool(entry["title"].strip()), f"{label}: Titel fehlt")
        try:
            start=dt.date.fromisoformat(entry["validFrom"])
            end=dt.date.fromisoformat(entry["validUntil"])
            require(start <= end, f"{label}: Gültigkeitszeitraum verkehrt")
        except (KeyError,ValueError,TypeError):
            errors.append(f"{label}: validFrom/validUntil müssen ISO-Daten sein")
        regions=entry.get("regions")
        require(isinstance(regions,list) and len(regions)>0 and all(isinstance(r,str) and REGION.fullmatch(r) for r in regions), f"{label}: Regionen ungültig")
        store_ids=entry.get("storeIds",[])
        require(isinstance(store_ids,list) and all(isinstance(s,str) and bool(s.strip()) for s in store_ids), f"{label}: storeIds ungültig")

    source_ids=set()
    for i,entry in enumerate(sources):
        label=f"sources[{i}]"
        if not isinstance(entry,dict):
            errors.append(f"{label}: kein Objekt")
            continue
        sid=entry.get("id")
        require(isinstance(sid,str) and bool(IDENTIFIER.fullmatch(sid)), f"{label}: ungültige ID")
        require(sid not in source_ids, f"{label}: doppelte ID")
        source_ids.add(sid)
        require(entry.get("retailer") in ids, f"{label}: unbekannter Händler")
        require(valid_https(entry.get("url")), f"{label}: HTTPS-URL ungültig")
        require(entry.get("method") in {"manual","pdf_link","official_api","html_adapter"}, f"{label}: unbekannte Methode")
        require(isinstance(entry.get("enabled"),bool), f"{label}: enabled muss boolean sein")
    return errors, len(retailers), len(brochures), len(sources)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,default=Path(__file__).resolve().parent.parent)
    args=parser.parse_args()
    try:
        errors,r,b,s=validate(args.data_dir)
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f"FEHLER: {exc}",file=sys.stderr)
        return 1
    for error in errors:
        print("FEHLER:",error,file=sys.stderr)
    if errors:
        print(f"Prüfung fehlgeschlagen: {len(errors)} Fehler",file=sys.stderr)
        return 1
    print(f"OK: {r} Händlerkandidaten, {b} Prospekte, {s} Quellen. Keine Legacy-Dateien geändert.")
    return 0

if __name__=="__main__":
    sys.exit(main())
