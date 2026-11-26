import unittest
from datetime import date

from build import resolve_source_url


class SourceUrlTemplateTests(unittest.TestCase):
    def test_resolves_ymd_using_supplied_date(self):
        template = "https://free.datiya.com/uploads/{Ymd}-clash.yaml"
        self.assertEqual(
            resolve_source_url(template, date(2026, 11, 26)),
            "https://free.datiya.com/uploads/20261126-clash.yaml",
        )

    def test_resolves_year_month_and_ymd_in_one_url(self):
        template = "https://node.freeclashnode.com/uploads/{Y}/{mm}/0-{Ymd}.yaml"
        self.assertEqual(
            resolve_source_url(template, date(2026, 11, 26)),
            "https://node.freeclashnode.com/uploads/2026/11/0-20261126.yaml",
        )

    def test_resolves_ymd_in_github_filename(self):
        template = "https://raw.githubusercontent.com/free-nodes/clashfree/main/clash{Ymd}.yml"
        self.assertEqual(
            resolve_source_url(template, date(2026, 11, 26)),
            "https://raw.githubusercontent.com/free-nodes/clashfree/main/clash20261126.yml",
        )

    def test_leaves_static_urls_unchanged(self):
        url = "https://example.com/subscription.yaml"
        self.assertEqual(resolve_source_url(url, date(2026, 11, 26)), url)


if __name__ == "__main__":
    unittest.main()
