import json
from pathlib import Path
import tempfile
import unittest
from scripts.source_audit import merge,valid_public_https

class SourceAuditTests(unittest.TestCase):
    def test_https(self):
        self.assertTrue(valid_public_https("https://www.billa.at/"))
        for url in ("http://example.com","https://localhost/","https://127.0.0.1/","https://user:pass@example.com/"):
            self.assertFalse(valid_public_https(url))
    def test_merge_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"sources.json").write_text('{"schemaVersion":1,"sources":[]}',encoding="utf-8")
            candidates=root/"candidates.json"
            candidates.write_text(json.dumps({"sources":[{"id":"example","url":"https://example.com","enabled":False}]}),encoding="utf-8")
            self.assertEqual(merge(root,candidates),["example"])
            self.assertEqual(merge(root,candidates),[])
    def test_existing_source_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"sources.json").write_text('{"sources":[{"id":"example","enabled":true,"url":"https://example.com"}]}',encoding="utf-8")
            candidates=root/"candidates.json"
            candidates.write_text(json.dumps({"sources":[{"id":"example","enabled":False,"url":"https://other.example.com"}]}),encoding="utf-8")
            self.assertEqual(merge(root,candidates),[])
            self.assertTrue(json.loads((root/"sources.json").read_text())["sources"][0]["enabled"])
if __name__=="__main__":
    unittest.main()
