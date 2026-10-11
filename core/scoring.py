import json
import os
import re
import statistics
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
        load_dotenv(override=False)


_load_ai_env()

# ======================= 1. CẤU HÌNH (chỉnh ở đây) =======================
# Trọng số là GIẢ ĐỊNH của nhóm (chưa kiểm chứng). Tin tức chỉ chấm bằng từ khóa nên để thấp.
WEIGHTS = {"fundamental": 0.45, "technical": 0.40, "news": 0.15}
BUY_THRESHOLD = 65      # điểm tổng tối thiểu để được xét MUA
SELL_THRESHOLD = 45     # điểm tổng dưới mức này mới được xét BÁN

# Khuyến nghị gắn với tiềm năng giá mục tiêu (ngưỡng theo hệ thống khuyến nghị của VCBS)
BUY_MIN_UPSIDE = 0.15   # MUA cần tiềm năng >= +15%
SELL_MAX_UPSIDE = -0.15  # BÁN cần tiềm năng <= -15%

# Tin tức: mỗi tin tích cực +5, tiêu cực -5, tổng điều chỉnh bị chặn trong +-20 (điểm tin tức 30-70)
NEWS_POINT_PER_ITEM = 5
NEWS_MAX_ADJUST = 20

# Định giá P/E lịch sử
MIN_PE_HISTORY = 3        # cần ít nhất 3 kỳ P/E hợp lệ để lấy trung vị
MAX_VALID_PE = 100        # P/E lớn hơn mức này coi là bất thường, bỏ khỏi trung vị
LARGE_UPSIDE_WARNING = 0.50  # |tiềm năng| lớn hơn mức này thì hạ độ tin cậy và cảnh báo

# Cụm từ dài, so khớp theo ranh giới từ (tránh lỗi "điều kiện" bị tính là "kiện")
POSITIVE_WORDS = ["tăng trưởng", "lợi nhuận tăng", "lãi ròng", "vượt kế hoạch", "kỷ lục",
                  "chia cổ tức", "trả cổ tức", "nâng dự báo", "khởi sắc", "trúng thầu",
                  "ký hợp đồng", "mở rộng", "mua ròng"]
NEGATIVE_WORDS = ["lỗ ròng", "báo lỗ", "sụt giảm", "lợi nhuận giảm", "giảm mạnh", "bán ròng",
                  "vi phạm", "bị phạt", "thanh tra", "đình chỉ", "nợ xấu", "hạ dự báo",
                  "khó khăn", "bị kiện", "khởi tố", "cảnh báo", "chấm dứt hợp đồng"]

VALUATION_SCOPE_NOTE = (
    "Giá mục tiêu = EPS gần nhất × P/E trung vị lịch sử của chính doanh nghiệp, không dùng EPS "
    "dự phóng. DCF, RNAV và SOTP không được sử dụng vì chưa có dự phóng dòng tiền, danh mục dự án "
    "và giả định chiết khấu đáng tin cậy."
)


# ======================= 2. HÀM TIỆN ÍCH =======================
def _clamp(x):
    return max(0, min(100, round(x)))


def _ok(*vals):
    """True nếu tất cả giá trị đều có (không None)."""
    return all(v is not None for v in vals)


def _f(x, nd=1, suffix=""):
    return "N/A" if x is None else f"{x:,.{nd}f}{suffix}"


def _price_vnd(x):
    """Giá tính bằng nghìn đồng -> '20,300 đ'."""
    return "N/A" if x is None else f"{x * 1000:,.0f} đ"


def _p(x, nd=1):
    return "N/A" if x is None else f"{x * 100:.{nd}f}%"


def _has_phrase(text, phrase):
    """So khớp cụm từ theo ranh giới từ."""
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


_WARNED = set()


def normalize_fund(fund):
    """Trả về bản sao của fund. KHÔNG đoán đơn vị: dữ liệu phải ở dạng thập phân
    (ROE 21% = 0.21). Nếu giá trị có vẻ sai đơn vị thì chỉ cảnh báo để người phụ trách sửa tại nguồn."""
    fund = dict(fund or {})
    limits = (("ROE", 1.5), ("ROA", 1.5), ("foreign_ownership", 1.5),
              ("EPS_growth", 10), ("revenue_growth", 10), ("profit_growth", 10))
    for key, limit in limits:
        value = fund.get(key)
        if value is None:
            continue
        try:
            too_big = abs(float(value)) > limit
        except (TypeError, ValueError):
            fund[key] = None
            continue
        if too_big and key not in _WARNED:
            _WARNED.add(key)
            print(f"[WARN] fund['{key}']={value} lớn bất thường, kiểm tra xem có phải dạng % "
                  f"thay vì thập phân (0.21 = 21%).", file=sys.stderr)
    return fund


def _factor(label, evidence, rule, points=None, text=None, core=False):
    """Một yếu tố chấm điểm. Điểm số và bảng giải thích đều sinh ra từ danh sách này
    nên không thể lệch nhau."""
    return {"label": label, "evidence": evidence, "rule": rule, "points": points,
            "applied": points is not None, "text": text, "core": core}


def _group_score(factors):
    """50 điểm nền + tổng điểm các yếu tố. None nếu không có yếu tố chính nào có dữ liệu."""
    if not any(f["core"] and f["points"] is not None for f in factors):
        return None
    return _clamp(50 + sum(f["points"] for f in factors if f["points"] is not None))


def _texts(factors):
    reasons, risks = [], []
    for f in factors:
        if not f.get("text") or f["points"] is None:
            continue
        if f["points"] > 0:
            reasons.append(f["text"])
        elif f["points"] < 0:
            risks.append(f["text"])
    return reasons, risks


BASE_FACTOR = ("Điểm nền", "50 điểm", "Bắt đầu mỗi nhóm từ 50 điểm.")


# ======================= 3. CHẤM ĐIỂM TỪNG NHÓM =======================
def _technical_factors(t):
    close, rsi = t.get("close"), t.get("RSI")
    ma20, ma50 = t.get("MA20"), t.get("MA50")
    macd, sig = t.get("MACD"), t.get("MACD_signal")
    vol = t.get("volume_ratio")
    fs = [_factor(*BASE_FACTOR, points=0)]

    if _ok(rsi):
        text = None
        if rsi < 30:
            pts = 20
            text = f"RSI {rsi:.1f} < 30: vùng quá bán, có khả năng hồi phục kỹ thuật"
            if _ok(close, ma50) and close < ma50:
                text += " (giá vẫn dưới MA50 nên chưa có xác nhận đảo chiều)"
        elif rsi > 70:
            pts = -20
            text = f"RSI {rsi:.1f} > 70: vùng quá mua, rủi ro điều chỉnh"
        else:
            pts = 0
        fs.append(_factor("RSI", f"{rsi:.2f}", "RSI < 30: +20; RSI > 70: -20; vùng 30–70: 0.",
                          pts, text, core=True))
    else:
        fs.append(_factor("RSI", "Chưa có dữ liệu", "Không được tính."))

    if _ok(close, ma50):
        if close > ma50:
            pts, text = 10, (f"Giá {_price_vnd(close)} nằm trên MA50 ({_price_vnd(ma50)}): "
                             "xu hướng trung hạn tích cực")
        else:
            pts, text = -10, (f"Giá {_price_vnd(close)} nằm dưới MA50 ({_price_vnd(ma50)}): "
                              "xu hướng trung hạn yếu")
        fs.append(_factor("Giá so với MA50", f"{_price_vnd(close)} so với {_price_vnd(ma50)}",
                          "Giá > MA50: +10; ngược lại: -10.", pts, text, core=True))
    else:
        fs.append(_factor("Giá so với MA50", "Thiếu giá hoặc MA50", "Không được tính."))

    if _ok(ma20, ma50):
        pts = 5 if ma20 > ma50 else -5
        text = "MA20 nằm trên MA50: động lượng ngắn hạn tốt" if pts > 0 else None
        fs.append(_factor("MA20 so với MA50", f"{_price_vnd(ma20)} so với {_price_vnd(ma50)}",
                          "MA20 > MA50: +5; ngược lại: -5.", pts, text, core=True))
    else:
        fs.append(_factor("MA20 so với MA50", "Thiếu MA20 hoặc MA50", "Không được tính."))

    if _ok(macd, sig):
        if macd > sig:
            pts, text = 10, "MACD nằm trên đường tín hiệu: động lượng tăng"
        else:
            pts, text = -10, "MACD nằm dưới đường tín hiệu: động lượng giảm"
        fs.append(_factor("MACD so với Signal", f"{macd:.4f} so với {sig:.4f}",
                          "MACD > Signal: +10; ngược lại: -10.", pts, text, core=True))
    else:
        fs.append(_factor("MACD so với Signal", "Thiếu MACD hoặc Signal", "Không được tính."))

    if _ok(vol, close, ma20):
        pts = 5 if vol > 1.5 and close > ma20 else 0
        text = f"Khối lượng gấp {vol:.1f} lần trung bình kèm giá tăng" if pts > 0 else None
        fs.append(_factor("Khối lượng", f"{vol:.2f}x trung bình 20 phiên",
                          "Chỉ +5 khi khối lượng > 1.5x và giá > MA20.", pts, text))
    else:
        fs.append(_factor("Khối lượng", "Thiếu dữ liệu", "Không được tính."))
    return fs


def _fundamental_factors(f):
    fs = [_factor(*BASE_FACTOR, points=0)]
    pe, pe_n = f.get("PE"), f.get("PE_nganh")
    pb, pb_n = f.get("PB"), f.get("PB_nganh")
    roe, eps_g = f.get("ROE"), f.get("EPS_growth")
    rev_g, de = f.get("revenue_growth"), f.get("debt_to_equity")

    if _ok(pe, pe_n):
        if pe < pe_n:
            pts, text = 15, f"P/E {pe:.1f} thấp hơn P/E ngành {pe_n:.1f}: định giá hấp dẫn"
        elif pe > pe_n * 1.2:
            pts, text = -15, f"P/E {pe:.1f} cao hơn P/E ngành {pe_n:.1f} trên 20%: định giá đắt"
        else:
            pts, text = 0, None
        fs.append(_factor("P/E so với ngành", f"{pe:.2f}x so với {pe_n:.2f}x",
                          "Thấp hơn ngành: +15; cao hơn ngành trên 20%: -15.", pts, text, core=True))
    elif _ok(pe):
        fs.append(_factor("P/E", f"{pe:.2f}x (đã có)",
                          "Chưa cộng/trừ điểm vì chưa có P/E ngành để so sánh."))
    else:
        fs.append(_factor("P/E", "Chưa có dữ liệu", "Không được tính."))

    if _ok(pb, pb_n):
        if pb < pb_n:
            pts, text = 5, f"P/B {pb:.1f} thấp hơn P/B ngành {pb_n:.1f}"
        elif pb > pb_n * 1.2:
            pts, text = -5, None
        else:
            pts, text = 0, None
        fs.append(_factor("P/B so với ngành", f"{pb:.2f}x so với {pb_n:.2f}x",
                          "Thấp hơn ngành: +5; cao hơn ngành trên 20%: -5.", pts, text, core=True))
    elif _ok(pb):
        fs.append(_factor("P/B", f"{pb:.2f}x (đã có)",
                          "Chưa cộng/trừ điểm vì thiếu P/B ngành."))
    else:
        fs.append(_factor("P/B", "Chưa có dữ liệu", "Không được tính."))

    if _ok(roe):
        if roe >= 0.15:
            pts, text = 15, f"ROE {roe:.1%} >= 15%: hiệu quả sử dụng vốn tốt"
        elif roe < 0.08:
            pts, text = -15, f"ROE {roe:.1%} < 8%: hiệu quả sử dụng vốn thấp"
        else:
            pts, text = 0, None
        fs.append(_factor("ROE", f"{roe:.1%}", "ROE >= 15%: +15; ROE < 8%: -15.",
                          pts, text, core=True))
    else:
        fs.append(_factor("ROE", "Chưa có dữ liệu", "Không được tính."))

    if _ok(eps_g):
        if eps_g > 0.10:
            pts, text = 10, f"EPS tăng trưởng {eps_g:.1%} so với cùng kỳ"
        elif eps_g < 0:
            pts, text = -15, f"EPS giảm {abs(eps_g):.1%} so với cùng kỳ"
        else:
            pts, text = 0, None
        fs.append(_factor("Tăng trưởng EPS", f"{eps_g:+.1%}", "Tăng > 10%: +10; giảm: -15.",
                          pts, text, core=True))
    else:
        fs.append(_factor("Tăng trưởng EPS", "Chưa có dữ liệu", "Không được tính."))

    if _ok(rev_g):
        if rev_g > 0.10:
            pts, text = 5, f"Doanh thu tăng trưởng {rev_g:.1%}"
        elif rev_g < 0:
            pts, text = -5, f"Doanh thu giảm {abs(rev_g):.1%}"
        else:
            pts, text = 0, None
        fs.append(_factor("Tăng trưởng doanh thu", f"{rev_g:+.1%}", "Tăng > 10%: +5; giảm: -5.",
                          pts, text, core=True))
    else:
        fs.append(_factor("Tăng trưởng doanh thu", "Chưa có dữ liệu", "Không được tính."))

    if f.get("valuation_type") == "bank":
        fs.append(_factor("Nợ/Vốn chủ sở hữu", "Ngân hàng",
                          "Không áp dụng: đòn bẩy cao là đặc thù ngành; chưa có dữ liệu NPL/NIM/CAR."))
    elif _ok(de):
        if de > 2:
            pts, text = -10, f"Nợ/Vốn chủ sở hữu {de:.1f} lần: đòn bẩy tài chính cao"
        elif de < 1:
            pts, text = 5, f"Nợ/Vốn chủ sở hữu {de:.1f} lần: cấu trúc vốn an toàn"
        else:
            pts, text = 0, None
        fs.append(_factor("Nợ/Vốn chủ sở hữu", f"{de:.2f}x", "Trên 2x: -10; dưới 1x: +5.",
                          pts, text, core=True))
    else:
        fs.append(_factor("Nợ/Vốn chủ sở hữu", "Chưa có dữ liệu", "Không được tính."))
    return fs


def _classify_headlines(news):
    """Mỗi tiêu đề (đã loại trùng) được xếp tích cực / tiêu cực / trung tính.
    Tiêu đề vừa có cụm tích cực vừa có cụm tiêu cực được xếp trung tính."""
    seen, pos, neg, neu = set(), 0, 0, 0
    for item in (news or {}).get("headlines") or []:
        title = (item.get("title") or "").strip().lower()
        if not title or title in seen:
            continue
        seen.add(title)
        has_pos = any(_has_phrase(title, w) for w in POSITIVE_WORDS)
        has_neg = any(_has_phrase(title, w) for w in NEGATIVE_WORDS)
        if has_pos and not has_neg:
            pos += 1
        elif has_neg and not has_pos:
            neg += 1
        else:
            neu += 1
    return pos, neg, neu


def _news_factors(n):
    fs = [_factor(*BASE_FACTOR, points=0)]
    pos, neg, neu = _classify_headlines(n)
    total = pos + neg + neu
    if total == 0:
        fs.append(_factor("Tin tích cực / tiêu cực", "Không có tin gần đây",
                          "Thiếu dữ liệu: không chấm và không coi là tin xấu."))
        return fs
    pts = max(-NEWS_MAX_ADJUST, min(NEWS_MAX_ADJUST, NEWS_POINT_PER_ITEM * (pos - neg)))
    text = None
    if pts > 0:
        text = f"Tin tức thiên tích cực ({pos} tích cực / {neg} tiêu cực trong {total} tin)"
    elif pts < 0:
        text = f"Tin tức thiên tiêu cực ({neg} tiêu cực / {pos} tích cực trong {total} tin)"
    fs.append(_factor(
        "Tin tích cực / tiêu cực", f"{pos} / {neg} (trung tính {neu}) trong {total} tin",
        f"Điều chỉnh = {NEWS_POINT_PER_ITEM} × (tích cực - tiêu cực), giới hạn ±{NEWS_MAX_ADJUST}. "
        "Đây là sentiment sơ bộ theo cụm từ khóa trong tiêu đề.", pts, text, core=True))
    return fs


# Hàm cũ giữ lại để tương thích: trả về (score hoặc None, reasons, risks)
def score_technical(t):
    fs = _technical_factors(t)
    return (_group_score(fs),) + _texts(fs)


def score_fundamental(f):
    fs = _fundamental_factors(f)
    return (_group_score(fs),) + _texts(fs)


def score_news(n):
    fs = _news_factors(n)
    return (_group_score(fs),) + _texts(fs)


def _public_factors(factors):
    """Bỏ các khóa nội bộ trước khi đưa vào bảng giải thích."""
    return [{k: f[k] for k in ("label", "evidence", "rule", "points", "applied")} for f in factors]


def _score_breakdown(factor_groups, scores, total, rec, decision_rule, decision_note):
    available = [{"name": name, "score": score, "weight": WEIGHTS[name]}
                 for name, score in scores.items() if score is not None]
    weight_sum = sum(item["weight"] for item in available)
    for item in available:
        item["weighted_points"] = item["score"] * item["weight"] / weight_sum
    return {
        "groups": available,
        "available_weight": weight_sum,
        "formula": ("Điểm tổng = trung bình có trọng số các nhóm đủ dữ liệu; trọng số được "
                    "chuẩn hóa lại khi có nhóm thiếu dữ liệu."),
        "decision_rule": decision_rule,
        "decision_note": decision_note,
        "buy_threshold": BUY_THRESHOLD,
        "sell_threshold": SELL_THRESHOLD,
        "buy_min_upside": BUY_MIN_UPSIDE,
        "sell_max_upside": SELL_MAX_UPSIDE,
        "technical_factors": _public_factors(factor_groups["technical"]),
        "fundamental_factors": _public_factors(factor_groups["fundamental"]),
        "news_factors": _public_factors(factor_groups["news"]),
        "missing_groups": [name for name, score in scores.items() if score is None],
        "total_score": total,
        "recommendation": rec,
    }


# ======================= 4. ĐỊNH GIÁ (P/E trung vị lịch sử) =======================
def compute_target_price(tech, fund):
    """Giá mục tiêu (nghìn đồng) = EPS × trung vị P/E lịch sử / 1000.
    Thiếu dữ liệu thì trả về target_price = None kèm lý do, không tự bịa."""

    def unavailable(reason):
        return {
            "target_price": None, "upside": None,
            "method": f"Chưa đủ dữ liệu định giá P/E: {reason}",
            "confidence": None,
            "valuation_methods": [{"method_name": "P/E so với trung vị lịch sử", "value": None,
                                   "weight": 0, "note": reason}],
            "valuation_summary": f"Chưa đủ dữ liệu định giá P/E: {reason} {VALUATION_SCOPE_NOTE}",
        }

    close = tech.get("close")
    if not _ok(close) or close <= 0:
        return unavailable("thiếu giá hiện tại.")
    eps = fund.get("EPS")
    if not _ok(eps) or eps <= 0:
        return unavailable("EPS không dương hoặc chưa có nên không áp dụng được P/E.")
    history = []
    for value in fund.get("historical_PE") or []:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < value <= MAX_VALID_PE:
            history.append(value)
    if len(history) < MIN_PE_HISTORY:
        return unavailable(f"chỉ có {len(history)} kỳ P/E lịch sử hợp lệ, cần tối thiểu "
                           f"{MIN_PE_HISTORY} kỳ.")

    median_pe = statistics.median(history)
    value = eps * median_pe / 1000
    target = round(value, 1)
    upside = round(target / close - 1, 4)

    cautions = []
    if fund.get("valuation_type") in ("bank", "real_estate"):
        cautions.append("P/E có thể kém đại diện với ngân hàng và bất động sản")
    if len(history) < 5:
        cautions.append(f"chỉ có {len(history)} kỳ P/E lịch sử")
    if abs(upside) > LARGE_UPSIDE_WARNING:
        cautions.append("kết quả chênh lệch lớn so với giá thị trường, cần kiểm tra EPS và P/E")
    summary = VALUATION_SCOPE_NOTE
    if cautions:
        summary += " Lưu ý: " + "; ".join(cautions) + "."
    return {
        "target_price": target,
        "upside": upside,
        "method": (f"P/E trung vị lịch sử ({median_pe:.1f} lần, {len(history)} kỳ) × "
                   f"EPS {eps:,.0f} đồng"),
        "confidence": "Thấp" if cautions else "Trung bình",
        "valuation_methods": [{
            "method_name": "P/E so với trung vị lịch sử", "value": value, "weight": 100,
            "note": f"EPS {eps:,.0f} đồng; trung vị P/E của {len(history)} kỳ là {median_pe:.2f}.",
        }],
        "valuation_summary": summary,
    }


# ======================= 5. KHUYẾN NGHỊ + TỔNG HỢP =======================
def _decision_rule_text():
    return (f"Khuyến nghị MUA khi điểm tổng >= {BUY_THRESHOLD}/100 và tiềm năng giá mục tiêu "
            f">= {BUY_MIN_UPSIDE:+.0%}; BÁN khi điểm tổng < {SELL_THRESHOLD}/100 và tiềm năng "
            f"<= {SELL_MAX_UPSIDE:+.0%}; các trường hợp còn lại là GIỮ. Nếu chưa định giá được "
            "thì chỉ đưa ra GIỮ.")


def _decide(total, upside):
    """Trả về (khuyến nghị, giải thích). Chỉ phát tín hiệu MUA/BÁN khi điểm số và định giá đồng thuận."""
    if upside is None:
        note = "Chưa định giá được nên chỉ đưa ra GIỮ, chưa đủ cơ sở để MUA hoặc BÁN."
        if total >= BUY_THRESHOLD:
            note = f"Điểm tổng {total}/100 đạt ngưỡng MUA nhưng " + note[0].lower() + note[1:]
        return "GIỮ", note
    if total >= BUY_THRESHOLD and upside >= BUY_MIN_UPSIDE:
        return "MUA", (f"Điểm tổng {total}/100 >= {BUY_THRESHOLD} và tiềm năng {upside:+.1%} "
                       f">= {BUY_MIN_UPSIDE:+.0%}: đủ điều kiện MUA.")
    if total < SELL_THRESHOLD and upside <= SELL_MAX_UPSIDE:
        return "BÁN", (f"Điểm tổng {total}/100 < {SELL_THRESHOLD} và tiềm năng {upside:+.1%} "
                       f"<= {SELL_MAX_UPSIDE:+.0%}: đủ điều kiện BÁN.")
    if total >= BUY_THRESHOLD:
        return "GIỮ", (f"Điểm tổng {total}/100 đạt ngưỡng MUA nhưng tiềm năng {upside:+.1%} "
                       f"thấp hơn {BUY_MIN_UPSIDE:+.0%}, nên giữ ở mức GIỮ.")
    if total < SELL_THRESHOLD:
        return "GIỮ", (f"Điểm tổng {total}/100 dưới ngưỡng {SELL_THRESHOLD} nhưng tiềm năng "
                       f"{upside:+.1%} chưa thấp hơn {SELL_MAX_UPSIDE:+.0%}, nên giữ ở mức GIỮ.")
    return "GIỮ", (f"Điểm tổng {total}/100 nằm giữa {SELL_THRESHOLD} và {BUY_THRESHOLD}, "
                   f"tiềm năng {upside:+.1%}.")


def get_recommendation(tech, fund, news, use_ai=True):
    tech = tech or {}
    fund = normalize_fund(fund)
    factor_groups = {"technical": _technical_factors(tech),
                     "fundamental": _fundamental_factors(fund),
                     "news": _news_factors(news)}
    scores = {k: _group_score(v) for k, v in factor_groups.items()}

    # Trung bình có trọng số, bỏ qua nhóm thiếu dữ liệu (chia lại trọng số)
    avail = {k: v for k, v in scores.items() if v is not None}
    if not avail:
        raise ValueError("Không có dữ liệu nào để chấm điểm")
    wsum = sum(WEIGHTS[k] for k in avail)
    total = _clamp(sum(WEIGHTS[k] * v for k, v in avail.items()) / wsum)

    reasons, risks = [], []
    for key in ("fundamental", "technical", "news"):
        r, k = _texts(factor_groups[key])
        reasons += r
        risks += k

    tp = compute_target_price(tech, fund)
    rec, decision_note = _decide(total, tp["upside"])
    decision_rule = _decision_rule_text()
    if total >= BUY_THRESHOLD and rec != "MUA":
        risks.insert(0, decision_note)

    result = {
        "symbol": tech.get("symbol") or fund.get("symbol"),
        "recommendation": rec,
        "decision_note": decision_note,
        "decision_rule": decision_rule,
        "total_score": total,
        "scores": scores,
        "target_price": tp["target_price"],    # nghìn đồng, None nếu chưa đủ dữ liệu
        "upside": tp["upside"],                # 0.26 = +26%
        "target_method": tp["method"],
        "target_confidence": tp["confidence"],
        "valuation_methods": tp["valuation_methods"],
        "valuation_summary": tp["valuation_summary"],
        "reasons": reasons,
        "risks": risks or ["Chưa phát hiện rủi ro nổi bật theo bộ quy tắc hiện tại"],
        "ai_summary": "",
        "ai_generated": False,
        "ai_provider": None,
        "ai_unverified_numbers": [],
    }
    result["score_breakdown"] = _score_breakdown(
        factor_groups, scores, total, rec, decision_rule, decision_note)

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


# ======================= 6. AI: PROMPT + GỌI API + DỰ PHÒNG =======================
def build_facts(tech, fund, result):
    """Gom số liệu thật thành text để đưa vào prompt (AI chỉ được dùng các số này)."""
    breakdown = {k: v for k, v in result.get("score_breakdown", {}).items()
                 if k in ("groups", "available_weight", "technical_factors",
                          "fundamental_factors", "news_factors", "missing_groups")}
    lines = [
        f"Mã cổ phiếu: {result['symbol']}",
        f"Khuyến nghị của hệ thống: {result['recommendation']} (điểm tổng hợp {result['total_score']}/100)",
        f"Quy tắc ra khuyến nghị: {result['decision_rule']}",
        f"Lý do ra khuyến nghị: {result['decision_note']}",
        f"Điểm thành phần: {result['scores']}",
        f"Cơ sở chấm điểm: {json.dumps(breakdown, ensure_ascii=False)}",
        f"Giá mục tiêu (nghìn đồng): {result['target_price']}, tiềm năng: {result['upside']}",
        f"Phương pháp định giá: {result['target_method']} (độ tin cậy: {result['target_confidence']})",
        f"Các phương pháp và tỷ trọng: {json.dumps(result['valuation_methods'], ensure_ascii=False)}",
        f"Giới hạn định giá: {result['valuation_summary']}",
        f"Chỉ số kỹ thuật (giá tính bằng nghìn đồng): {json.dumps(tech, ensure_ascii=False)}",
        f"Chỉ số cơ bản: {json.dumps({k: v for k, v in fund.items() if k != 'historical_PE'}, ensure_ascii=False)}",
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
- Viết 4 đoạn ngắn: (1) kết luận, khuyến nghị và lý do ra khuyến nghị theo quy tắc của hệ thống; (2) các chỉ số cơ bản nổi bật; (3) tín hiệu kỹ thuật và các luận cứ cụ thể; (4) định giá, giả định và giới hạn của mô hình, rủi ro.
- Chỉ giải thích số điểm bằng các điểm thành phần, trọng số, yếu tố cộng/trừ và quy tắc có trong dữ liệu; không suy diễn nguyên nhân thiếu bằng chứng.
- Nếu có chuỗi doanh thu/lợi nhuận, nêu xu hướng YoY theo năm; phân biệt ngân hàng với doanh nghiệp khác.
- Phân biệt dữ liệu thiếu với chỉ số bằng 0. Nếu chưa đủ dữ liệu định giá, nói rõ lý do, không tự thay bằng giá trị khác.
- Giá trong dữ liệu tính bằng nghìn đồng; khi viết hãy ghi rõ đơn vị.
- CHỈ sử dụng các con số có trong dữ liệu ở trên. Tuyệt đối không bịa thêm số liệu, giá mục tiêu hay dự báo.
- Nêu ngắn gọn phương pháp tính giá mục tiêu và độ tin cậy; nếu độ tin cậy là Thấp thì nói rõ đây chỉ là ước tính tham khảo.
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
    m = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=900,
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
    labels = {"technical": "kỹ thuật", "fundamental": "cơ bản", "news": "tin tức"}
    available = [f"{labels[k]} {s}/100" for k, s in r["scores"].items() if s is not None]
    missing = [labels[k] for k, s in r["scores"].items() if s is None]
    p1 = (f"Theo bộ tiêu chí định lượng, cổ phiếu {r['symbol']} đạt {r['total_score']}/100 điểm "
          f"(điểm thành phần: {', '.join(available)}), tương ứng khuyến nghị "
          f"{r['recommendation']}. {r['decision_note']}")
    if r.get("target_price") is not None:
        p1 += (f" Giá mục tiêu {_price_vnd(r['target_price'])} (tiềm năng "
               f"{r['upside'] * 100:+.1f}%), tính theo {r['target_method']}; "
               f"độ tin cậy {r.get('target_confidence') or 'chưa đánh giá'}.")
    else:
        p1 += f" {r['target_method']}."
    p2 = ("Các yếu tố ủng hộ: " + "; ".join(r["reasons"]) + ".") if r["reasons"] else \
        "Hiện chưa có yếu tố ủng hộ nổi bật."
    p3 = "Rủi ro cần theo dõi: " + "; ".join(r["risks"]) + "."
    if missing:
        p3 += f" Chưa đủ dữ liệu để chấm nhóm: {', '.join(missing)}."
    return "\n\n".join([p1, p2, p3])


# ======================= 7. DỮ LIỆU ĐIỀN VÀO TEMPLATE PDF =======================
def to_report_context(rec, tech, fund, company_name="", exchange="", industry=""):
    """Đổi kết quả sang tên biến của report_template.html.
    report_pdf.apply_scoring_to_report đọc đúng 11 khóa: recommendation, target_price, upside,
    target_method, target_confidence, valuation_methods, valuation_summary, investment_thesis,
    ai_summary, investment_points, key_risks. KHÔNG đổi tên hoặc xóa các khóa này."""
    fund = normalize_fund(fund)
    sc = rec["scores"]
    labels = {"fundamental": "cơ bản", "technical": "kỹ thuật", "news": "tin tức"}
    score_details = ", ".join(
        f"{labels[k]} {s}/100" if s is not None else f"{labels[k]} chưa đủ dữ liệu"
        for k, s in sc.items())
    if rec["target_price"] is not None:
        target_note = (f"Giá mục tiêu tính theo: {rec['target_method']} "
                       f"(độ tin cậy {rec.get('target_confidence') or 'N/A'}).")
    else:
        target_note = rec["target_method"] + "."
    thesis = (f"Hệ thống chấm {rec['total_score']}/100 điểm ({score_details}), tương ứng "
              f"khuyến nghị {rec['recommendation']}. {rec['decision_note']} {target_note}")

    price_range = "N/A"
    if _ok(tech.get("low_52w"), tech.get("high_52w")):
        price_range = f"{_price_vnd(tech['low_52w'])} - {_price_vnd(tech['high_52w'])}"
    sources = ", ".join(sorted({s for s in (tech.get("source"), fund.get("source")) if s})) or "N/A"

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
            {"method_name": item["method_name"],
             "fair_value": (f"{item['value'] * 1000:,.0f}" if item.get("value") is not None
                            else item.get("note", "Chưa đủ dữ liệu")),
             "weight": f"{item.get('weight', 0)}%" if item.get("value") is not None else "—"}
            for item in rec.get("valuation_methods", [])],
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
        "analyst_name": "Nhóm phân tích",
        "analyst_contact": "",
        "disclaimer": "",
    }


# ======================= 8. CHẠY THỬ (DỮ LIỆU MẪU, KHÔNG PHẢI SỐ THẬT) =======================
DEMO = {
    "tech": {"symbol": "DEMO", "as_of": "2026-10-09", "source": "mẫu", "close": 37.9,
             "RSI": 45.0, "MA20": 38.2, "MA50": 37.0, "MACD": 0.12, "MACD_signal": 0.05,
             "volume_ratio": 1.1, "low_52w": 33.6, "high_52w": 47.5},
    "fund": {"symbol": "DEMO", "source": "mẫu", "as_of": "2025", "EPS": 5200, "ROE": 0.18,
             "EPS_growth": 0.12, "revenue_growth": 0.15, "profit_growth": 0.14,
             "valuation_type": "non-bank", "historical_PE": [8.0, 9.5, 10.0, 11.0, 12.5, 13.0]},
    "news": {"symbol": "DEMO", "headlines": [
        {"title": "DEMO ký hợp đồng mới, lợi nhuận tăng trưởng hai chữ số"},
        {"title": "Điều kiện thị trường thuận lợi cho cổ phiếu ngành"},
        {"title": "Khối ngoại bán ròng phiên hôm nay"}]},
}

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    else:
        print("[INFO] Dùng dữ liệu MẪU trong file (số liệu không có thật).")
        d = DEMO
    out = get_recommendation(d["tech"], d["fund"], d["news"], use_ai=bool(os.getenv("USE_AI")))
    print(json.dumps(out, ensure_ascii=False, indent=2))