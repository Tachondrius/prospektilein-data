import argparse
import datetime as datetime
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

def safe_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password
    except (ValueError, TypeError):
        return False

class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_url(newurl):
            raise ValueError("unsafe redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def check(url, timeout=12):
    if not safe_url(url):
        return {"state": "invalid_url"}
    req = urllib.request.Request(url, headers={"User-Agent":"ProspektileinAudit/1.0"})
    try:
        with urllib.request.build_opener(SecureRedirect()).open(req, timeout=timeout) as r:
            content = r.read(1024).lstrip().lower()
            kind = "pdf" if content.startswith(b"%pdf-") else "html" if b"<html" in content or b"<!doctype html" in content else "unknown"
            return {"state":"reachable","http":r.status,"format":kind,"redirected":r.geturl()!=url,"finalUrl":r.geturl()}
    except urllib.error.HTTPError as e:
        return {"state":"http_error","http":e.code}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return {"state":"network_error","detail":str(e)[:200]}

def validity(item, today=None):
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    try:
        start=datetime.date.fromisoformat(item["validFrom"])
        end=datetime.date.fromisoformat(item["validUntil"])
    except (KeyError,TypeError,ValueError):
        return "unknown"
    if start>end: return "invalid"
    if today<start: return "upcoming"
    if today>end: return "expired"
    return "within_dates"

def read(path, default):
    p=pathlib.Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

def audit(sources, brochures, offline=False):
    out=[]
    for s in sources.get("sources",[]):
        try:
            result={"id":s.get("id"),"retailer":s.get("retailer"),"enabled":s.get("enabled") is True,
                    "check":{"state":"offline"} if offline else check(s.get("url",""))}
        except Exception as e:
            result={"id":s.get("id"),"check":{"state":"unexpected_error","detail":str(e)[:200]}}
        out.append(result)
    return {"createdAt":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "readOnly":True,"sources":out,
            "brochures":[{"id":b.get("id"),"retailer":b.get("retailer"),"validity":validity(b)} for b in brochures.get("brochures",[])]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--offline",action="store_true")
    p.add_argument("--output",default="phase5-report.json")
    a=p.parse_args()
    report=audit(read("sources.json",{"sources":[]}),read("brochures.json",{"brochures":[]}),a.offline)
    pathlib.Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Quellen:",len(report["sources"]),"Prospekte:",len(report["brochures"]),"Bericht:",a.output)
    print("Nur Diagnose: keine Daten aktualisiert oder freigeschaltet.")
if __name__=="__main__":
    main()
