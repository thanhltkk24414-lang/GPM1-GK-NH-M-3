import unittest
from unittest.mock import patch

from core.news import fetch_stock_news


class FetchStockNewsTests(unittest.TestCase):
    def test_expands_search_period_to_fill_requested_news_count(self):
        daily_item = {
            "title": "AAA ghi nhận diễn biến kinh doanh mới",
            "url": "https://news.example/aaa/latest",
            "source": "Báo A",
            "body": "Thông tin về doanh nghiệp AAA.",
        }
        weekly_items = [
            daily_item,
            {
                "title": "AAA công bố kết quả kinh doanh quý",
                "url": "https://news.example/aaa/quarter",
                "source": "Báo B",
                "body": "Bài viết về cổ phiếu AAA.",
            },
            {
                "title": "AAA cập nhật kế hoạch đầu tư mới",
                "url": "https://news.example/aaa/investment",
                "source": "Báo C",
                "body": "Doanh nghiệp AAA chia sẻ kế hoạch.",
            },
        ]

        class FakeDDGS:
            def __init__(self, timeout):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def news(self, query, **kwargs):
                return {
                    "d": [daily_item],
                    "w": weekly_items,
                    "m": [],
                    "y": [],
                }[kwargs["timelimit"]]

        with patch("core.news.DDGS", FakeDDGS):
            result = fetch_stock_news("AAA", limit=3, period="d")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["headlines"]), 3)
        self.assertEqual(
            [item["url"] for item in result["headlines"]],
            [
                "https://news.example/aaa/latest",
                "https://news.example/aaa/quarter",
                "https://news.example/aaa/investment",
            ],
        )
        self.assertEqual(result["periods_checked"], ["d", "w"])
        self.assertEqual(result["period_used"], "w")
        self.assertTrue(result["fallback_used"])


if __name__ == "__main__":
    unittest.main()
