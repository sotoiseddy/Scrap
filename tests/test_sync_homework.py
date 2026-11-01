import unittest

from bs4 import BeautifulSoup

import sync_homework


class HomeworkSyncTests(unittest.TestCase):
    def test_extracts_homework_fields_and_attachment(self):
        soup = BeautifulSoup(
            """
            <div class="row white-radius-sec">
              <div class="details-box-wrp">
                <div class="row">
                  <div class="col-lg-3 col-md-6 col-4">Title</div>
                  <div>Fractions worksheet</div>
                </div>
                <div class="row">
                  <div class="col-lg-3 col-md-6 col-4">Date</div>
                  <div>02/11/2026</div>
                </div>
                <div class="row">
                  <div class="col-lg-3 col-md-6 col-4">Due Date</div>
                  <div>05/11/2026</div>
                </div>
              </div>
              <div class="message-box-wrp"><pre>Complete questions 1-5.</pre></div>
              <span class="icn-cvr-1">Math</span>
              <span class="icn-cvr-2">Ms. Teacher</span>
              <a href="/ParentApp/HomeWork/Attachments/worksheet.pdf">Download</a>
            </div>
            """,
            "html.parser",
        )

        record = sync_homework._extract_record(
            "42",
            "https://mcsuna.in/ParentApp/HomeWork/Details/42",
            soup,
        )

        self.assertEqual(record["title"], "Fractions worksheet")
        self.assertEqual(record["subject"], "Math")
        self.assertEqual(record["teacher"], "Ms. Teacher")
        self.assertEqual(record["assigned_date"], "2026-11-02")
        self.assertEqual(record["due_date"], "2026-11-05")
        self.assertEqual(record["description"], "Complete questions 1-5.")
        self.assertEqual(
            record["attachment_urls"],
            ["https://mcsuna.in/ParentApp/HomeWork/Attachments/worksheet.pdf"],
        )

    def test_normalizes_supported_date_formats(self):
        self.assertEqual(sync_homework._normalize_date("2 Nov 2026"), "2026-11-02")
        self.assertIsNone(sync_homework._normalize_date(None))


if __name__ == "__main__":
    unittest.main()
