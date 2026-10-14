import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from core import annual_report


class AuditedFinancialStatementTests(unittest.TestCase):
    @staticmethod
    def create_pdf(contents):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), contents)
        pdf_bytes = document.tobytes()
        document.close()
        return pdf_bytes

    def test_relevance_requires_company_year_statement_and_audit_terms(self):
        self.assertGreater(
            annual_report.financial_statement_relevance_score(
                "FPT",
                2024,
                "https://example.test/FPT_2024.pdf",
                "FPT audited consolidated financial statements 2024",
            ),
            0,
        )
        self.assertEqual(
            annual_report.financial_statement_relevance_score(
                "FPT",
                2024,
                "https://example.test/FPT_2024.pdf",
                "FPT annual report 2024",
            ),
            0,
        )
        self.assertEqual(
            annual_report.financial_statement_relevance_score(
                "HPG",
                2024,
                "https://example.test/HPG_2024_audited.pdf",
                "Explanation for audited financial statements in 06 months of 2024",
            ),
            0,
        )

    def test_vietnamese_company_name_matches_ticker_alias(self):
        self.assertTrue(
            any(
                annual_report._search_text(alias)
                in annual_report._search_text("Công ty Cổ phần Tập đoàn Hòa Phát")
                for alias in annual_report.get_company_names("HPG")
            )
        )

    def test_content_validation_rejects_explanation_and_accepts_audited_annual_statements(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            explanation_path = Path(temp_dir) / "explanation.pdf"
            explanation_path.write_bytes(self.create_pdf(
                "HPG Hoa Phat Group Joint Stock Company\n"
                "Explanation for audited financial statements in 06 months of 2024"
            ))
            is_valid, reason = (
                annual_report.validate_audited_annual_financial_statement(
                    explanation_path, "HPG", 2024
                )
            )
            self.assertFalse(is_valid)
            self.assertIn("giải trình", reason)

            statements_path = Path(temp_dir) / "statements.pdf"
            statements_path.write_bytes(self.create_pdf(
                "HPG Hoa Phat Group Joint Stock Company\n"
                "Consolidated financial statements\n"
                "At 31 December 2024\n"
                "Balance sheet\n"
                "Income statement\n"
                "Statement of cash flows\n"
                "Independent auditor's report\n"
                "We have audited these financial statements."
            ))
            is_valid, reason = (
                annual_report.validate_audited_annual_financial_statement(
                    statements_path, "HPG", 2024
                )
            )
            self.assertTrue(is_valid, reason)

        self.assertEqual(
            annual_report.financial_statement_relevance_score(
                "FPT",
                2024,
                "https://example.test/ABC_2024.pdf",
                "ABC audited financial statements 2024",
            ),
            0,
        )

    def test_content_validation_uses_optional_ocr_for_scanned_pdf(self):
        scanned_text = (
            "HPG Hoa Phat Group Joint Stock Company "
            "Consolidated financial statements At 31 December 2024 "
            "Balance sheet Income statement Statement of cash flows "
            "Independent auditor's report We have audited these financial statements."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            scanned_path = Path(temp_dir) / "scanned.pdf"
            scanned_path.write_bytes(self.create_pdf(""))
            with patch(
                "core.annual_report._ocr_pdf_page",
                return_value=scanned_text,
            ):
                is_valid, reason = (
                    annual_report.validate_audited_annual_financial_statement(
                        scanned_path, "HPG", 2024
                    )
                )

        self.assertTrue(is_valid, reason)

    def test_search_returns_only_relevant_audited_financial_statement_pdf(self):
        class FakeDDGS:
            def __init__(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def text(self, query, **kwargs):
                return [
                    {
                        "href": "https://example.test/FPT_2024_audited.pdf",
                        "title": "FPT audited consolidated financial statements 2024",
                    },
                    {
                        "href": "https://example.test/FPT_2024_annual_report.pdf",
                        "title": "FPT annual report 2024",
                    },
                ]

        with patch("core.annual_report.DDGS", FakeDDGS):
            links = annual_report.search_financial_statement_pdf_links("FPT", 2024)

        self.assertEqual(links, ["https://example.test/FPT_2024_audited.pdf"])

    def test_official_issuer_page_extracts_only_audited_annual_statement_links(self):
        class FakeResponse:
            url = "https://www.hoaphat.com.vn/investor-relations/financial-report"
            content = b"<html>"
            text = """
                <a href="https://file.hoaphat.com.vn/2025/hpg-audited-consolidated-financial-statements-2024.pdf">
                  Audited Consolidated Financial Statements for the year ended 31 December 2024
                </a>
                <a href="https://file.hoaphat.com.vn/2025/hpg-explanation-audited-six-month-financial-statements-2024.pdf">
                  Explanation for audited financial statements in 06 months of 2024
                </a>
            """

            def raise_for_status(self):
                return None

        with patch("core.annual_report.requests.get", return_value=FakeResponse()):
            links = annual_report.extract_financial_statement_pdf_links(
                FakeResponse.url, "HPG", 2024
            )

        self.assertEqual(
            links,
            [
                "https://file.hoaphat.com.vn/2025/"
                "hpg-audited-consolidated-financial-statements-2024.pdf"
            ],
        )

    def test_cafef_feed_selects_audited_annual_pdf_and_prefers_consolidated(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "Data": [
                        {
                            "Year": 2024,
                            "Quarter": 5,
                            "Name": "Báo cáo tài chính hợp nhất năm 2024 (đã kiểm toán)",
                            "Link": "https://cafefnew.mediacdn.vn/hpg-2024-audited-consolidated.pdf",
                        },
                        {
                            "Year": 2024,
                            "Quarter": 5,
                            "Name": "Báo cáo tài chính công ty mẹ năm 2024 (đã kiểm toán)",
                            "Link": "https://cafefnew.mediacdn.vn/hpg-2024-audited-separate.pdf",
                        },
                        {
                            "Year": 2024,
                            "Quarter": 2,
                            "Name": "Giải trình BCTC đã kiểm toán 6 tháng năm 2024",
                            "Link": "https://cafefnew.mediacdn.vn/hpg-explanation-2024.pdf",
                        },
                        {
                            "Year": 2024,
                            "Quarter": 4,
                            "Name": "Báo cáo tài chính hợp nhất quý 4 năm 2024",
                            "Link": "https://cafefnew.mediacdn.vn/hpg-q4-2024.pdf",
                        },
                    ]
                }

        with patch("core.annual_report.requests.get", return_value=FakeResponse()) as get:
            links = annual_report.search_cafef_financial_statement_pdf_links(
                "HPG", 2024
            )

        self.assertEqual(
            links,
            [
                "https://cafefnew.mediacdn.vn/hpg-2024-audited-consolidated.pdf",
                "https://cafefnew.mediacdn.vn/hpg-2024-audited-separate.pdf",
            ],
        )
        self.assertIn("Symbol=HPG", get.call_args.args[0])

    def test_download_saves_valid_pdf_and_reports_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "FPT_financial_statements_2024.pdf"
            official_url = "https://file.company.test/FPT_2024_audited.pdf"

            def fake_download(url, output_path):
                output_path.write_bytes(self.create_pdf(
                    "FPT Corporation\n"
                    "Consolidated financial statements\n"
                    "At 31 December 2024\n"
                    "Balance sheet\n"
                    "Income statement\n"
                    "Statement of cash flows\n"
                    "Independent auditor's report\n"
                    "We have audited these financial statements."
                ))
                return True

            with (
                patch(
                    "core.annual_report.search_cafef_financial_statement_pdf_links",
                    return_value=[],
                ) as cafef_search,
                patch(
                    "core.annual_report.search_official_financial_statement_pdf_links",
                    return_value=[official_url],
                ) as official_search,
                patch(
                    "core.annual_report.search_financial_statement_pdf_links"
                ) as fallback_search,
                patch("core.annual_report.download_pdf", side_effect=fake_download),
            ):
                result = annual_report.download_financial_statement_report(
                    "FPT", 2024, destination
                )

            cafef_search.assert_called_once()
            official_search.assert_called_once()
            fallback_search.assert_not_called()
            self.assertTrue(result["ok"])
            self.assertEqual(result["path"], destination)
            self.assertEqual(result["url"], official_url)
            self.assertEqual(result["source_type"], "official")
            self.assertTrue(
                annual_report.validate_audited_annual_financial_statement(
                    destination, "FPT", 2024
                )[0]
            )

    def test_download_rejects_explanation_even_if_search_result_looks_relevant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "HPG_financial_statements_2024.pdf"

            def fake_download(url, output_path):
                output_path.write_bytes(self.create_pdf(
                    "HPG Hoa Phat Group Joint Stock Company\n"
                    "Explanation for audited financial statements in 06 months of 2024"
                ))
                return True

            with (
                patch(
                    "core.annual_report.search_cafef_financial_statement_pdf_links",
                    return_value=[],
                ),
                patch(
                    "core.annual_report.search_official_financial_statement_pdf_links",
                    return_value=[],
                ),
                patch(
                    "core.annual_report.search_financial_statement_pdf_links",
                    return_value=["https://example.test/HPG_2024_audited.pdf"],
                ),
                patch("core.annual_report.download_pdf", side_effect=fake_download),
            ):
                result = annual_report.download_financial_statement_report(
                    "HPG", 2024, destination
                )

            self.assertFalse(result["ok"])
            self.assertIn("văn bản giải trình", result["error"])
            self.assertFalse(destination.exists())

    def test_no_matching_report_returns_explicit_failure(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "core.annual_report.search_cafef_financial_statement_pdf_links",
                return_value=[],
            ),
            patch(
                "core.annual_report.search_official_financial_statement_pdf_links",
                return_value=[],
            ),
            patch(
                "core.annual_report.search_financial_statement_pdf_links",
                return_value=[],
            ),
        ):
            result = annual_report.download_financial_statement_report(
                "FPT",
                2024,
                Path(temp_dir) / "FPT_financial_statements_2024.pdf",
            )

        self.assertFalse(result["ok"])
        self.assertIn("Không tìm thấy", result["error"])


if __name__ == "__main__":
    unittest.main()
