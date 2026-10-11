#!/usr/bin/env python3
"""Conservative, opt-in official brochure URL verifier.

Reads sources.json and brochures.json. Does not scrape arbitrary websites,
discover unknown URLs, or touch the legacy prospekte.json.
"""
import argparse
import datetime as dt
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "ProspektileinCatalogChecker/1.0 (+https://github.com/Tachondrius/prospektilein-data)"
MAX_BYTES = 8192
TIMEOUT = 12
MAX_REDIRECTS = 3

def public_https(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443):
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".local", ".internal")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_global
        except ValueError:
            return True
    except ValueError:
        return False

def public_dns(host):
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(records) and all(ipaddress.ip_address(record[4][0]).is_global for record in records)
    except (OSError, ValueError):
        return False

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.count = 0
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS or not public_https(newurl):
            raise urllib.error.HTTPError(newurl, code, "Unsafe redirect", headers, fp)
        host = urllib.parse.urlsplit(newurl).hostname
        if not public_dns(host):
            raise urllib.error.HTTPError(newurl, code, "Redirect DNS rejected", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def check_url(url, fmt):
    if not public_https(url):
        return "invalid_url", "Only public HTTPS URLs on port 443 are allowed"
    host = urllib.parse.urlsplit(url).hostname
    if not public_dns(host):
        return "dns_rejected", "DNS unavailable or non-public IP"
    try:
        opener = urllib.request.build_opener(SafeRedirect())
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html;q=0.8,*/*;q=0.1"})
        with opener.open(req, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES)
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower().strip()
            final_url = response.geturl()
            if not public_https(final_url):
                return "invalid_redirect", "Final URL is not public HTTPS"
            if fmt == "pdf":
                if not data.startswith(b"%PDF-"):
                    return "wrong_format", f"Expected PDF header; received {content_type or 'unknown'}"
            elif fmt in ("web", "external"):
                if content_type not in ("text/html", "application/xhtml+xml"):
                    return "wrong_format", f"Expected HTML; received {content_type or 'unknown'}"
            else:
                return "unsupported_format", str(fmt)
            return "reachable", f"HTTP {response.status}; {content_type or 'unknown'}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return "unreachable", type(exc).__name__

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--online", action="store_true", help="Check network URLs; otherwise only report structure and expiration")
    args = parser.parse_args()
    base = args.data_dir
    with (base / "brochures.json").open(encoding="utf-8") as f:
        brochures = json.load(f)["brochures"]
    with (base / "sources.json").open(encoding="utf-8") as f:
        sources = json.load(f)["sources"]
    today = dt.date.today()
    results = []
    for item in brochures:
        if item.get("status") != "active":
            continue
        try:
            expired = dt.date.fromisoformat(item["validUntil"]) < today
        except (KeyError, ValueError, TypeError):
            expired = True
        if expired:
            status, detail = "expired", "Validity period ended or missing"
        elif args.online:
            status, detail = check_url(item["url"], item["format"])
        else:
            status, detail = "not_checked", "Run with --online for network verification"
        results.append({"kind": "brochure", "id": item.get("id"), "status": status, "detail": detail})
    for source in sources:
        if not source.get("enabled", False):
            continue
        if source.get("method") not in ("manual", "pdf_link", "official_api", "html_adapter"):
            results.append({"kind": "source", "id": source.get("id"), "status": "unsupported_method", "detail": "Unknown source method"})
            continue
        if args.online:
            # Source URL format may differ from brochure format; do not pretend to discover brochures.
            status, detail = check_url(source["url"], "pdf" if source["method"] == "pdf_link" else "web")
        else:
            status, detail = "not_checked", "Run with --online for network verification"
        results.append({"kind": "source", "id": source.get("id"), "status": status, "detail": detail})
    report = {
        "schemaVersion": 1,
        "checkedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "online": args.online,
        "results": results,
        "summary": {status: sum(x["status"] == status for x in results) for status in sorted({x["status"] for x in results})}
    }
    output = args.output or (base / "verification-report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(results)} registered entries. Report: {output}")
    for entry in results:
        print(f"{entry['kind']} {entry['id']}: {entry['status']}")
    # Network outages do not delete catalog data or fail the whole workflow.
    return 0

if __name__ == "__main__":
    sys.exit(main())
