import unittest
from unittest.mock import patch

from core.company_metadata import load_or_fetch_company_metadata


class OnDemandCompanyMetadataTests(unittest.TestCase):
    @patch("core.company_metadata.fetch_company_metadata")
    @patch("core.company_metadata.load_company_metadata")
    def test_uses_cached_industry_without_fetching_again(self, load_cached, fetch):
        load_cached.return_value = {
            "symbol": "MWG",
            "exchange": "HOSE",
            "industry": "Retail",
        }

        metadata, error = load_or_fetch_company_metadata("mwg")

        self.assertEqual(metadata["industry"], "Retail")
        self.assertIsNone(error)
        fetch.assert_not_called()

    @patch("core.company_metadata._save_metadata")
    @patch("core.company_metadata.fetch_company_metadata")
    @patch("core.company_metadata.load_company_metadata", return_value={})
    def test_caches_industry_even_when_shares_are_unavailable(
        self, _load_cached, fetch, save
    ):
        fetch.return_value = {
            "symbol": "MWG",
            "exchange": "HOSE",
            "industry": "Retail",
            "shares_outstanding": None,
            "company_name": "Mobile World Investment Corporation",
            "source": "VNStock VCI Company.overview",
            "fetched_at": "2026-10-14T03:00:00+00:00",
        }

        metadata, error = load_or_fetch_company_metadata("MWG", exchange="HOSE")

        self.assertEqual(metadata["industry"], "Retail")
        self.assertEqual(metadata["exchange"], "HOSE")
        self.assertIsNone(error)
        fetch.assert_called_once()
        save.assert_called_once_with([metadata])


if __name__ == "__main__":
    unittest.main()
