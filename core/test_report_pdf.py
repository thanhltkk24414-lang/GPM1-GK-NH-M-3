import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.report_pdf import (
    _load_financial_data,
    apply_scoring_to_report,
    generate_pdf,
    load_stock_report_data,
)
from core.scoring import build_fundamental_facts, build_tab_prompt, get_recommendation


class GeneratePdfTests(unittest.TestCase):
    def test_scoring_context_uses_equal_fundamental_and_technical_weights(self):
        report = apply_scoring_to_report(
            {
                "ticker": "AAA",
                "company_name": "Công ty AAA",
                "news_feed": {
                    "status": "ok",
                    "source": "Nguồn kiểm thử",
                    "period_used": "w",
                    "headlines": [
                        {"title": f"Tin kiểm thử AAA số {index}", "url": f"https://example.com/{index}"}
                        for index in range(1, 5)
                    ],
                },
            },
            {
                "tech": {
                    "symbol": "AAA",
                    "close": 25,
                    "MA20": 24,
                    "MA50": 23,
                    "MA200": 21,
                    "RSI": 58,
                    "MACD": 0.4,
                    "MACD_signal": 0.2,
                    "volume_ratio": 1.2,
                    "support_60d": 22,
                    "resistance_60d": 28,
                    "source": "Nguồn giá",
                },
                "fund": {
                    "symbol": "AAA",
                    "ROE": 0.15,
                    "EPS_growth": 0.12,
                    "revenue_growth": 0.10,
                    "profit_growth": 0.11,
                    "debt_to_equity": 0.6,
                    "EPS": 2500,
                    "historical_pe": [10, 11, 12],
                    "source": "Nguồn BCTC",
                },
                "news": {
                    "symbol": "AAA",
                    "sentiment_score": 100,
                },
            },
        )

        self.assertEqual(report["scoring_weights"], {
            "fundamental": 0.5,
            "technical": 0.5,
        })
        self.assertRegex(report["report_date"], r"^\d{2}/\d{2}/\d{4}$")
        self.assertIn("fundamental", report["scores"])
        self.assertIn("technical", report["scores"])
        self.assertTrue(all(
            group["name"] in ("fundamental", "technical")
            for group in report["score_breakdown"]["groups"]
        ))
        self.assertFalse(report["ai_generated"])
        self.assertIn("Nguồn giá", report["data_sources"])
        self.assertIn("Nguồn BCTC", report["data_sources"])
        self.assertEqual(
            [(group["category"], group["weight"]) for group in report["technical_factor_groups"]],
            [("Xu hướng", 45), ("Động lượng", 25), ("Khối lượng", 20), ("Hỗ trợ/kháng cự", 10)],
        )
        rsi_factor = next(
            factor
            for group in report["technical_factor_groups"]
            for factor in group["factors"]
            if factor["label"] == "RSI (14)"
        )
        self.assertEqual(rsi_factor["score"], 100)
        self.assertEqual(rsi_factor["points"], 50)

        from jinja2 import Environment, FileSystemLoader, select_autoescape

        template = Environment(
            loader=FileSystemLoader(str(Path(__file__).parent)),
            autoescape=select_autoescape(["html", "xml"]),
        ).get_template("report_template.html")
        rendered = template.render(**report)
        self.assertIn("Điểm / 100", rendered)
        self.assertIn("RSI (14) (15%)", rendered)
        self.assertNotIn("+50 điểm", rendered)
        self.assertIn("Số tin: 4", rendered)
        self.assertIn("Phạm vi tìm tối đa: 7 ngày", rendered)
        for index in range(1, 5):
            self.assertIn(f"Tin kiểm thử AAA số {index}", rendered)

    def test_stock_report_calculates_technical_inputs_from_closed_history(self):
        import pandas as pd

        dates = pd.date_range("2025-01-01", periods=240, freq="B")
        closes = [10 + index * 0.05 for index in range(len(dates))]
        closes[-1] = closes[-2] + 1
        history = pd.DataFrame(
            {
                "symbol": ["AAA"] * len(dates),
                "date": dates,
                "open": closes,
                "high": [close + 0.2 for close in closes],
                "low": [close - 0.2 for close in closes],
                "close": closes,
                "volume": [1000] * (len(dates) - 1) + [2000],
            }
        )
        with (
            patch("core.report_pdf.load_price_history", return_value=history),
            patch("core.report_pdf._load_financial_data", return_value=(
                [], [], {}, "", {}, "saved_financial_data", None
            )),
            patch("core.report_pdf.load_realtime_quote", return_value=None),
            patch("core.report_pdf.is_vietnam_trading_session", return_value=False),
            patch("core.report_pdf.load_symbol_metadata", return_value={}),
            patch(
                "core.report_pdf.load_or_fetch_company_metadata",
                return_value=({}, None),
            ),
            patch("core.report_pdf.fetch_stock_news", return_value={
                "symbol": "AAA",
                "status": "ok",
                "source": "DuckDuckGo News",
                "fetched_at": "2026-10-13T10:00:00+00:00",
                "headlines": [{
                    "title": "Tin doanh nghiệp AAA",
                    "url": "https://example.com/aaa",
                    "published_at": "2026-10-13",
                    "source": "Báo Kinh tế",
                    "summary": "Tóm tắt tin.",
                    "relevance": "direct",
                }],
            }) as news_fetcher,
        ):
            report = load_stock_report_data("AAA")

        technical = report["tech"]
        self.assertAlmostEqual(technical["MA200"], sum(closes[-200:]) / 200)
        self.assertAlmostEqual(technical["volume_ratio"], 2.0)
        self.assertEqual(technical["breakout_20d"], True)
        self.assertIsNotNone(technical["RSI"])
        self.assertIsNotNone(technical["MACD_signal"])
        self.assertEqual(technical["signal_as_of"], dates[-1].strftime("%Y-%m-%d"))
        self.assertEqual(report["news_feed"]["source"], "DuckDuckGo News")
        self.assertEqual(report["news_feed"]["status"], "ok")
        self.assertEqual(report["news_list"][0]["source"], "Báo Kinh tế")
        self.assertEqual(report["news_list"][0]["url"], "https://example.com/aaa")
        news_fetcher.assert_called_once_with("AAA", limit=8, period="d")

    def test_net_profit_series_uses_consolidated_income_statement(self):
        import pandas as pd

        project_root = Path(__file__).resolve().parent.parent
        workbook_path = project_root / "data" / "processed" / "tv2_financial_data.xlsx"

        for ticker in ("HPG", "FPT"):
            with self.subTest(ticker=ticker):
                years, sections, ratios, _, chart_data, _, _ = _load_financial_data(
                    ticker, workbook_path, fetch_missing=False
                )
                income_statement = next(
                    section for section in sections
                    if "KINH DOANH" in section["title"]
                )
                net_profit_row = next(
                    row for row in income_statement["rows"]
                    if "Lợi nhuận sau thuế" in row["label"]
                )
                latest_year = years[-1]
                row_profit = float(
                    net_profit_row["values"][-1].replace(",", "")
                )

                historical_valuation = chart_data["historical_valuation"]
                for field in ("PE", "PB", "EPS", "BVPS"):
                    self.assertEqual(len(historical_valuation[field]), len(years))
                self.assertGreater(
                    historical_valuation["EPS"][-1],
                    0,
                )
                self.assertGreater(
                    historical_valuation["BVPS"][-1],
                    0,
                )

                self.assertIn("hợp nhất", net_profit_row["label"])
                self.assertAlmostEqual(
                    chart_data["net_profit"][latest_year], row_profit, places=1
                )
                self.assertGreater(chart_data["net_profit"][latest_year], 1000)
                cash_flow_section = next(
                    section
                    for section in sections
                    if "LƯU CHUYỂN TIỀN TỆ" in section["title"]
                )
                cash_flow_rows = {
                    row["label"]: row["values"]
                    for row in cash_flow_section["rows"]
                }
                cash_flow_item_ids = {
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh": "operating_cash_flow",
                    "Lưu chuyển tiền thuần từ hoạt động đầu tư": "investing_cash_flow",
                    "Lưu chuyển tiền thuần từ hoạt động tài chính": "financing_cash_flow",
                }
                self.assertEqual(
                    set(cash_flow_rows),
                    set(cash_flow_item_ids),
                )
                for values in cash_flow_rows.values():
                    self.assertEqual(len(values), len(years))
                    self.assertNotIn("N/A", values)
                cash_flow_source = pd.read_csv(
                    project_root / "data" / "raw" / f"{ticker}_cash_flow.csv"
                ).set_index("item_id")
                for label, item_id in cash_flow_item_ids.items():
                    self.assertAlmostEqual(
                        float(cash_flow_rows[label][0].replace(",", "")),
                        float(cash_flow_source.loc[item_id, years[-1]]) / 1_000_000_000,
                        places=1,
                    )
                    self.assertAlmostEqual(
                        float(cash_flow_rows[label][-1].replace(",", "")),
                        float(cash_flow_source.loc[item_id, years[0]]) / 1_000_000_000,
                        places=1,
                    )
                result = get_recommendation(
                    {},
                    {
                        "ROE": ratios["roe"],
                        "EPS_growth": ratios["eps_growth"],
                        "revenue_growth": ratios["revenue_growth"],
                        "PE": ratios["pe_ratio"],
                    },
                    {},
                    use_ai=False,
                )
                factors = {
                    factor["label"]: factor
                    for factor in result["score_breakdown"]["fundamental_factors"]
                }
                self.assertNotEqual(
                    factors["Hiệu quả sinh lời (ROE)"]["evidence"],
                    "Chưa có dữ liệu",
                )
                self.assertNotEqual(
                    factors["Tăng trưởng EPS"]["evidence"],
                    "Chưa có dữ liệu",
                )
                self.assertNotEqual(
                    factors["Tăng trưởng doanh thu"]["evidence"],
                    "Chưa có dữ liệu",
                )
                self.assertNotIn("P/E định giá", factors)

    def test_bank_balance_sheet_derives_missing_liabilities_from_assets_and_equity(self):
        import pandas as pd

        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "bank_financial_data.xlsx"
            with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
                pd.DataFrame(
                    {
                        "ticker": ["VCB"],
                        "item": ["Thu nhập lãi thuần"],
                        "item_id": ["net_interest_income"],
                        "2023": [10_000_000_000],
                        "2024": [12_000_000_000],
                    }
                ).to_excel(writer, sheet_name="income_statement", index=False)
                pd.DataFrame(
                    {
                        "ticker": ["VCB", "VCB", "VCB", "VCB"],
                        "item": [
                            "Tổng tài sản",
                            "Vốn và các quỹ",
                            "Cho vay khách hàng",
                            "Tiền gửi khách hàng",
                        ],
                        "item_id": [
                            "total_assets",
                            "capital_and_reserves",
                            "loans_advances_and_finance_leases_to_customers",
                            "deposits_from_customers",
                        ],
                        "2023": [
                            100_000_000_000,
                            10_000_000_000,
                            60_000_000_000,
                            70_000_000_000,
                        ],
                        "2024": [
                            120_000_000_000,
                            15_000_000_000,
                            75_000_000_000,
                            85_000_000_000,
                        ],
                    }
                ).to_excel(writer, sheet_name="balance_sheet", index=False)
                for sheet_name in ("cash_flow", "ratio"):
                    pd.DataFrame(
                        columns=["ticker", "item", "item_id", "2023", "2024"]
                    ).to_excel(writer, sheet_name=sheet_name, index=False)

            _, sections, _, _, _, _, _ = _load_financial_data(
                "VCB", workbook_path, fetch_missing=False
            )

        balance_sheet = next(
            section
            for section in sections
            if "CÂN ĐỐI KẾ TOÁN" in section["title"]
        )
        rows = {row["label"]: row["values"] for row in balance_sheet["rows"]}
        self.assertEqual(rows["Nợ phải trả"], ["90.0", "105.0"])
        self.assertEqual(rows["Vốn chủ sở hữu"], ["10.0", "15.0"])
        self.assertIn("Cho vay khách hàng", rows)
        self.assertIn("Tiền gửi khách hàng", rows)

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
            self.assertIn("ĐIỂM TỔNG HỢP", html_sources[0])
            self.assertIn("50% CƠ BẢN + 50% KỸ THUẬT", html_sources[0])
            self.assertNotIn("news_factors", html_sources[0])
            self.assertNotIn("Nguồn dữ liệu:", html_sources[0])
            self.assertNotIn("Nguồn giá:", html_sources[0])
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

    def test_load_stock_report_data_uses_sample_context_fallback(self):
        import pandas as pd

        history = pd.DataFrame(
            {
                "symbol": ["FPT"],
                "date": [pd.Timestamp("2026-10-09")],
                "open": [99.0],
                "high": [102.0],
                "low": [98.0],
                "close": [100.0],
                "volume": [1000],
                "MA20": [99.0],
                "MA50": [98.0],
                "RSI14": [55.0],
                "MACD": [1.0],
                "MACD_signal": [0.5],
            }
        )
        financial_sections = [
            {
                "title": "KẾT QUẢ KINH DOANH (TỶ ĐỒNG)",
                "rows": [{"label": "Doanh thu thuần", "values": ["10.0"]}],
            }
        ]
        with (
            patch("core.report_pdf.load_price_history", return_value=history),
            patch("core.report_pdf._load_financial_data", return_value=(
                ["2024"], financial_sections, {
                    "pe_ratio": 12.0,
                    "beta": 0.64,
                    "roe": 12.0,
                    "eps_growth": 0.16,
                    "revenue_growth": 0.2,
                    "profit_growth": 0.1,
                    "debt_to_equity": 0.5,
                    "gross_margin": 35.0,
                    "net_margin": 12.0,
                    "current_ratio": 1.7,
                    "operating_cash_flow": 2500.0,
                },
                "", {}, "saved_financial_data", None
            )) as financial_loader,
            patch("core.report_pdf.load_realtime_quote", return_value=None),
            patch("core.report_pdf.is_vietnam_trading_session", return_value=False),
            patch(
                "core.report_pdf.load_symbol_metadata",
                return_value={"exchange": "HOSE"},
            ),
            patch(
                "core.report_pdf.load_or_fetch_company_metadata",
                return_value=({"industry": "Technology"}, None),
            ),
            patch("core.report_pdf.fetch_stock_news", return_value={
                "symbol": "FPT",
                "status": "empty",
                "source": "DuckDuckGo News",
                "headlines": [],
                "error": None,
            }),
        ):
            report = load_stock_report_data("FPT")

        self.assertEqual(report["ticker"], "FPT")
        self.assertEqual(report["exchange"], "HOSE")
        self.assertEqual(report["industry"], "Công nghệ")
        self.assertEqual(report["recommendation"], "MUA")
        self.assertEqual(report["scores"]["technical"], 45)
        self.assertGreater(len(report["investment_points"]), 0)
        self.assertEqual(report["financial_sections"], financial_sections)
        self.assertEqual(report["financial_data_source"], "Báo cáo tài chính đã lưu")
        self.assertEqual(report["fundamental_metrics"]["pe"], 12.0)
        self.assertEqual(report["fundamental_metrics"]["beta"], 0.64)
        self.assertAlmostEqual(report["fund"]["ROE"], 0.12)
        self.assertEqual(report["fund"]["EPS_growth"], 0.16)
        self.assertEqual(report["fund"]["revenue_growth"], 0.2)
        self.assertEqual(report["fund"]["debt_to_equity"], 0.5)
        self.assertEqual(report["fund"]["gross_margin"], 0.35)
        self.assertEqual(report["fund"]["net_margin"], 0.12)
        self.assertEqual(report["fund"]["current_ratio"], 1.7)
        self.assertEqual(report["fund"]["operating_cash_flow"], 2500.0)
        self.assertEqual(report["beta"], "0.64")
        self.assertEqual(report["current_price"], "100,000 đ")
        self.assertEqual(report["news_feed"]["status"], "empty")
        self.assertEqual(report["news_list"], [])
        rsi_metric = next(
            metric for metric in report["technical_metrics"]
            if metric["label"] == "RSI (14)"
        )
        self.assertEqual(rsi_metric["value"], "55.00")
        score = get_recommendation(
            report["tech"], report["fund"], {}, use_ai=False
        )
        score_factors = score["score_breakdown"]["fundamental_factors"]
        factors_by_label = {factor["label"]: factor for factor in score_factors}
        self.assertEqual(factors_by_label["Hiệu quả sinh lời (ROE)"]["evidence"], "12.0%")
        self.assertEqual(factors_by_label["Tăng trưởng EPS"]["evidence"], "+16.0%")
        self.assertEqual(factors_by_label["Tăng trưởng doanh thu"]["evidence"], "+20.0%")
        self.assertFalse(any("P/E" in factor["label"] for factor in score_factors))
        legacy_percentage_score = get_recommendation(
            {},
            {"ROE": 12.69, "EPS_growth": 0.129, "revenue_growth": 0.124},
            {},
            use_ai=False,
        )
        legacy_roe = next(
            factor
            for factor in legacy_percentage_score["score_breakdown"][
                "fundamental_factors"
            ]
            if factor["label"] == "Hiệu quả sinh lời (ROE)"
        )
        self.assertEqual(legacy_roe["evidence"], "12.7%")
        financial_loader.assert_called_once()
        self.assertTrue(financial_loader.call_args.kwargs["fetch_missing"])

    def test_fundamental_ai_prompt_covers_key_metrics_without_valuation(self):
        facts = build_fundamental_facts(
            {
                "symbol": "AAA",
                "ROE": 0.1269,
                "ROA": 0.0641,
                "EPS_growth": 0.129,
                "revenue_growth": 0.124,
                "profit_growth": 0.29,
                "net_profit": 15514.9,
                "net_profit_year": "2025",
                "EPS": 2203.59,
                "gross_margin": 0.18,
                "net_margin": 0.10,
                "debt_to_equity": 0.7,
                "current_ratio": 1.5,
                "operating_cash_flow": 12000,
            },
            {},
        )
        prompt = build_tab_prompt("fundamental", facts)

        for metric in (
            "ROE", "ROA", "Tăng trưởng EPS", "tăng trưởng doanh thu",
            "tăng trưởng lợi nhuận", "Biên lợi nhuận gộp",
            "biên lợi nhuận ròng", "Nợ vay/VCSH",
            "thanh toán hiện hành", "Dòng tiền kinh doanh",
            "NIM", "NPL", "CAR",
        ):
            self.assertIn(metric, prompt)
        self.assertIn("không đánh giá ở đây", prompt)


if __name__ == "__main__":
    unittest.main()
