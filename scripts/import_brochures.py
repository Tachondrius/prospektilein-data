#!/usr/bin/env python3
"""Safe, extensible Prospektilein catalog importer. Python 3.10+, stdlib only."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REGION = re.compile(r"^AT(?:-[1-9])?$")
ID = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_BYTES = 12 * 1024 * 1024
TIMEOUT = 15

def load(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".prospektilein-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def https_url(value):
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.hostname.endswith(".local"))

def valid_date(value):
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False

def check_source(source, retailers):
    if not isinstance(source, dict):
        raise ValueError("Source muss ein Objekt sein")
    sid = source.get("id")
    if not isinstance(sid, str) or not ID.fullmatch(sid):
        raise ValueError("Ungültige Source-ID")
    if source.get("retailer") not in retailers:
        raise ValueError(f"{sid}: unbekannter Händler")
    if source.get("method") not in ("manual", "pdf_link", "html_adapter", "official_api"):
        raise ValueError(f"{sid}: unbekannte Methode")
    if not isinstance(source.get("enabled"), bool):
        raise ValueError(f"{sid}: enabled muss true/false sein")
    if not https_url(source.get("url")):
        raise ValueError(f"{sid}: HTTPS-Quelladresse fehlt/ungültig")
    return sid

def brochure_from_source(source):
    sid = source["id"]
    if source["method"] not in ("manual", "pdf_link"):
        raise ValueError(f"{sid}: Methode benötigt einen eigenen geprüften Adapter")
    if source["method"] == "manual" and not source.get("brochureUrl"):
        return None
    url = source.get("brochureUrl", source["url"])
    if not https_url(url):
        raise ValueError(f"{sid}: Prospektadresse muss HTTPS sein")
    start, end = source.get("validFrom"), source.get("validUntil")
    if not valid_date(start) or not valid_date(end) or start > end:
        raise ValueError(f"{sid}: gültige validFrom/validUntil fehlen")
    regions = source.get("regions")
    if not isinstance(regions, list) or not regions or any(not isinstance(r, str) or not REGION.fullmatch(r) for r in regions):
        raise ValueError(f"{sid}: ungültige Regionen")
    store_ids = source.get("storeIds", [])
    if not isinstance(store_ids, list) or any(not isinstance(s, str) or not s.strip() for s in store_ids):
        raise ValueError(f"{sid}: ungültige storeIds")
    fmt = source.get("format", "pdf" if source["method"] == "pdf_link" else "web")
    if fmt not in ("pdf", "web", "external"):
        raise ValueError(f"{sid}: ungültiges Format")
    title = source.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{sid}: Titel fehlt")
    identifier = "src_" + sid
    return {"id": identifier, "retailer": source["retailer"], "format": fmt,
            "status": "active", "url": url, "title": title.strip(),
            "validFrom": start, "validUntil": end, "regions": regions,
            "storeIds": store_ids}

def check_reachable(url, expected_format, opener=None):
    """GET with bounded read; never trust a file extension or HEAD alone."""
    opener = opener or urllib.request.build_opener()
    request = urllib.request.Request(url, headers={"User-Agent": "Prospektilein-Catalog/1.0", "Accept": "application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    with opener.open(request, timeout=TIMEOUT) as response:
        final = response.geturl()
        if not https_url(final):
            raise ValueError("Unsicheres Weiterleitungsziel")
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_BYTES:
            raise ValueError("Datei zu groß")
        sample = response.read(4096)
        if not sample:
            raise ValueError("Leere Antwort")
        content_type = response.headers.get("Content-Type", "").lower()
        if expected_format == "pdf" and not sample.lstrip().startswith(b"%PDF-"):
            raise ValueError(f"Kein PDF (Content-Type: {content_type})")
        if expected_format in ("web", "external") and b"<html" not in sample.lower() and "text/html" not in content_type:
            raise ValueError("Kein erkennbares HTML")
    return True

def import_catalog(root, check_network=False, today=None):
    today = today or dt.date.today()
    retailers_doc = load(root / "retailers.json", {})
    retailer_rows = retailers_doc.get("retailers", [])
    retailers = {r["id"] for r in retailer_rows if isinstance(r, dict) and isinstance(r.get("id"), str)}
    if not retailers:
        raise ValueError("retailers.json fehlt oder enthält keine Händler")
    sources_doc = load(root / "sources.json", {})
    sources = sources_doc.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources.json: sources muss eine Liste sein")
    catalog = load(root / "brochures.json", {"schemaVersion": 1, "generatedAt": None, "brochures": []})
    existing = catalog.get("brochures")
    if not isinstance(existing, list):
        raise ValueError("brochures.json: brochures muss eine Liste sein")
    ids = set()
    updates = {}
    report = {"checkedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "sources": len(sources),
              "imported": [], "skipped": [], "errors": [], "networkCheck": check_network}
    for source in sources:
        try:
            sid = check_source(source, retailers)
            if sid in ids:
                raise ValueError(f"{sid}: doppelte Source-ID")
            ids.add(sid)
            if not source["enabled"]:
                report["skipped"].append({"source": sid, "reason": "disabled"})
                continue
            entry = brochure_from_source(source)
            if entry is None:
                report["skipped"].append({"source": sid, "reason": "no explicit brochure"})
                continue
            if dt.date.fromisoformat(entry["validUntil"]) < today:
                report["skipped"].append({"source": sid, "reason": "expired"})
                continue
            if check_network:
                check_reachable(entry["url"], entry["format"])
            updates[entry["id"]] = entry
            report["imported"].append(sid)
        except (ValueError, KeyError, TypeError, urllib.error.URLError, OSError) as exc:
            report["errors"].append({"source": source.get("id", "?") if isinstance(source, dict) else "?",
                                     "error": str(exc)})
    # Preserve all entries except those that have a validated replacement.
    result = [item for item in existing if not isinstance(item, dict) or item.get("id") not in updates]
    result.extend(updates.values())
    changed = result != existing
    if changed:
        atomic_json(root / "brochures.json", {"schemaVersion": 1,
                    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "brochures": result})
    atomic_json(root / "import-report.json", report)
    return report, changed

def main():
    parser = argparse.ArgumentParser(description="Importiert bestätigte Prospektquellen sicher.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check-network", action="store_true", help="URL-Inhalte live prüfen")
    args = parser.parse_args()
    try:
        report, changed = import_catalog(args.root.resolve(), args.check_network)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Abbruch: {exc}", file=sys.stderr)
        return 2
    print(f"Quellen: {report['sources']}; importiert: {len(report['imported'])}; "
          f"übersprungen: {len(report['skipped'])}; Fehler: {len(report['errors'])}; "
          f"Katalog geändert: {'ja' if changed else 'nein'}")
    for error in report["errors"]:
        print(f"FEHLER {error['source']}: {error['error']}", file=sys.stderr)
    print(f"Bericht: {args.root.resolve() / 'import-report.json'}")
    return 1 if report["errors"] else 0

if __name__ == "__main__":
    sys.exit(main())
