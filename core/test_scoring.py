import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from core.scoring import (
    GEMINI_CANDIDATE_MODELS,
    _gemini,
    _cached_ai_for_tabs,
    call_ai_for_tabs,
    build_tab_prompt,
    build_fundamental_facts,
    build_technical_facts,
    build_valuation_facts,
    compute_target_price,
    fallback_summary,
    get_dynamic_weights,
    get_recommendation,
    to_report_context,
)


class TechnicalScoringTests(unittest.TestCase):
    def test_tab_commentaries_share_one_gemini_request(self):
        tabs = ("general", "technical", "fundamental", "valuation")
        analyses = {
            tab_type: {
                "tech": {"symbol": f"TEST_{tab_type}"},
                "fund": {},
                "result": {},
            }
            for tab_type in tabs
        }
        response = "\n".join(
            f"<<<{tab_type.upper()}>>>commentary:{tab_type}"
            f"<<<END_{tab_type.upper()}>>>"
            for tab_type in tabs
        )
        _cached_ai_for_tabs.clear()
        with patch("core.scoring._gemini", return_value=response) as gemini:
            commentaries = call_ai_for_tabs(analyses)

        self.assertEqual(
            commentaries,
            {tab_type: f"commentary:{tab_type}" for tab_type in tabs},
        )
        gemini.assert_called_once()

    def test_failed_ai_response_is_not_cached(self):
        analyses = {
            "general": {
                "tech": {"symbol": "TEST_RETRY"},
                "fund": {},
                "result": {},
            }
        }
        response = "<<<GENERAL>>>Retried successfully<<<END_GENERAL>>>"
        _cached_ai_for_tabs.clear()
        with patch(
            "core.scoring._gemini",
            side_effect=[RuntimeError("503 UNAVAILABLE"), response],
        ) as gemini:
            failed = call_ai_for_tabs(analyses)
            succeeded = call_ai_for_tabs(analyses)

        self.assertIn("503 UNAVAILABLE", failed["general"])
        self.assertEqual(succeeded["general"], "Retried successfully")
        self.assertEqual(gemini.call_count, 2)

    def test_missing_ai_section_is_retried_without_discarding_other_sections(self):
        analyses = {
            tab_type: {
                "tech": {"symbol": f"TEST_PARTIAL_{tab_type}"},
                "fund": {},
                "result": {},
            }
            for tab_type in ("general", "technical", "fundamental", "valuation")
        }
        partial_response = "\n".join(
            f"<<<{tab_type.upper()}>>>commentary:{tab_type}"
            f"<<<END_{tab_type.upper()}>>>"
            for tab_type in ("technical", "fundamental", "valuation")
        )
        _cached_ai_for_tabs.clear()
        with patch(
            "core.scoring._gemini",
            side_effect=[partial_response, "General nhận xét được tạo lại."],
        ) as gemini:
            commentaries = call_ai_for_tabs(analyses)

        self.assertEqual(
            commentaries,
            {
                "general": "General nhận xét được tạo lại.",
                "technical": "commentary:technical",
                "fundamental": "commentary:fundamental",
                "valuation": "commentary:valuation",
            },
        )
        self.assertEqual(gemini.call_count, 2)

    def test_gemini_tries_only_configured_generation_three_models_without_listing(self):
        response = SimpleNamespace(text="  Nhận định kiểm thử  ")
        generate_content = Mock(
            side_effect=[
                RuntimeError("model not found"),
                response,
            ]
        )
        models = SimpleNamespace(generate_content=generate_content)
        fake_genai = ModuleType("google.genai")
        fake_genai.Client = Mock(return_value=SimpleNamespace(models=models))
        fake_google = ModuleType("google")
        fake_google.genai = fake_genai

        with (
            patch.dict("sys.modules", {
                "google": fake_google,
                "google.genai": fake_genai,
            }),
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}),
        ):
            result = _gemini("prompt")

        self.assertEqual(result, "Nhận định kiểm thử")
        self.assertEqual(
            [call.kwargs["model"] for call in generate_content.call_args_list],
            list(GEMINI_CANDIDATE_MODELS),
        )
        self.assertTrue(all(
            model.startswith("gemini-3.")
            for model in GEMINI_CANDIDATE_MODELS
        ))

    def test_risk_points_are_deduplicated_and_explain_indicator_implications(self):
        result = get_recommendation(
            {
                "signal_close": 9.0,
                "MA20": 10.0,
                "MA50": 11.0,
                "MA200": 12.0,
                "ma50_ma200_cross": "death_cross",
                "RSI": 75.0,
                "MACD": -0.3,
                "MACD_signal": 0.1,
                "volume_ratio": 1.5,
                "price_change_pct": -0.03,
                "breakout_20d": False,
                "support_distance_pct": -0.02,
                "resistance_distance_pct": 0.1,
            },
            {},
            use_ai=False,
        )

        risks = result["risks"]
        self.assertEqual(len(risks), len(set(risks)))
        self.assertTrue(any("áp lực ngắn hạn" in risk for risk in risks))
        self.assertTrue(any("trung hạn chưa xác nhận trạng thái tăng" in risk for risk in risks))
        self.assertTrue(any("áp lực bán được xác nhận" in risk for risk in risks))

    def test_ai_prompts_require_interpretation_instead_of_relisting_metrics(self):
        general_prompt = build_tab_prompt(
            "general",
            "Điểm cơ bản 82, điểm kỹ thuật 30; doanh thu tăng 12.4%; RSI 45.",
        )
        fundamental_facts = build_fundamental_facts(
            {
                "symbol": "AAA",
                "valuation_type": "non-bank",
                "ROE": 0.15,
                "revenue_growth": 0.12,
                "debt_to_equity": 0.7,
            },
            {"scores": {"fundamental": 70}},
        )
        fundamental_prompt = build_tab_prompt("fundamental", fundamental_facts)

        self.assertIn("Không chép lại bảng số liệu", general_prompt)
        self.assertIn("Tối đa 2 con số", general_prompt)
        self.assertIn("không lần lượt nhắc ROE, ROA, EPS", fundamental_prompt)
        self.assertNotIn("NIM:", fundamental_facts)

    def test_overall_score_uses_fixed_equal_weights_and_ignores_news(self):
        tech = {
            "symbol": "AAA",
            "signal_close": 11.0,
            "MA20": 10.0,
            "MA50": 9.0,
            "MA200": 8.0,
            "ma50_ma200_cross": "golden_cross",
            "RSI": 55.0,
            "MACD": 0.5,
            "MACD_signal": 0.2,
            "volume_ratio": 1.6,
            "price_change_pct": 0.04,
            "breakout_20d": True,
            "support_distance_pct": 0.02,
            "resistance_distance_pct": 0.10,
        }
        fund = {
            "symbol": "AAA",
            "ROE": 0.20,
            "ROA": 0.10,
            "EPS_growth": 0.20,
            "revenue_growth": 0.15,
            "profit_growth": 0.15,
            "gross_margin": 0.20,
            "net_margin": 0.10,
            "debt_to_equity": 0.50,
            "current_ratio": 2.0,
            "operating_cash_flow": 5.0,
        }

        positive_news = {"headlines": [{"title": "Lợi nhuận tăng trưởng, vượt kế hoạch"}]}
        negative_news = {"headlines": [{"title": "Báo lỗ, lợi nhuận giảm mạnh"}]}
        positive_result = get_recommendation(tech, fund, positive_news, use_ai=False)
        negative_result = get_recommendation(tech, fund, negative_news, use_ai=False)

        self.assertEqual(get_dynamic_weights({"valuation_type": "bank"}), {
            "fundamental": 0.50,
            "technical": 0.50,
        })
        self.assertEqual(positive_result["total_score"], negative_result["total_score"])
        self.assertEqual(
            positive_result["recommendation"],
            negative_result["recommendation"],
        )
        self.assertEqual(positive_result["scores"], negative_result["scores"])
        self.assertNotIn("news", positive_result["scores"])
        self.assertNotIn("headline", " ".join(positive_result["reasons"]).lower())
        self.assertAlmostEqual(
            positive_result["total_score"],
            positive_result["scores"]["fundamental"] * 0.5
            + positive_result["scores"]["technical"] * 0.5,
            places=1,
        )

    def test_missing_scoring_pillar_does_not_get_renormalized(self):
        tech = {
            "signal_close": 11.0,
            "MA20": 10.0,
            "MA50": 9.0,
            "MA200": 8.0,
            "ma50_ma200_cross": "golden_cross",
            "RSI": 55.0,
            "MACD": 0.5,
            "MACD_signal": 0.2,
            "volume_ratio": 1.6,
            "price_change_pct": 0.04,
            "breakout_20d": True,
            "support_distance_pct": 0.02,
            "resistance_distance_pct": 0.10,
        }
        result = get_recommendation(tech, {}, use_ai=False)
        report_context = to_report_context(result, tech, {})

        self.assertIsNotNone(result["scores"]["technical"])
        self.assertIsNone(result["scores"]["fundamental"])
        self.assertIsNone(result["total_score"])
        self.assertEqual(result["recommendation"], "GIỮ")
        self.assertEqual(result["score_breakdown"]["available_weight"], 0.5)
        self.assertIn("chưa đủ dữ liệu để tính điểm tổng 50/50", fallback_summary(result))
        self.assertIn("chưa đủ dữ liệu để tính điểm tổng 50/50", report_context["investment_thesis"])
        self.assertNotIn("None/100", report_context["investment_thesis"])

    def test_technical_score_uses_all_weighted_groups_and_real_evidence(self):
        tech = {
            "symbol": "AAA",
            "signal_close": 11.0,
            "MA20": 10.0,
            "MA50": 9.0,
            "MA200": 8.0,
            "ma50_ma200_cross": "golden_cross",
            "RSI": 55.0,
            "MACD": 0.5,
            "MACD_signal": 0.2,
            "volume_ratio": 1.6,
            "price_change_pct": 0.04,
            "breakout_20d": True,
            "support_distance_pct": 0.02,
            "resistance_distance_pct": 0.10,
        }

        result = get_recommendation(tech, {}, {}, use_ai=False)
        factors = result["score_breakdown"]["technical_factors"]

        self.assertEqual(result["scores"]["technical"], 97)
        self.assertEqual(result["score_breakdown"]["technical_coverage"], 100)
        self.assertEqual(sum(factor["weight"] for factor in factors), 100)
        self.assertTrue(all(factor["score"] is not None for factor in factors))
        self.assertIn(
            "Golden Cross mới xác nhận",
            next(factor["evidence"] for factor in factors if factor["label"] == "Giao cắt MA50/MA200"),
        )

    def test_oversold_rsi_is_not_treated_as_an_automatic_buy_signal(self):
        result = get_recommendation(
            {
                "signal_close": 9,
                "MA20": 10,
                "MA50": 11,
                "RSI": 24,
            },
            {},
            {},
            use_ai=False,
        )
        rsi_factor = next(
            factor
            for factor in result["score_breakdown"]["technical_factors"]
            if factor["label"] == "RSI (14)"
        )

        self.assertEqual(rsi_factor["score"], 35)
        self.assertLess(rsi_factor["points"], 0)
        self.assertIn("cần tín hiệu xác nhận đảo chiều", rsi_factor["text"])

    def test_price_rally_with_low_volume_is_flagged_as_weak_confirmation(self):
        result = get_recommendation(
            {
                "signal_close": 10,
                "MA20": 9,
                "MA50": 8,
                "RSI": 55,
                "MACD": 0.2,
                "MACD_signal": 0.1,
                "volume_ratio": 0.6,
                "price_change_pct": 0.02,
                "breakout_20d": False,
            },
            {},
            {},
            use_ai=False,
        )
        volume_factor = next(
            factor
            for factor in result["score_breakdown"]["technical_factors"]
            if factor["label"] == "Giá và khối lượng"
        )

        self.assertEqual(volume_factor["score"], 30)
        self.assertIn("lực xác nhận yếu", volume_factor["text"])

    def test_insufficient_indicator_coverage_does_not_produce_a_score(self):
        result = get_recommendation(
            {"signal_close": 10, "MA20": 9},
            {},
            {},
            use_ai=False,
        )

        self.assertIsNone(result["scores"]["technical"])
        self.assertEqual(result["score_breakdown"]["technical_coverage"], 10)

    def test_technical_ai_prompt_receives_actual_indicators_and_limitations(self):
        tech = {
            "symbol": "AAA",
            "signal_as_of": "2026-10-12",
            "source": "market-data",
            "signal_close": 10.2,
            "MA20": 10.0,
            "MA50": 9.8,
            "MA200": 9.2,
            "ma50_ma200_cross": "none",
            "RSI": 57.0,
            "MACD": 0.12,
            "MACD_signal": 0.08,
            "volume_ratio": 1.4,
            "price_change_pct": 0.01,
            "breakout_20d": False,
            "support_60d": 9.5,
            "support_distance_pct": 0.07,
            "resistance_60d": 10.5,
            "resistance_distance_pct": 0.03,
        }
        result = get_recommendation(tech, {}, {}, use_ai=False)
        facts = build_technical_facts(tech, result)
        prompt = build_tab_prompt("technical", facts)

        for evidence in ("2026-10-12", "MA200 9.20", "RSI(14) 57.00", "1.40x", "10.5"):
            self.assertIn(evidence, facts)
        self.assertIn("không phải vùng hỗ trợ/kháng cự đã được xác nhận", prompt)
        self.assertIn("không khẳng định chắc chắn xu hướng tương lai", prompt)
        self.assertIn("đúng 4 gạch đầu dòng ngắn", prompt)

    def test_valuation_ai_interprets_relative_value_and_method_limits(self):
        tech = {"symbol": "AAA", "close": 20.0}
        fund = {
            "EPS": 2000,
            "PE": 10.0,
            "historical_PE": [12, 14, 16, None, "invalid"],
        }
        result = compute_target_price(tech, fund)
        facts = build_valuation_facts(tech, fund, result)
        prompt = build_tab_prompt("valuation", facts)

        self.assertEqual(result["valuation_reference_pe"], 14)
        self.assertEqual(result["valuation_history_count"], 3)
        self.assertIn("P/E hiện tại: 10.00 lần", facts)
        self.assertIn("P/E trung vị lịch sử: 14.00 lần", facts)
        self.assertEqual(result["valuation_current_pe"], 10.0)
        self.assertIn("EPS gần nhất; chỉ có 0 năm EPS dương", facts)
        self.assertIn("không gộp trọng số", facts)
        self.assertIn("giải thích ý nghĩa", prompt)
        self.assertIn("không phải biên an toàn chắc chắn", prompt)

    def test_recommendation_exposes_historical_pe_to_valuation_tab(self):
        tech = {"symbol": "AAA", "close": 20.0}
        fund = {
            "EPS": 2000,
            "historical_PE": [12, 14, 16, None, "invalid"],
        }
        result = get_recommendation(
            tech,
            fund,
            {},
            use_ai=False,
        )
        report_context = to_report_context(result, tech, fund)

        self.assertEqual(result["valuation_reference_pe"], 14)
        self.assertEqual(result["valuation_history_count"], 3)
        self.assertIn("P/E trung vị 14.00 lần", result["target_method"])
        self.assertEqual(report_context["valuation_reference_pe"], 14)
        self.assertEqual(report_context["valuation_history_count"], 3)
        self.assertEqual(report_context["target_price_range"], "26,000 đ – 30,000 đ")
        self.assertEqual(report_context["target_price_range_values"], [26000.0, 30000.0])
        self.assertEqual(report_context["upside_range"], "+30.0% đến +50.0%")

    def test_valuation_uses_nested_historical_series_when_direct_key_is_missing(self):
        result = compute_target_price(
            {"close": 20.0},
            {
                "EPS": 2000,
                "historical_valuation": {
                    "PE": [11, 13, 15, 17],
                    "EPS": [1800, 1900, 2100, 2200],
                },
            },
        )

        self.assertEqual(result["valuation_reference_pe"], 14)
        self.assertEqual(result["valuation_history_count"], 4)
        self.assertEqual(result["valuation_eps"], 2000)
        self.assertEqual(result["valuation_eps_count"], 4)

    def test_valuation_uses_normalized_eps_range_and_pb_cross_check(self):
        result = compute_target_price(
            {"close": 10.0},
            {
                "EPS": 400,
                "BVPS": 3125,
                "historical_EPS": [100, 200, 300, 400],
                "historical_PE": [10, 20, 30, 40],
                "historical_PB": [1, 2, 3],
            },
        )

        self.assertEqual(result["valuation_eps"], 250)
        self.assertEqual(result["valuation_eps_count"], 4)
        self.assertEqual(result["valuation_reference_pe"], 25)
        self.assertEqual(result["target_price_range"], (4.38, 8.12))
        self.assertEqual(result["valuation_pb_reference"], 2)
        self.assertEqual(result["valuation_pb_target"], 6.25)
        # Chỉ 4 kỳ P/E (< 8) nên độ tin cậy phải là "Thấp"
        self.assertEqual(result["confidence"], "Thấp")
        self.assertIn("Chỉ có 4 kỳ P/E", result["valuation_warning"])

    def test_valuation_normalizes_mwg_eps_and_warns_on_large_range(self):
        result = compute_target_price(
            {"close": 9.42},
            {
                "EPS": 4772.80,
                "BVPS": 22439.69,
                "historical_EPS": [3646.08, 114.61, 2545.57, 4772.80],
                "historical_PE": [11.77, 373.43, 23.96, 18.52],
                "historical_PB": [2.62, 2.68, 3.17, 3.94],
            },
        )

        self.assertEqual(result["valuation_history_count"], 3)
        self.assertEqual(result["valuation_reference_pe"], 18.52)
        self.assertEqual(result["valuation_eps"], 3095.825)
        self.assertGreater(result["target_price_range"][1], result["target_price"])
        self.assertEqual(result["valuation_pb_count"], 4)
        self.assertIsNotNone(result["valuation_pb_target"])
        self.assertEqual(result["confidence"], "Thấp")
        self.assertIn("cảnh báo 50%", result["valuation_warning"])

    def test_large_upside_does_not_trigger_buy_recommendation(self):
        result = get_recommendation(
            {
                "signal_close": 11.0,
                "close": 11.0,
                "MA20": 10.0,
                "MA50": 9.0,
                "MA200": 8.0,
                "ma50_ma200_cross": "golden_cross",
                "RSI": 55.0,
                "MACD": 0.5,
                "MACD_signal": 0.2,
                "volume_ratio": 1.6,
                "price_change_pct": 0.04,
                "breakout_20d": True,
                "support_distance_pct": 0.02,
                "resistance_distance_pct": 0.10,
            },
            {
                "symbol": "DIG",
                "EPS": 1023,
                "historical_PE": [54.24, 137.40, 100.63, 16.37],
                "ROE": 0.25,
                "EPS_growth": 0.2,
                "revenue_growth": 0.2,
                "debt_to_equity": 0.5,
            },
            use_ai=False,
        )

        self.assertEqual(result["recommendation"], "GIỮ")
        self.assertIn("P/E", result["decision_note"])

    def test_valuation_has_medium_confidence_at_or_below_fifty_percent(self):
        result = compute_target_price(
            {"close": 20.0},
            {
                "EPS": 2000,
                "historical_PE": [12, 14, 16],
            },
        )

        self.assertEqual(result["target_price"], 28.0)
        self.assertEqual(result["upside"], 0.4)
        self.assertEqual(result["confidence"], "Thấp")
        self.assertIn("chưa đủ dữ liệu chuẩn hóa", result["valuation_warning"])

    def test_valuation_still_excludes_extreme_historical_pe_above_150(self):
        result = compute_target_price(
            {"close": 20.0},
            {
                "EPS": 2000,
                "historical_PE": [12, 14, 16, 151],
            },
        )

        self.assertEqual(result["valuation_history_count"], 3)
        self.assertEqual(result["valuation_reference_pe"], 14)

    def test_valuation_ai_explicitly_flags_insufficient_history(self):
        tech = {"symbol": "AAA", "close": 20.0}
        fund = {"EPS": 2000, "historical_PE": [12, 14]}
        result = compute_target_price(tech, fund)
        facts = build_valuation_facts(tech, fund, result)

        self.assertIsNone(result["target_price"])
        self.assertIn("P/E trung vị lịch sử: 13.00 lần", facts)
        self.assertIn("chỉ có 2 kỳ P/E hợp lệ", facts)

    def test_bank_valuation_commentary_warns_pe_needs_pb_roe_cross_check(self):
        facts = build_valuation_facts(
            {"symbol": "ABC", "close": 20},
            {"EPS": 2000, "valuation_type": "bank"},
            compute_target_price(
                {"close": 20},
                {"EPS": 2000, "historical_PE": [10, 11, 12]},
            ),
        )

        self.assertIn("nhóm ngân hàng", facts)
        self.assertIn("P/B và ROE", facts)

class ShareAdjustmentAndBankTests(unittest.TestCase):
    """Kiểm chứng bằng số liệu ACB in trong báo cáo ngày 14/10/2026."""

    ACB_FUND = {
        "symbol": "ACB",
        "valuation_type": "bank",
        "EPS": 3230,
        "BVPS": 18401,
        "historical_EPS": [4442, 4369, 3975, 3230],
        "historical_PE": [4.93, 5.47, 6.49, 7.43],
        "historical_PB": [1.27, 1.31, 1.38, 1.30],
        "historical_net_profit": {
            "2022": 13688.2, "2023": 16044.7, "2024": 16789.8, "2025": 15624.7,
        },
        "equity_latest": 94519.7e9,
        "market_cap": 118119.99e9,
        "shares_outstanding": 118119.99e9 / 20450,
        "ROE": 0.1756,
        "EPS_growth": -0.187,
        "profit_growth": -0.0694,
        "revenue_growth": -0.032,
        "net_interest_margin": 0.0292,
    }

    def test_acb_eps_is_recomputed_on_current_share_count(self):
        result = compute_target_price({"close": 20.45}, self.ACB_FUND)

        self.assertAlmostEqual(result["valuation_current_pe"], 7.56, places=2)
        low, high = result["upside_range"]
        self.assertLess(high, 0)          # cả dải upside đều âm, không còn +8.8% đến +37.2%
        self.assertLess(low, -0.25)
        self.assertEqual(result["confidence"], "Thấp")
        self.assertIn("lệch", result["valuation_warning"])
        self.assertTrue(result["valuation_shares_changed"])
        self.assertAlmostEqual(result["valuation_pb_target"], 21.36, places=1)

    def test_valuation_blocked_when_share_count_contradicts_market_cap(self):
        fund = dict(self.ACB_FUND, shares_outstanding=4_000_000_000)
        result = compute_target_price({"close": 20.45}, fund)

        self.assertIsNone(result["target_price"])
        self.assertIn("không nhất quán", result["method"])

    def test_bank_scores_profit_growth_and_bank_ratios(self):
        result = get_recommendation({"symbol": "ACB", "close": 20.45}, self.ACB_FUND, use_ai=False)
        factors = {f["label"]: f for f in result["score_breakdown"]["fundamental_factors"]}

        self.assertIn("Tăng trưởng LNST", factors)
        self.assertEqual(factors["Tăng trưởng LNST"]["points"], -7)
        self.assertIn("Tăng trưởng thu nhập lãi thuần", factors)
        self.assertIn("Biên lãi thuần (NIM)", factors)
        self.assertFalse(factors["Tỷ lệ nợ xấu (NPL)"]["applied"])   # thiếu dữ liệu thì không chấm

    def test_non_bank_without_share_data_keeps_old_behaviour(self):
        result = compute_target_price(
            {"close": 20.0}, {"EPS": 2000, "historical_PE": [12, 14, 16]}
        )
        self.assertFalse(result["valuation_shares_adjusted"])
        self.assertEqual(result["target_price"], 28.0)

if __name__ == "__main__":
    unittest.main()
