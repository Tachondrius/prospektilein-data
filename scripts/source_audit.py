#!/usr/bin/env python3
"""Phase 4: safely merge candidates and audit their HTTP availability."""
import argparse
import datetime as dt
import json
from pathlib import Path
import urllib.error
import urllib.request
import urllib.parse
import ipaddress
import socket

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "source_candidates.json"
MAX_READ = 4096

def valid_public_https(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
            return False
        host = parsed.hostname
        if host == "localhost" or host.endswith((".local", ".internal")):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return True
    except ValueError:
        return False

def merge(root, candidates_file):
    existing_file = root / "sources.json"
    existing = json.loads(existing_file.read_text(encoding="utf-8"))
    candidates = json.loads(candidates_file.read_text(encoding="utf-8"))
    if not isinstance(existing.get("sources"), list) or not isinstance(candidates.get("sources"), list):
        raise ValueError("sources must be arrays")
    ids = {s["id"] for s in existing["sources"]}
    added = []
    for s in candidates["sources"]:
        if not isinstance(s, dict) or not isinstance(s.get("id"),str) or not valid_public_https(s.get("url")):
            raise ValueError("invalid candidate")
        if s.get("enabled") is not False:
            raise ValueError("candidates must be disabled")
        if s["id"] not in ids:
            existing["sources"].append(s)
            ids.add(s["id"])
            added.append(s["id"])
    if added:
        import os, tempfile
        fd, name = tempfile.mkstemp(prefix=".sources-",suffix=".json",dir=root)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f:
                json.dump(existing,f,ensure_ascii=False,indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(name,existing_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return added

def audit(root, output):
    doc = json.loads((root/"sources.json").read_text(encoding="utf-8"))
    results=[]
    for source in doc["sources"]:
        url=source.get("url")
        result={"id":source.get("id"),"retailer":source.get("retailer"),"enabled":source.get("enabled"),"status":"not_checked"}
        if not valid_public_https(url):
            result.update(status="invalid_url")
        else:
            try:
                # urllib follows redirects: validate final destination as well.
                req=urllib.request.Request(url,headers={"User-Agent":"Prospektilein-SourceAudit/1.0","Accept":"application/pdf,text/html,*/*;q=0.5"})
                with urllib.request.urlopen(req,timeout=12) as resp:
                    final=resp.geturl()
                    if not valid_public_https(final):
                        raise ValueError("unsafe redirect")
                    sample=resp.read(MAX_READ)
                    content_type=resp.headers.get("Content-Type","").lower()
                    kind="pdf" if sample.lstrip().startswith(b"%PDF-") else "html" if b"<html" in sample.lower() or "text/html" in content_type else "other"
                    result.update(status="reachable" if sample else "empty",httpStatus=resp.status,detectedFormat=kind,finalHost=urllib.parse.urlsplit(final).hostname)
            except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:
                result.update(status="error",reason=str(exc)[:250])
        results.append(result)
    report={"checkedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"note":"Availability is not evidence of current brochure validity or permission to reuse content.","sources":results}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,default=ROOT)
    parser.add_argument("--candidates",type=Path,default=CANDIDATES)
    parser.add_argument("--merge",action="store_true")
    parser.add_argument("--audit",action="store_true")
    args=parser.parse_args()
    if args.merge:
        added=merge(args.root,args.candidates)
        print("Neue Prüfkandidaten:",len(added))
    if args.audit:
        report=audit(args.root,args.root/"source-audit-report.json")
        for r in report["sources"]:
            print(r["id"],r["status"],r.get("detectedFormat",""))
    if not args.merge and not args.audit:
        parser.error("use --merge or --audit")
if __name__=="__main__":
    main()
