from __future__ import annotations

import unittest

from modules.file_ingest_api import MAX_DOCUMENT_BYTES, MAX_IMAGE_BYTES, _file_type


class _Upload:
    def __init__(self, filename: str, mimetype: str):
        self.filename = filename
        self.mimetype = mimetype


class FileIngestAPITests(unittest.TestCase):
    def test_pdf_uses_document_limit(self):
        name, mimetype, limit = _file_type(_Upload("ticket.pdf", "application/pdf"))
        self.assertEqual(name, "document.pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertEqual(limit, MAX_DOCUMENT_BYTES)

    def test_image_uses_image_limit(self):
        name, mimetype, limit = _file_type(_Upload("ticket.png", "image/png"))
        self.assertEqual(name, "document.png")
        self.assertEqual(mimetype, "image/png")
        self.assertEqual(limit, MAX_IMAGE_BYTES)

    def test_browser_xlsx_alias_is_normalized_for_provider(self):
        _, mimetype, _ = _file_type(_Upload(
            "trip.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ))
        self.assertEqual(mimetype, "application/vnd.ms-excel")

    def test_legacy_xls_is_rejected_because_provider_does_not_list_it(self):
        with self.assertRaises(ValueError):
            _file_type(_Upload("trip.xls", "application/vnd.ms-excel"))

    def test_mime_extension_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            _file_type(_Upload("ticket.pdf", "image/png"))


if __name__ == "__main__":
    unittest.main()
