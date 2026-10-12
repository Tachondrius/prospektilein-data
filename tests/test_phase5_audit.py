import datetime
import importlib.util
from pathlib import Path
import unittest
p=Path(__file__).resolve().parents[1]/"scripts"/"phase5_audit.py"
spec=importlib.util.spec_from_file_location("audit",p)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
class AuditTests(unittest.TestCase):
    def test_https(self): self.assertTrue(audit.safe_url("https://example.com/a"))
    def test_http(self): self.assertFalse(audit.safe_url("http://example.com"))
    def test_credentials(self): self.assertFalse(audit.safe_url("https://x:y@example.com"))
    def test_expired(self): self.assertEqual(audit.validity({"validFrom":"2025-01-01","validUntil":"2025-12-31"},datetime.date(2026,10,12)),"expired")
    def test_unknown(self): self.assertEqual(audit.validity({}),"unknown")
    def test_future(self): self.assertEqual(audit.validity({"validFrom":"2027-01-01","validUntil":"2027-12-31"},datetime.date(2026,10,12)),"upcoming")
    def test_valid(self): self.assertEqual(audit.validity({"validFrom":"2026-01-01","validUntil":"2026-12-31"},datetime.date(2026,10,12)),"within_dates")
    def test_isolation(self):
        result=audit.audit({"sources":[{"id":"one","url":"x"},{"id":"two","url":"https://example.com"}]},{"brochures":[]},True)
        self.assertEqual(len(result["sources"]),2)
        self.assertTrue(result["readOnly"])
if __name__=="__main__": unittest.main()
