import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from core.market_data import (
    is_vietnam_trading_session,
    load_realtime_quote,
    load_symbol_metadata,
)
from core.report_pdf import refresh_stock_report_quote


class VietnamTradingSessionTests(unittest.TestCase):
    def test_detects_vietnam_trading_hours_and_weekdays(self):
        timezone = ZoneInfo("Asia/Ho_Chi_Minh")
        self.assertTrue(
            is_vietnam_trading_session(datetime(2026, 10, 12, 9, 15, tzinfo=timezone))
        )
        self.assertTrue(
            is_vietnam_trading_session(datetime(2026, 10, 12, 14, 0, tzinfo=timezone))
        )
        self.assertFalse(
            is_vietnam_trading_session(datetime(2026, 10, 12, 12, 0, tzinfo=timezone))
        )
        self.assertFalse(
            is_vietnam_trading_session(datetime(2026, 10, 12, 16, 0, tzinfo=timezone))
        )
        self.assertFalse(
            is_vietnam_trading_session(datetime(2026, 10, 11, 10, 0, tzinfo=timezone))
        )


class SymbolMetadataTests(unittest.TestCase):
    def test_discovers_singular_symbol_csv_under_data_directory(self):
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "data").mkdir()
            pd.DataFrame(
                {"symbol": ["VNM"], "exchange": ["HOSE"]}
            ).to_csv(project_root / "data" / "symbol.csv", index=False)

            with patch("core.market_data.PROJECT_ROOT", project_root):
                metadata = load_symbol_metadata("VNM")

        self.assertEqual(metadata["exchange"], "HOSE")

    def test_reads_exchange_from_symbol_csv_with_case_insensitive_headers(self):
        with TemporaryDirectory() as temp_dir:
            listing_path = Path(temp_dir) / "symbol.csv"
            pd.DataFrame(
                {" Symbol ": ["VNM", "FPT"], "Exchange": ["HOSE", "HOSE"]}
            ).to_csv(listing_path, index=False)

            metadata = load_symbol_metadata(" vnm ", listing_path=listing_path)

        self.assertEqual(metadata["exchange"], "HOSE")
        self.assertEqual(metadata["source"], str(listing_path))

    @patch("core.market_data._fetch_vietcap_quote")
    @patch("core.market_data._cached_quote")
    @patch("core.market_data.is_vietnam_trading_session", return_value=False)
    def test_skips_realtime_sources_outside_trading_hours(
        self, _session, cached_quote, vietcap_quote
    ):
        self.assertIsNone(load_realtime_quote("FPT", database_path="unused.db"))
        cached_quote.assert_not_called()
        vietcap_quote.assert_not_called()

    @patch("core.market_data._fetch_vietcap_quote")
    @patch(
        "core.market_data._cached_quote",
        return_value={"price": 100.0, "source": "Vietcap WebSocket"},
    )
    @patch("core.market_data.is_vietnam_trading_session", return_value=True)
    def test_prefers_fresh_cached_realtime_during_trading_hours(
        self, _session, cached_quote, vietcap_quote
    ):
        quote = load_realtime_quote("FPT", database_path="unused.db")
        self.assertEqual(quote["source"], "Vietcap WebSocket")
        cached_quote.assert_called_once()
        vietcap_quote.assert_not_called()

    @patch(
        "core.market_data._fetch_vietcap_quote",
        return_value={"price": 101.0, "source": "Vietcap priceboard snapshot"},
    )
    @patch("core.market_data._cached_quote", return_value=None)
    @patch("core.market_data.is_vietnam_trading_session", return_value=True)
    def test_fetches_live_quote_when_trading_and_cache_is_empty(
        self, _session, cached_quote, vietcap_quote
    ):
        quote = load_realtime_quote("FPT", database_path="unused.db")
        self.assertEqual(quote["price"], 101.0)
        cached_quote.assert_called_once()
        vietcap_quote.assert_called_once_with("FPT")


class ReportQuoteSelectionTests(unittest.TestCase):
    def setUp(self):
        self.report_data = {
            "ticker": "FPT",
            "price_history": pd.DataFrame(
                {"date": [pd.Timestamp("2026-10-09")], "close": [100.0]}
            ),
            "technical_metrics": [
                {"label": "Giá gần nhất", "value": "100,000 đ"},
                {"label": "MA20", "value": "90,000 đ"},
            ],
        }

    @patch("core.report_pdf._apply_company_metadata", side_effect=lambda report, _: report)
    @patch("core.report_pdf.load_company_metadata", return_value={})
    @patch("core.report_pdf.load_realtime_quote", return_value=None)
    @patch("core.report_pdf.is_vietnam_trading_session", return_value=True)
    def test_does_not_substitute_history_for_current_price_in_session(
        self, _session, _quote, _metadata, _apply_metadata
    ):
        refreshed = refresh_stock_report_quote(
            self.report_data, database_path="unused.db"
        )
        self.assertEqual(refreshed["current_price"], "Chờ realtime")
        self.assertEqual(refreshed["data_as_of"], "Chờ realtime")
        self.assertEqual(refreshed["technical_metrics"][0]["value"], "Chờ realtime")
        self.assertIn("không hiển thị giá lịch sử thay thế", refreshed["price_source"])

    @patch("core.report_pdf._apply_company_metadata", side_effect=lambda report, _: report)
    @patch("core.report_pdf.load_company_metadata", return_value={})
    @patch("core.report_pdf.load_realtime_quote", return_value=None)
    @patch("core.report_pdf.is_vietnam_trading_session", return_value=False)
    def test_uses_latest_close_outside_trading_session(
        self, _session, _quote, _metadata, _apply_metadata
    ):
        refreshed = refresh_stock_report_quote(
            self.report_data, database_path="unused.db"
        )
        self.assertEqual(refreshed["current_price"], "100,000 đ")
        self.assertEqual(refreshed["data_as_of"], "09/10/2026")
        self.assertEqual(refreshed["technical_metrics"][0]["value"], "100,000 đ")
        self.assertIn("ngoài giờ giao dịch", refreshed["price_source"])


if __name__ == "__main__":
    unittest.main()
