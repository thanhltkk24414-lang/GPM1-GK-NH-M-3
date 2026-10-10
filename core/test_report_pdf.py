import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.report_pdf import generate_pdf


class GeneratePdfTests(unittest.TestCase):
    def test_replaces_embedded_svg_charts_for_xhtml2pdf(self):
        from xhtml2pdf import pisa

        svg_uri = "data:image/svg+xml;base64," + base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            b'<rect width="10" height="10" fill="red"/></svg>'
        ).decode("ascii")
        report = {
            "ticker": "AAA",
            "company_name": "AAA",
            "report_date": "10/10/2026",
            "recommendation": "—",
            "target_price": "—",
            "upside": "—",
            "exchange": "HOSE",
            "industry": "Test",
            "current_price": "Chờ realtime",
            "investment_horizon": "12 tháng",
            "price_chart": svg_uri,
            "financial_chart": svg_uri,
            "technical_metrics": [],
            "financial_years": [],
            "financial_sections": [],
            "valuation_methods": [],
            "investment_points": [],
            "key_risks": [],
            "news_list": [],
        }
        original_create_pdf = pisa.CreatePDF
        html_sources = []

        def capture_source(*args, **kwargs):
            html_sources.append(kwargs["src"])
            return original_create_pdf(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "AAA_report.pdf"
            with patch.object(pisa, "CreatePDF", side_effect=capture_source):
                generate_pdf(report, output_path)

            self.assertTrue(output_path.read_bytes().startswith(b"%PDF-"))
            self.assertEqual(len(html_sources), 1)
            self.assertNotIn("data:image/svg+xml;base64", html_sources[0])
            self.assertEqual(html_sources[0].count("data:image/png;base64"), 1)
            self.assertIn('<table class="report-header">', html_sources[0])
            import pymupdf

            document = pymupdf.open(output_path)
            image_pixmaps = [
                page.get_pixmap(clip=image["bbox"], alpha=False)
                for page in document
                for image in page.get_image_info()
            ]
            self.assertEqual(len(image_pixmaps), 1)
            self.assertTrue(
                all(
                    pixmap.width > 0
                    and pixmap.height > 0
                    and any(channel < 250 for channel in pixmap.samples)
                    for pixmap in image_pixmaps
                )
            )
            document.close()

    def test_rasterizes_price_chart_svg_to_png_for_fallback(self):
        import base64
        from datetime import datetime, timedelta

        import pandas as pd

        from core.report_pdf import _price_chart_data_uri, _rasterize_svg_data_uri

        history = pd.DataFrame(
            {
                "date": [datetime(2026, 1, 1) + timedelta(days=i) for i in range(25)],
                "open": [10 + i for i in range(25)],
                "high": [11 + i for i in range(25)],
                "low": [9 + i for i in range(25)],
                "close": [10.5 + i for i in range(25)],
                "volume": [1000 + i * 10 for i in range(25)],
            }
        )
        svg_uri = _price_chart_data_uri(history, "AAA")
        png_uri = _rasterize_svg_data_uri(svg_uri, Path(__file__).parent)
        png_data = base64.b64decode(png_uri.split(",", 1)[1])
        self.assertTrue(png_data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_rasterizes_financial_chart_svg_to_png_for_fallback(self):
        import base64
        from io import BytesIO

        from PIL import Image

        from core.report_pdf import (
            _financial_chart_data_uri,
            _rasterize_svg_data_uri,
        )

        chart_data = {
            "income_label": "Doanh thu thuần",
            "income": {"2022": 10, "2023": 12, "2024": 15},
            "income_yoy": {"2022": None, "2023": 20, "2024": 25},
            "profit_label": "Lợi nhuận sau thuế",
            "net_profit": {"2022": 1, "2023": 2, "2024": 3},
            "net_profit_yoy": {"2022": None, "2023": 100, "2024": 50},
        }
        svg_uri = _financial_chart_data_uri(
            ["2022", "2023", "2024"], chart_data
        )
        png_uri = _rasterize_svg_data_uri(svg_uri, Path(__file__).parent)
        png_data = base64.b64decode(png_uri.split(",", 1)[1])
        image = Image.open(BytesIO(png_data)).convert("RGB")
        nonwhite_pixels = sum(
            count
            for count, color in image.getcolors(image.width * image.height)
            if color != (255, 255, 255)
        )
        self.assertTrue(png_data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(nonwhite_pixels, 5000)

    def test_retries_without_css_when_xhtml2pdf_hits_keep_together(self):
        from xhtml2pdf import pisa

        original_create_pdf = pisa.CreatePDF
        calls = 0

        def fail_once_then_render(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AttributeError("'KeepTogether' object has no attribute 'draw'")
            return original_create_pdf(*args, **kwargs)

        report = {
            "ticker": "AAA",
            "company_name": "AAA",
            "report_date": "10/10/2026",
            "recommendation": "—",
            "target_price": "—",
            "upside": "—",
            "exchange": "HOSE",
            "industry": "Test",
            "current_price": "Chờ realtime",
            "investment_horizon": "12 tháng",
            "technical_metrics": [],
            "financial_years": [],
            "financial_sections": [],
            "valuation_methods": [],
            "investment_points": [],
            "key_risks": [],
            "news_list": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "AAA_report.pdf"
            with patch.object(pisa, "CreatePDF", side_effect=fail_once_then_render):
                generate_pdf(report, output_path)

            self.assertEqual(calls, 2)
            self.assertTrue(output_path.read_bytes().startswith(b"%PDF-"))
            self.assertFalse(
                list(Path(temp_dir).glob("tmp*.pdf")),
                "Temporary PDF files should be removed after rendering.",
            )


if __name__ == "__main__":
    unittest.main()
