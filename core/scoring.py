"""
scoring.py - Logic Engine (TV3 - Lạc) [phiên bản 3]
Nhận dữ liệu từ TV1 (tech, news) và TV2 (fund) -> khuyến nghị MUA / GIỮ / BÁN
+ giá mục tiêu (có phương pháp dự phòng, không còn N/A) + nhận định AI
+ dữ liệu điền vào report_template.html.
Chạy thử: py core/scoring.py (đọc data/sample_FPT.json)
"""
import json
import os
import re
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv chưa cài, không làm gì cả
    load_dotenv = None


def _load_ai_env():
    """Tự động load .env ở root project trước khi kiểm tra AI key."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env_file = os.path.join(project_root, ".env")

    if load_dotenv is None:
        return

    if os.path.exists(env_file):
        load_dotenv(env_file, override=False)
    else:
        # Fallback: cố load .env từ working directory hiện tại nếu app chạy từ repo root
        load_dotenv(override=False)


_load_ai_env()

# ======================= 1. CẤU HÌNH (chỉnh ở đây) =======================
WEIGHTS = {"fundamental": 0.40, "technical": 0.35, "news": 0.25}
BUY_THRESHOLD = 65   # total >= 65 -> MUA
SELL_THRESHOLD = 45  # total < 45 -> BÁN, còn lại GIỮ

POSITIVE_WORDS = ["tăng trưởng", "lợi nhuận tăng", "vượt kế hoạch", "kỷ lục", "cổ tức",
                  "mua vào", "nâng dự báo", "khởi sắc", "hợp đồng", "mở rộng", "lãi"]
NEGATIVE_WORDS = ["giảm", "lỗ", "sụt giảm", "bán ròng", "vi phạm", "bị phạt", "thanh tra",
                  "đình chỉ", "nợ xấu", "hạ dự báo", "khó khăn", "kiện", "cảnh báo"]

# --- Cấu hình định giá (giả định của nhóm, nên nêu rõ khi thuyết trình) ---
COST_OF_EQUITY = 0.13               # chi phí vốn chủ sở hữu giả định
LONG_TERM_G = 0.05                  # tăng trưởng dài hạn giả định
FAIR_PE_MIN, FAIR_PE_MAX = 8, 20    # P/E hợp lý theo PEG = 1, kẹp trong khoảng này
MAX_UPSIDE, MIN_UPSIDE = 0.50, -0.30  # chặn kết quả quá lệch

# ======================= 2. HÀM TIỆN ÍCH =======================
def _clamp(x):
    return max(0, min(100, round(x)))


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _ok(*vals):
    """True nếu tất cả giá trị đều có (không None)."""
    return all(v is not None for v in vals)


def _f(x, nd=1, suffix=""):
    return "N/A" if x is None else f"{x:,.{nd}f}{suffix}"


def _price_vnd(x):
    return "N/A" if x is None else f"{x * 1000:,.0f} đ"


def _p(x, nd=1):
    return "N/A" if x is None else f"{x * 100:.{nd}f}%"


def _ratio(x, limit):
    """Chuẩn hóa về dạng thập phân. Nếu |x| > limit thì coi là phần trăm (17.56 -> 0.1756).
    Giúp code chạy đúng dù TV2 trả về 0.1756 hay 17.56."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x / 100 if abs(x) > limit else x


def normalize_fund(fund):
    """Trả về bản sao của fund với các tỷ lệ đã đưa về dạng thập phân (idempotent)."""
    fund = dict(fund or {})
    for k in ("ROE", "ROA", "foreign_ownership"):
        if k in fund:
            fund[k] = _ratio(fund[k], 1.5)
    for k in ("EPS_growth", "revenue_growth", "profit_growth"):
        if k in fund:
            fund[k] = _ratio(fund[k], 5)
    return fund


# ======================= 3. CHẤM ĐIỂM TỪNG NHÓM =======================
def score_technical(t):
    """Trả về (score hoặc None, reasons, risks). Bắt đầu 50 điểm, cộng/trừ theo quy tắc."""
    s, used, reasons, risks = 50, 0, [], []
    close, rsi = t.get("close"), t.get("RSI")
    ma20, ma50 = t.get("MA20"), t.get("MA50")
    macd, sig = t.get("MACD"), t.get("MACD_signal")
    vol = t.get("volume_ratio")

    if _ok(rsi):
        used += 1
        if rsi < 30:
            s += 20
            reasons.append(f"RSI {rsi:.1f} < 30: vùng quá bán, có khả năng hồi phục kỹ thuật")
        elif rsi > 70:
            s -= 20
            risks.append(f"RSI {rsi:.1f} > 70: vùng quá mua, rủi ro điều chỉnh")
    if _ok(close, ma50):
        used += 1
        if close > ma50:
            s += 10
            reasons.append(
                f"Giá {_price_vnd(close)} nằm trên MA50 ({_price_vnd(ma50)}): "
                "xu hướng trung hạn tích cực"
            )
        else:
            s -= 10
            risks.append(
                f"Giá {_price_vnd(close)} nằm dưới MA50 ({_price_vnd(ma50)}): "
                "xu hướng trung hạn yếu"
            )
    if _ok(ma20, ma50):
        used += 1
        if ma20 > ma50:
            s += 5
            reasons.append("MA20 nằm trên MA50: động lượng ngắn hạn tốt")
        else:
            s -= 5
    if _ok(macd, sig):
        used += 1
        if macd > sig:
            s += 10
            reasons.append("MACD nằm trên đường tín hiệu: động lượng tăng")
        else:
            s -= 10
            risks.append("MACD nằm dưới đường tín hiệu: động lượng giảm")
    if _ok(vol, close, ma20) and vol > 1.5 and close > ma20:
        s += 5
        reasons.append(f"Khối lượng gấp {vol:.1f} lần trung bình kèm giá tăng")
    return (_clamp(s) if used else None), reasons, risks


def score_fundamental(f):
    s, used, reasons, risks = 50, 0, [], []
    pe, pe_n = f.get("PE"), f.get("PE_nganh")
    pb, pb_n = f.get("PB"), f.get("PB_nganh")
    roe, eps_g = f.get("ROE"), f.get("EPS_growth")
    rev_g, de = f.get("revenue_growth"), f.get("debt_to_equity")

    if _ok(pe, pe_n):
        used += 1
        if pe < pe_n:
            s += 15
            reasons.append(f"P/E {pe:.1f} thấp hơn P/E ngành {pe_n:.1f}: định giá hấp dẫn")
        elif pe > pe_n * 1.2:
            s -= 15
            risks.append(f"P/E {pe:.1f} cao hơn P/E ngành {pe_n:.1f} trên 20%: định giá đắt")
    if _ok(pb, pb_n):
        used += 1
        if pb < pb_n:
            s += 5
            reasons.append(f"P/B {pb:.1f} thấp hơn P/B ngành {pb_n:.1f}")
        elif pb > pb_n * 1.2:
            s -= 5
    if _ok(roe):
        used += 1
        if roe >= 0.15:
            s += 15
            reasons.append(f"ROE {roe:.1%} >= 15%: hiệu quả sử dụng vốn tốt")
        elif roe < 0.08:
            s -= 15
            risks.append(f"ROE {roe:.1%} < 8%: hiệu quả sử dụng vốn thấp")
    if _ok(eps_g):
        used += 1
        if eps_g > 0.10:
            s += 10
            reasons.append(f"EPS tăng trưởng {eps_g:.1%} so với cùng kỳ")
        elif eps_g < 0:
            s -= 15
            risks.append(f"EPS giảm {abs(eps_g):.1%} so với cùng kỳ")
    if _ok(rev_g):
        used += 1
        if rev_g > 0.10:
            s += 5
            reasons.append(f"Doanh thu tăng trưởng {rev_g:.1%}")
        elif rev_g < 0:
            s -= 5
            risks.append(f"Doanh thu giảm {abs(rev_g):.1%}")
    if _ok(de):
        used += 1
        if de > 2:
            s -= 10
            risks.append(f"Nợ/Vốn chủ sở hữu {de:.1f} lần: đòn bẩy tài chính cao")
        elif de < 1:
            s += 5
            reasons.append(f"Nợ/Vốn chủ sở hữu {de:.1f} lần: cấu trúc vốn an toàn")
    return (_clamp(s) if used else None), reasons, risks


def score_news(n):
    """Sentiment đơn giản theo từ khóa trong tiêu đề."""
    heads = (n or {}).get("headlines") or []
    if not heads:
        return None, [], ["Không lấy được tin tức, chưa đánh giá được yếu tố này"]
    pos = neg = 0
    for h in heads:
        title = (h.get("title") or "").lower()
        pos += any(w in title for w in POSITIVE_WORDS)
        neg += any(w in title for w in NEGATIVE_WORDS)
    score = _clamp(50 + 10 * (pos - neg))
    reasons, risks = [], []
    if pos > neg:
        reasons.append(f"Tin tức thiên tích cực ({pos} tích cực / {neg} tiêu cực trong {len(heads)} tin)")
    elif neg > pos:
        risks.append(f"Tin tức thiên tiêu cực ({neg} tiêu cực / {pos} tích cực trong {len(heads)} tin)")
    return score, reasons, risks


def _score_breakdown(tech, fund, news, scores, bonus=0):
    def factor(label, evidence, rule, points=None, applied=False):
        return {
            "label": label,
            "evidence": evidence,
            "rule": rule,
            "points": points,
            "applied": applied,
        }

    technical = [factor("Điểm nền", "50 điểm", "Bắt đầu mỗi nhóm từ 50 điểm.", 0, True)]
    rsi = tech.get("RSI")
    if _ok(rsi):
        points = 20 if rsi < 30 else -20 if rsi > 70 else 0
        rule = (
            "RSI < 30: +20"
            if points > 0
            else "RSI > 70: -20"
            if points < 0
            else "RSI trong vùng 30–70: không điều chỉnh"
        )
        technical.append(factor("RSI", f"{rsi:.2f}", rule, points, True))
    else:
        technical.append(factor("RSI", "Chưa có dữ liệu", "Không được tính."))
    close, ma50 = tech.get("close"), tech.get("MA50")
    if _ok(close, ma50):
        points = 10 if close > ma50 else -10
        technical.append(
            factor(
                "Giá so với MA50",
                f"{_price_vnd(close)} so với {_price_vnd(ma50)}",
                "Giá > MA50: +10; ngược lại: -10.",
                points,
                True,
            )
        )
    else:
        technical.append(factor("Giá so với MA50", "Thiếu giá hoặc MA50", "Không được tính."))
    ma20, ma50 = tech.get("MA20"), tech.get("MA50")
    if _ok(ma20, ma50):
        points = 5 if ma20 > ma50 else -5
        technical.append(
            factor(
                "MA20 so với MA50",
                f"{_price_vnd(ma20)} so với {_price_vnd(ma50)}",
                "MA20 > MA50: +5; ngược lại: -5.",
                points,
                True,
            )
        )
    else:
        technical.append(factor("MA20 so với MA50", "Thiếu MA20 hoặc MA50", "Không được tính."))
    macd, signal = tech.get("MACD"), tech.get("MACD_signal")
    if _ok(macd, signal):
        points = 10 if macd > signal else -10
        technical.append(
            factor(
                "MACD so với Signal",
                f"{macd:.4f} so với {signal:.4f}",
                "MACD > Signal: +10; ngược lại: -10.",
                points,
                True,
            )
        )
    else:
        technical.append(factor("MACD so với Signal", "Thiếu MACD hoặc Signal", "Không được tính."))
    volume = tech.get("volume_ratio")
    if _ok(volume, close, ma20):
        points = 5 if volume > 1.5 and close > ma20 else 0
        technical.append(
            factor(
                "Khối lượng",
                f"{volume:.2f}x trung bình 20 phiên",
                "Chỉ +5 khi khối lượng > 1.5x và giá > MA20.",
                points,
                points > 0,
            )
        )
    else:
        technical.append(factor("Khối lượng", "Thiếu dữ liệu", "Không được tính."))

    fundamental = [factor("Điểm nền", "50 điểm", "Bắt đầu mỗi nhóm từ 50 điểm.", 0, True)]
    pe, pe_industry = fund.get("PE"), fund.get("PE_nganh")
    if _ok(pe, pe_industry):
        points = 15 if pe < pe_industry else -15 if pe > pe_industry * 1.2 else 0
        fundamental.append(
            factor(
                "P/E so với ngành",
                f"{pe:.2f}x so với {pe_industry:.2f}x",
                "Thấp hơn ngành: +15; cao hơn ngành trên 20%: -15.",
                points,
                True,
            )
        )
    elif _ok(pe):
        fundamental.append(
            factor(
                "P/E",
                f"{pe:.2f}x (đã có)",
                "Chưa cộng/trừ điểm vì chưa có P/E ngành để so sánh.",
            )
        )
    else:
        fundamental.append(factor("P/E", "Chưa có dữ liệu", "Không được tính."))
    pb, pb_industry = fund.get("PB"), fund.get("PB_nganh")
    if _ok(pb, pb_industry):
        points = 5 if pb < pb_industry else -5 if pb > pb_industry * 1.2 else 0
        fundamental.append(
            factor(
                "P/B so với ngành",
                f"{pb:.2f}x so với {pb_industry:.2f}x",
                "Thấp hơn ngành: +5; cao hơn ngành trên 20%: -5.",
                points,
                True,
            )
        )
    elif _ok(pb):
        fundamental.append(
            factor("P/B", f"{pb:.2f}x (đã có)", "Chưa cộng/trừ điểm vì thiếu P/B ngành.")
        )
    else:
        fundamental.append(factor("P/B", "Chưa có dữ liệu", "Không được tính."))
    roe = fund.get("ROE")
    if _ok(roe):
        points = 15 if roe >= 0.15 else -15 if roe < 0.08 else 0
        fundamental.append(
            factor(
                "ROE",
                f"{roe:.1%}",
                "ROE >= 15%: +15; ROE < 8%: -15.",
                points,
                True,
            )
        )
    else:
        fundamental.append(factor("ROE", "Chưa có dữ liệu", "Không được tính."))
    eps_growth = fund.get("EPS_growth")
    if _ok(eps_growth):
        points = 10 if eps_growth > 0.10 else -15 if eps_growth < 0 else 0
        fundamental.append(
            factor(
                "Tăng trưởng EPS",
                f"{eps_growth:+.1%}",
                "Tăng > 10%: +10; giảm: -15.",
                points,
                True,
            )
        )
    else:
        fundamental.append(factor("Tăng trưởng EPS", "Chưa có dữ liệu", "Không được tính."))
    revenue_growth = fund.get("revenue_growth")
    if _ok(revenue_growth):
        points = 5 if revenue_growth > 0.10 else -5 if revenue_growth < 0 else 0
        fundamental.append(
            factor(
                "Tăng trưởng doanh thu",
                f"{revenue_growth:+.1%}",
                "Tăng > 10%: +5; giảm: -5.",
                points,
                True,
            )
        )
    else:
        fundamental.append(factor("Tăng trưởng doanh thu", "Chưa có dữ liệu", "Không được tính."))
    debt_to_equity = fund.get("debt_to_equity")
    if _ok(debt_to_equity):
        points = -10 if debt_to_equity > 2 else 5 if debt_to_equity < 1 else 0
        fundamental.append(
            factor(
                "Nợ/Vốn chủ sở hữu",
                f"{debt_to_equity:.2f}x",
                "Trên 2x: -10; dưới 1x: +5.",
                points,
                True,
            )
        )
    else:
        fundamental.append(factor("Nợ/Vốn chủ sở hữu", "Chưa có dữ liệu", "Không được tính."))

    headlines = (news or {}).get("headlines") or []
    positive = negative = 0
    for headline in headlines:
        title = (headline.get("title") or "").lower()
        positive += any(word in title for word in POSITIVE_WORDS)
        negative += any(word in title for word in NEGATIVE_WORDS)
    news_factors = [
        factor("Điểm nền", "50 điểm", "Bắt đầu điểm tin tức từ 50.", 0, True),
        factor(
            "Tin tích cực / tiêu cực",
            f"{positive} / {negative} trong {len(headlines)} tin",
            "Điều chỉnh = 10 x (tích cực - tiêu cực), sau đó cộng điểm nền và chặn 0–100.",
            10 * (positive - negative) if headlines else None,
            bool(headlines),
        )
    ]

    available = [
        {
            "name": name,
            "score": score,
            "weight": WEIGHTS[name],
        }
        for name, score in scores.items()
        if score is not None
    ]
    weight_sum = sum(item["weight"] for item in available)
    for item in available:
        item["weighted_points"] = item["score"] * item["weight"] / weight_sum
    return {
        "groups": available,
        "available_weight": weight_sum,
        "formula": (
            "Điểm tổng = trung bình có trọng số các nhóm đủ dữ liệu; "
            "trọng số được chuẩn hóa lại khi nhóm khác thiếu dữ liệu."
        ),
        "buy_threshold": BUY_THRESHOLD,
        "sell_threshold": SELL_THRESHOLD,
        "bonus": bonus,
        "technical_factors": technical,
        "fundamental_factors": fundamental,
        "news_factors": news_factors,
        "missing_groups": [
            name for name, score in scores.items() if score is None
        ],
    }


def compute_target_price(tech, fund):
    """Tính giá mục tiêu theo các phương pháp có dữ liệu (đơn vị nghìn đồng)."""
    close = tech.get("close")
    if not _ok(close) or close <= 0:
        return {"target_price": None, "upside": None,
                "method": "Không có giá hiện tại", "confidence": None,
                "valuation_methods": [], "valuation_summary": ""}

    valuation_rows, estimates = [], []
    if fund.get("valuation_type") == "bank":
        historical_values = [
            float(value) for value in fund.get("historical_PB", [])
            if _ok(value) and float(value) > 0
        ]
        bvps = fund.get("BVPS")
        if _ok(bvps) and bvps > 0 and len(historical_values) >= 2:
            relative_pb = sorted(historical_values)[len(historical_values) // 2]
            value = bvps * relative_pb / 1000
            estimates.append((value, 50))
            valuation_rows.append({
                "method_name": "P/B so với trung vị lịch sử",
                "value": value,
                "weight": 50,
                "note": f"BVPS {bvps:,.0f} đồng; trung vị P/B của {len(historical_values)} kỳ.",
            })
        roe = _ratio(fund.get("ROE"), 1.5)
        if _ok(bvps, roe) and bvps > 0 and roe > LONG_TERM_G:
            fair_pb = (roe - LONG_TERM_G) / (COST_OF_EQUITY - LONG_TERM_G)
            if fair_pb > 0:
                value = bvps * fair_pb / 1000
                estimates.append((value, 50))
                valuation_rows.append({
                    "method_name": "Thu nhập thặng dư (Residual Income)",
                    "value": value,
                    "weight": 50,
                    "note": (
                        f"ROE {roe:.1%}; giả định chi phí vốn {COST_OF_EQUITY:.0%}, "
                        f"tăng trưởng dài hạn {LONG_TERM_G:.0%}."
                    ),
                })
        valuation_summary = (
            f"Residual Income phiên bản rút gọn giả định ROE ổn định, chi phí vốn chủ sở hữu "
            f"{COST_OF_EQUITY:.0%} và tăng trưởng dài hạn {LONG_TERM_G:.0%}; "
            "P/B tham chiếu trung vị lịch sử. Đây là ước tính nhạy với giả định, không phải dự báo."
        )
        valuation_label = "P/B lịch sử + Residual Income"
    elif fund.get("valuation_type") == "real_estate":
        historical_values = [
            float(value) for value in fund.get("historical_PB", [])
            if _ok(value) and float(value) > 0
        ]
        bvps = fund.get("BVPS")
        if _ok(bvps) and bvps > 0 and len(historical_values) >= 2:
            relative_pb = sorted(historical_values)[len(historical_values) // 2]
            value = bvps * relative_pb / 1000
            estimates.append((value, 100))
            valuation_rows.append({
                "method_name": "P/B so với trung vị lịch sử",
                "value": value,
                "weight": 100,
                "note": f"BVPS {bvps:,.0f} đồng; trung vị P/B của {len(historical_values)} kỳ.",
            })
        valuation_rows.append({
            "method_name": "RNAV",
            "value": None,
            "weight": 0,
            "note": "Không tính: thiếu danh mục dự án, pháp lý và định giá quỹ đất.",
        })
        valuation_summary = (
            "RNAV chưa thể tính do thiếu dữ liệu dự án và giá trị quỹ đất; "
            "P/B lịch sử chỉ dùng khi có BVPS và đủ dữ liệu P/B."
        )
        valuation_label = "P/B lịch sử; RNAV chưa đủ dữ liệu"
    else:
        historical_values = [
            float(value) for value in fund.get("historical_PE", [])
            if _ok(value) and float(value) > 0
        ]
        eps = fund.get("EPS")
        if _ok(eps) and eps > 0 and len(historical_values) >= 2:
            relative_pe = sorted(historical_values)[len(historical_values) // 2]
            value = eps * relative_pe / 1000
            estimates.append((value, 100))
            valuation_rows.append({
                "method_name": "P/E so với trung vị lịch sử",
                "value": value,
                "weight": 100,
                "note": f"EPS {eps:,.0f} đồng; trung vị P/E của {len(historical_values)} kỳ.",
            })
        valuation_rows.append({
            "method_name": "DCF / FCFF",
            "value": None,
            "weight": 0,
            "note": "Không tính: thiếu dự phóng dòng tiền, WACC và tăng trưởng cuối kỳ.",
        })
        valuation_summary = (
            "DCF/FCFF được để trống vì chưa có dự phóng dòng tiền và giả định chiết khấu; "
            "không tự tạo các giả định này."
        )
        valuation_label = "P/E lịch sử; DCF chưa đủ giả định"

    if estimates:
        weight_total = sum(weight for _, weight in estimates)
        raw = sum(value * weight for value, weight in estimates) / weight_total
        for row in valuation_rows:
            if row["value"] is not None:
                row["weight"] = round(row["weight"] / weight_total * 100)
        upside = raw / close - 1
        capped = upside > MAX_UPSIDE or upside < MIN_UPSIDE
        raw = close * (1 + _clip(upside, MIN_UPSIDE, MAX_UPSIDE))
        target = round(raw, 1)
        used_names = [
            row["method_name"] for row in valuation_rows if row["value"] is not None
        ]
        return {
            "target_price": target,
            "upside": round(target / close - 1, 4),
            "method": (
                f"{valuation_label} ({' + '.join(used_names)})"
                + (
                    f"; giá mục tiêu cuối cùng được giới hạn trong "
                    f"{MIN_UPSIDE:.0%} đến {MAX_UPSIDE:+.0%} so với giá hiện tại"
                    if capped
                    else ""
                )
            ),
            "confidence": "Trung bình" if len(estimates) > 1 else "Thấp",
            "valuation_methods": valuation_rows,
            "valuation_summary": valuation_summary,
        }

    pe, pe_n = fund.get("PE"), fund.get("PE_nganh")
    pb, pb_n = fund.get("PB"), fund.get("PB_nganh")
    roe = fund.get("ROE")
    growth = fund.get("EPS_growth")
    if growth is None:
        growth = fund.get("profit_growth")

    if _ok(pe, pe_n) and pe > 0 and pe_n > 0:
        raw = close * pe_n / pe
        method, confidence = f"P/E ngành ({pe_n:.1f} lần)", "Cao"
    elif _ok(pb, pb_n) and pb > 0 and pb_n > 0:
        raw = close * pb_n / pb
        method, confidence = f"P/B ngành ({pb_n:.1f} lần)", "Cao"
    else:
        estimates, notes = [], []
        if _ok(roe, pb) and pb > 0:
            fair_pb = _clip((roe - LONG_TERM_G) / (COST_OF_EQUITY - LONG_TERM_G), 0.5, 5)
            estimates.append(close * fair_pb / pb)
            notes.append(f"P/B hợp lý {fair_pb:.2f} lần theo ROE {roe:.1%}")
        if _ok(growth, pe) and growth > 0 and pe > 0:
            fair_pe = _clip(growth * 100, FAIR_PE_MIN, FAIR_PE_MAX)
            estimates.append(close * fair_pe / pe)
            notes.append(f"P/E hợp lý {fair_pe:.0f} lần theo tăng trưởng EPS {growth:.1%}")
        elif _ok(growth, pe) and growth <= 0:
            notes.append(
                f"Không dùng P/E theo tăng trưởng EPS không dương ({growth:.1%})"
            )
        if estimates:
            raw = sum(estimates) / len(estimates)
            method = "Định giá nội tại (thiếu P/E/P/B ngành): " + "; ".join(notes)
            confidence = "Trung bình"
        else:
            refs = [tech.get("MA50")]
            lo, hi = tech.get("low_52w"), tech.get("high_52w")
            if _ok(lo, hi):
                refs.append((lo + hi) / 2)
            refs = [x for x in refs if x]
            if refs:
                raw = sum(refs) / len(refs)
                method, confidence = "Kỹ thuật (hồi về MA50 / giữa dải 52 tuần)", "Thấp"
            else:
                raw = close
                method, confidence = "Thiếu dữ liệu, giữ nguyên giá hiện tại", "Rất thấp"

    # Chặn kết quả quá lệch (vd P/E mã quá thấp so với ngành)
    up = raw / close - 1
    if up > MAX_UPSIDE or up < MIN_UPSIDE:
        raw = close * (1 + _clip(up, MIN_UPSIDE, MAX_UPSIDE))
        method += f" (đã giới hạn tiềm năng trong khoảng {MIN_UPSIDE:.0%} đến {MAX_UPSIDE:+.0%})"

    target = round(raw, 1)
    return {"target_price": target, "upside": round(target / close - 1, 4),
            "method": method, "confidence": confidence,
            "valuation_methods": valuation_rows,
            "valuation_summary": valuation_summary if valuation_rows else ""}


# ======================= 4. TỔNG HỢP =======================
def get_recommendation(tech, fund, news, use_ai=True):
    fund = normalize_fund(fund)
    sc_t, r_t, k_t = score_technical(tech)
    sc_f, r_f, k_f = score_fundamental(fund)
    sc_n, r_n, k_n = score_news(news)
    scores = {"technical": sc_t, "fundamental": sc_f, "news": sc_n}

    # Trung bình có trọng số, bỏ qua nhóm thiếu dữ liệu (chia lại trọng số)
    avail = {k: v for k, v in scores.items() if v is not None}
    if not avail:
        raise ValueError("Không có dữ liệu nào để chấm điểm")
    wsum = sum(WEIGHTS[k] for k in avail)
    total = sum(WEIGHTS[k] * v for k, v in avail.items()) / wsum
    reasons, risks = r_f + r_t + r_n, k_f + k_t + k_n

    # Quy tắc kết hợp (ví dụ của nhóm trưởng): RSI quá bán + P/E thấp hơn ngành -> cộng thêm
    rsi, pe, pe_n = tech.get("RSI"), fund.get("PE"), fund.get("PE_nganh")
    if _ok(rsi, pe, pe_n) and rsi < 30 and pe < pe_n:
        total += 5
        reasons.insert(0, "Tín hiệu kết hợp: RSI quá bán và P/E thấp hơn ngành (+5 điểm)")

    total = _clamp(total)
    rec = "MUA" if total >= BUY_THRESHOLD else ("BÁN" if total < SELL_THRESHOLD else "GIỮ")
    tp = compute_target_price(tech, fund)

    result = {
        "symbol": tech.get("symbol") or fund.get("symbol"),
        "recommendation": rec,
        "total_score": total,
        "scores": scores,
        "target_price": tp["target_price"],   # nghìn đồng, None chỉ khi thiếu giá hiện tại
        "upside": tp["upside"],               # 0.26 = +26%
        "target_method": tp["method"],
        "target_confidence": tp["confidence"],
        "valuation_methods": tp.get("valuation_methods", []),
        "valuation_summary": tp.get("valuation_summary", ""),
        "reasons": reasons,
        "risks": risks or ["Chưa phát hiện rủi ro nổi bật theo bộ quy tắc hiện tại"],
        "ai_summary": "",
        "ai_generated": False,
        "ai_provider": None,
        "ai_unverified_numbers": [],
    }
    result["score_breakdown"] = _score_breakdown(
        tech, fund, news, scores, bonus=5 if _ok(rsi, pe, pe_n) and rsi < 30 and pe < pe_n else 0
    )
    result["score_breakdown"]["total_score"] = total
    result["score_breakdown"]["recommendation"] = rec

    # Sinh nhận định: thử AI, lỗi thì dùng văn bản dự phòng
    facts = build_facts(tech, fund, result)
    if use_ai:
        try:
            text, provider = call_ai(build_prompt(facts))
            result["ai_summary"] = text
            result["ai_generated"] = True
            result["ai_provider"] = provider
            result["ai_unverified_numbers"] = check_numbers(text, facts)
        except Exception as e:  # không có key, bị từ chối, hết quota, mất mạng...
            print(f"[WARN] AI lỗi, dùng văn bản dự phòng: {e}", file=sys.stderr)
    if not result["ai_summary"]:
        result["ai_summary"] = fallback_summary(result)
    return result


# ======================= 5. AI: PROMPT + GỌI API + DỰ PHÒNG =======================
def build_facts(tech, fund, result):
    """Gom số liệu thật thành text để đưa vào prompt (AI chỉ được dùng các số này)."""
    lines = [
        f"Mã cổ phiếu: {result['symbol']}",
        f"Khuyến nghị của hệ thống: {result['recommendation']} (điểm tổng hợp {result['total_score']}/100)",
        f"Điểm thành phần: {result['scores']}",
        f"Giải thích cách tính điểm: {json.dumps(result.get('score_breakdown', {}), ensure_ascii=False)}",
        f"Giá mục tiêu (nghìn đồng): {result['target_price']}, tiềm năng: {result['upside']}",
        f"Phương pháp định giá: {result['target_method']} (độ tin cậy: {result['target_confidence']})",
        f"Các phương pháp và tỷ trọng: {json.dumps(result.get('valuation_methods', []), ensure_ascii=False)}",
        f"Giới hạn định giá: {result.get('valuation_summary', '')}",
        f"Chỉ số kỹ thuật: {json.dumps(tech, ensure_ascii=False)}",
        f"Chỉ số cơ bản: {json.dumps(fund, ensure_ascii=False)}",
        "Luận điểm ủng hộ: " + "; ".join(result["reasons"]),
        "Rủi ro: " + "; ".join(result["risks"]),
    ]
    return "\n".join(lines)


def build_prompt(facts):
    return f"""Bạn là chuyên viên phân tích của một công ty chứng khoán tại Việt Nam.
Hãy viết phần "Nhận định tổng quan" cho báo cáo phân tích cổ phiếu, dựa TRÊN DỮ LIỆU SAU:

{facts}

YÊU CẦU:
- Viết tiếng Việt, văn phong chuyên nghiệp, trung lập; phân tích rõ cơ bản, kỹ thuật và định giá.
- Viết 4 đoạn ngắn: (1) kết luận, khuyến nghị và giải thích điểm tổng hợp; (2) xu hướng tài chính/các chỉ số cơ bản nổi bật; (3) tín hiệu kỹ thuật và các luận cứ cụ thể dẫn tới khuyến nghị; (4) định giá, giả định/giới hạn của mô hình và rủi ro.
- Chỉ giải thích số điểm bằng các điểm thành phần, trọng số, yếu tố cộng/trừ và ngưỡng khuyến nghị có trong dữ liệu; không suy diễn nguyên nhân thiếu bằng chứng.
- Nếu có chuỗi doanh thu/thu nhập và LNST, nêu xu hướng YoY theo năm; phân biệt doanh nghiệp ngân hàng (NII) với doanh thu thuần của doanh nghiệp khác.
- Đánh giá độc lập rủi ro đòn bẩy, thanh khoản, chất lượng tài sản hoặc tín hiệu kỹ thuật chỉ khi các chỉ tiêu liên quan thực sự có dữ liệu.
- Phân biệt dữ liệu thiếu với chỉ số bằng 0. Nếu thiếu dữ liệu của một phương pháp định giá, nói rõ lý do và không tự thay thế bằng dự phóng.
- Nêu phương pháp, giá trị các phương pháp và trọng số nếu có trong dữ liệu; phân biệt giá mục tiêu cuối cùng với các giá trị hợp lý chưa bị giới hạn.
- CHỈ sử dụng các con số có trong dữ liệu ở trên. Tuyệt đối không bịa thêm số liệu, giá mục tiêu hay dự báo.
- Nêu ngắn gọn phương pháp tính giá mục tiêu và độ tin cậy như trong dữ liệu; nếu độ tin cậy là Thấp hoặc Rất thấp thì nói rõ đây chỉ là ước tính tham khảo.
- Không hứa hẹn lợi nhuận, không dùng từ "chắc chắn", "đảm bảo".
- Khuyến nghị phải đúng với kết quả của hệ thống, không tự đổi.
- Không dùng markdown, không dùng tiêu đề; chỉ văn bản thường."""


def _gemini(prompt):
    from google import genai  # py -m pip install google-genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    r = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), contents=prompt)
    return r.text.strip()


def _anthropic(prompt):
    import anthropic  # py -m pip install anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    m = client.messages.create(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=800,
                               messages=[{"role": "user", "content": prompt}])
    return m.content[0].text.strip()


def _openai(prompt):
    from openai import OpenAI  # py -m pip install openai
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    r = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content.strip()


# Thử lần lượt các nhà cung cấp có đặt key. Nhà nào lỗi thì thử nhà tiếp theo.
PROVIDERS = [("gemini", "GEMINI_API_KEY", _gemini),
             ("anthropic", "ANTHROPIC_API_KEY", _anthropic),
             ("openai", "OPENAI_API_KEY", _openai)]


def call_ai(prompt):
    """Trả về (văn bản, tên nhà cung cấp). Ném lỗi nếu không nhà nào dùng được."""
    errors = []
    for name, env, fn in PROVIDERS:
        if not os.getenv(env):
            continue
        try:
            return fn(prompt), name
        except Exception as e:
            errors.append(f"{name}: {str(e)[:150]}")
    if not errors:
        raise RuntimeError("Chưa đặt key nào (GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)")
    raise RuntimeError(" | ".join(errors))


def _isnum(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


def check_numbers(text, facts):
    """Trả về các số trong văn bản AI mà KHÔNG xuất hiện trong dữ liệu đầu vào (nghi bịa số)."""
    def nums(s):
        return {x.replace(",", ".").rstrip(".") for x in re.findall(r"\d+(?:[.,]\d+)?", s)}
    allowed = nums(facts)
    allowed |= {f"{float(x) * 100:g}" for x in allowed if _isnum(x)}  # 0.21 -> 21
    return sorted(n for n in nums(text) if n not in allowed)


def fallback_summary(r):
    """Tạo nhận định tiếng Việt từ kết quả rule engine, không phụ thuộc API."""
    score_labels = {
        "technical": "kỹ thuật",
        "fundamental": "cơ bản",
        "news": "tin tức",
    }
    available = [
        f"{score_labels[key]} {score}/100"
        for key, score in r["scores"].items()
        if score is not None
    ]
    missing = [
        score_labels[key]
        for key, score in r["scores"].items()
        if score is None
    ]
    p1 = (
        f"Theo bộ tiêu chí định lượng, cổ phiếu {r['symbol']} đạt "
        f"{r['total_score']}/100 điểm, tương ứng khuyến nghị "
        f"{r['recommendation']}. Điểm thành phần: {', '.join(available)}."
    )
    if r.get("target_price") is not None:
        p1 += (
            f" Giá mục tiêu {r['target_price']:,.1f} nghìn đồng "
            f"(tiềm năng {r['upside'] * 100:+.1f}%), tính theo "
            f"{r.get('target_method', 'phương pháp định giá của hệ thống')}."
        )
    p2 = ("Các yếu tố ủng hộ: " + "; ".join(r["reasons"]) + ".") if r["reasons"] else \
        "Hiện chưa có yếu tố ủng hộ nổi bật."
    p3 = "Rủi ro cần theo dõi: " + "; ".join(r["risks"]) + "."
    if missing:
        p3 += f" Chưa đủ dữ liệu để chấm nhóm: {', '.join(missing)}."
    return "\n\n".join([p1, p2, p3])


# ======================= 6. DỮ LIỆU ĐIỀN VÀO TEMPLATE PDF =======================
def to_report_context(rec, tech, fund, company_name="", exchange="", industry=""):
    """Đổi kết quả sang đúng tên biến trong core/report_template.html (TV4, TV5 dùng).
    Truyền dict này vào template.render(**context)."""
    fund = normalize_fund(fund)
    sc = rec["scores"]
    score_labels = {
        "fundamental": "cơ bản",
        "technical": "kỹ thuật",
        "news": "tin tức",
    }
    score_details = ", ".join(
        f"{score_labels[key]} {score}/100" if score is not None
        else f"{score_labels[key]} chưa đủ dữ liệu"
        for key, score in sc.items()
    )
    target_note = (
        f"Giá mục tiêu tính theo: {rec.get('target_method', 'N/A')} "
        f"(độ tin cậy {rec.get('target_confidence') or 'N/A'})."
        if rec["target_price"] is not None
        else "Chưa đủ dữ liệu để tính giá mục tiêu."
    )
    thesis = (
        f"Hệ thống chấm {rec['total_score']}/100 điểm ({score_details}), "
        f"tương ứng khuyến nghị {rec['recommendation']}. {target_note}"
    )
    price_range = "N/A"
    if _ok(tech.get("low_52w"), tech.get("high_52w")):
        price_range = (
            f"{_price_vnd(tech['low_52w'])} - {_price_vnd(tech['high_52w'])}"
        )
    sources = ", ".join(s for s in {tech.get("source"), fund.get("source")} if s) or "N/A"

    def row(label, value):
        return {"label": label, "values": [value]}

    sections = [
        {"title": "Định giá", "rows": [
            row("P/E (lần)", _f(fund.get("PE"))), row("P/E ngành (lần)", _f(fund.get("PE_nganh"))),
            row("P/B (lần)", _f(fund.get("PB"))), row("P/B ngành (lần)", _f(fund.get("PB_nganh")))]},
        {"title": "Hiệu quả hoạt động", "rows": [
            row("ROE", _p(fund.get("ROE"))), row("ROA", _p(fund.get("ROA"))),
            row("EPS (đồng)", _f(fund.get("EPS"), 0))]},
        {"title": "Tăng trưởng so với cùng kỳ", "rows": [
            row("Doanh thu", _p(fund.get("revenue_growth"))),
            row("Lợi nhuận", _p(fund.get("profit_growth"))),
            row("EPS", _p(fund.get("EPS_growth")))]},
        {"title": "Cấu trúc vốn", "rows": [
            row("Nợ/Vốn chủ sở hữu (lần)", _f(fund.get("debt_to_equity")))]},
    ]
    return {
        "ticker": rec["symbol"],
        "company_name": company_name or rec["symbol"],
        "report_date": datetime.now().strftime("%d/%m/%Y"),
        "recommendation": rec["recommendation"],
        "target_price": _price_vnd(rec["target_price"]),
        "upside": "N/A" if rec["upside"] is None else f"{rec['upside'] * 100:+.1f}%",
        "target_method": rec.get("target_method", ""),
        "target_confidence": rec.get("target_confidence") or "",
        "valuation_methods": [
            {
                "method_name": item["method_name"],
                "fair_value": (
                    f"{item['value'] * 1000:,.0f}"
                    if item.get("value") is not None
                    else item.get("note", "Chưa đủ dữ liệu")
                ),
                "weight": f"{item.get('weight', 0)}%",
            }
            for item in rec.get("valuation_methods", [])
        ],
        "valuation_summary": rec.get("valuation_summary", ""),
        "exchange": exchange or "N/A",
        "industry": industry or "N/A",
        "current_price": _price_vnd(tech.get("close")),
        "investment_horizon": "12 tháng",
        "price_chart": tech.get("chart_path") or "",
        "price_source": tech.get("source") or "",
        "market_cap": _f(fund.get("market_cap"), 0),
        "shares_outstanding": _f(fund.get("shares_outstanding"), 0),
        "avg_volume_20d": _f(tech.get("avg_volume_20d"), 0),
        "price_52w_range": price_range,
        "foreign_ownership": _p(fund.get("foreign_ownership")),
        "foreign_ownership_limit": _p(fund.get("foreign_ownership_limit")),
        "beta": _f(fund.get("beta"), 2),
        "financial_years": [fund.get("as_of") or "Gần nhất"],
        "financial_sections": sections,
        "investment_thesis": thesis,
        "ai_summary": rec["ai_summary"],
        "ai_generated": rec["ai_generated"],
        "investment_points": rec["reasons"],
        "key_risks": rec["risks"],
        "data_sources": sources,
        "data_as_of": tech.get("as_of") or fund.get("as_of") or "N/A",
        "analyst_name": "Nhóm phân tích",  # sửa thành tên nhóm / tên thành viên nếu muốn
        "analyst_contact": "",
        "disclaimer": "",  # để trống: template dùng đoạn miễn trừ trách nhiệm mặc định
    }


# ======================= 7. CHẠY THỬ =======================
if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/sample_FPT.json"
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    out = get_recommendation(d["tech"], d["fund"], d["news"], use_ai=True)
    os.makedirs("data", exist_ok=True)
    out_path = f"data/sample_output_{out['symbol']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    ctx_path = f"data/sample_context_{out['symbol']}.json"
    with open(ctx_path, "w", encoding="utf-8") as f:
        json.dump(to_report_context(out, d["tech"], d["fund"]), f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nĐã lưu: {out_path} và {ctx_path}")