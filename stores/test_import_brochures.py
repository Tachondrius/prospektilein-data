import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from import_brochures import import_catalog, brochure_from_source, check_source, https_url

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write("retailers.json", {"schemaVersion": 1, "retailers": [{"id": "billa", "name": "BILLA"}]})
        self.write("brochures.json", {"schemaVersion": 1, "generatedAt": None, "brochures": [
            {"id": "old", "retailer": "billa", "format": "web", "status": "active",
             "url": "https://example.org/old", "title": "Bestehend", "validFrom": "2026-01-01",
             "validUntil": "2026-12-31", "regions": ["AT"]}]})
        self.source = {"id": "billa_test", "retailer": "billa", "method": "pdf_link",
                       "enabled": True, "url": "https://example.org/prospekt.pdf",
                       "format": "pdf", "title": "Test", "validFrom": "2026-10-01",
                       "validUntil": "2026-10-31", "regions": ["AT"]}
    def tearDown(self):
        self.temp.cleanup()
    def write(self, filename, data):
        (self.root / filename).write_text(json.dumps(data), encoding="utf-8")
    def catalog(self):
        return json.loads((self.root / "brochures.json").read_text(encoding="utf-8"))
    def test_valid_import_preserves_existing(self):
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        report, changed = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertTrue(changed)
        self.assertEqual(len(self.catalog()["brochures"]), 2)
        self.assertEqual(report["imported"], ["billa_test"])
    def test_idempotent(self):
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        import_catalog(self.root, today=dt.date(2026, 10, 12))
        _, changed = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertFalse(changed)
    def test_expired_not_imported(self):
        self.source["validUntil"] = "2026-10-11"
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        report, changed = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertFalse(changed)
        self.assertEqual(report["skipped"][0]["reason"], "expired")
    def test_invalid_source_does_not_erase(self):
        self.source["url"] = "http://unsafe.example.org"
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        report, changed = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertFalse(changed)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(len(self.catalog()["brochures"]), 1)
    def test_disabled(self):
        self.source["enabled"] = False
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        report, changed = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertFalse(changed)
        self.assertEqual(report["skipped"][0]["reason"], "disabled")
    def test_manual_without_explicit_brochure(self):
        self.source["method"] = "manual"
        self.source.pop("validFrom")
        self.source.pop("validUntil")
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        report, changed = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertFalse(changed)
        self.assertEqual(report["skipped"][0]["reason"], "no explicit brochure")
    def test_unsafe_urls(self):
        for url in ["http://example.org", "https://user:pass@example.org/x", "file:///etc/passwd", "https://example.org:444/x"]:
            self.assertFalse(https_url(url))
    def test_unsupported_adapter_is_reported(self):
        self.source["method"] = "html_adapter"
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source]})
        report, _ = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertEqual(len(report["errors"]), 1)
    def test_duplicate_source_ids_reported(self):
        self.write("sources.json", {"schemaVersion": 1, "sources": [self.source, self.source]})
        report, _ = import_catalog(self.root, today=dt.date(2026, 10, 12))
        self.assertEqual(len(report["errors"]), 1)

if __name__ == "__main__":
    unittest.main()
