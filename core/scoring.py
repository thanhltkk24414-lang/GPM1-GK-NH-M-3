import json
import os
import re
import statistics
import sys
from datetime import datetime
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_ai_env():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env_file = os.path.join(project_root, ".env")
    if load_dotenv is None:
        return
    if os.path.exists(env_file):
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)


_load_ai_env()

# ======================= 1. CẤU HÌNH & NGƯỠNG ĐIỂM =======================
BUY_THRESHOLD = 65      
SELL_THRESHOLD = 45     

BUY_MIN_UPSIDE = 0.15   
SELL_MAX_UPSIDE = -0.15 

MIN_PE_HISTORY = 3      
MAX_VALID_PE = 150
MIN_NORMALIZED_EPS_HISTORY = 3
NORMALIZED_EPS_PERIODS = 5
LARGE_UPSIDE_WARNING = 0.50  
# ----- BỔ SUNG: kiểm tra số cổ phiếu, độ tin cậy, ngưỡng ngân hàng -----
SHARE_TOLERANCE = 0.05          # lệch > 5% giữa các cách suy ra số CP thì cảnh báo
MIN_PE_FOR_MEDIUM_CONFIDENCE = 8  # dưới 8 kỳ P/E thì độ tin cậy luôn là "Thấp"
# Ngưỡng ngân hàng là GIẢ ĐỊNH của nhóm, cần thay bằng trung vị ngành có nguồn.
BANK_NIM_HIGH, BANK_NIM_LOW = 0.035, 0.025
BANK_NPL_GOOD, BANK_NPL_BAD = 0.015, 0.03
BANK_CAR_MIN = 0.09
BANK_COVERAGE_MIN = 0.70


VALUATION_SCOPE_NOTE = (
    "Dải giá tham khảo = trung vị EPS dương tối đa 5 năm × Q1–Q3 P/E lịch sử "
    "của chính doanh nghiệp; cần ít nhất 3 năm EPS để gọi là EPS chuẩn hóa. "
    "P/B trung vị × BVPS là phép đối chiếu riêng, không gộp trọng số. DCF, RNAV "
    "và SOTP chưa được sử dụng vì thiếu dự phóng dòng tiền, danh mục dự án và "
    "giả định chiết khấu đáng tin cậy."
)


# ======================= 2. HÀM TIỆN ÍCH =======================
def _clamp(x):
    return max(0, min(100, round(x)))


def _ok(*vals):
    return all(v is not None for v in vals)


def _f(x, nd=1, suffix=""):
    return "N/A" if x is None else f"{x:,.{nd}f}{suffix}"


def _price_vnd(x):
    if x is None or x == "N/A":
        return "N/A"
    try:
        val = float(x)
        # Nếu giá trị đã tính theo nghìn đồng (ví dụ 20.6) thì nhân 1000, 
        # còn nếu đã là đồng lớn (>1000) thì giữ nguyên.
        if val < 1000:
            val *= 1000
        return f"{val:,.0f} đ"
    except (ValueError, TypeError):
        return str(x)


def _p(x, nd=1):
    return "N/A" if x is None else f"{x * 100:.{nd}f}%"


_WARNED = set()


def normalize_fund(fund):
    fund = dict(fund or {})
    for key in ("ROE", "ROA"):
        value = fund.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            fund[key] = None
            continue
        if 1.5 < abs(value) <= 100:
            fund[key] = value / 100

    limits = (("foreign_ownership", 1.5), ("EPS_growth", 10),
              ("revenue_growth", 10), ("profit_growth", 10))
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
            print(f"[WARN] fund['{key}']={value} lớn bất thường, kiểm tra đơn vị thập phân.", file=sys.stderr)
    return fund


def _factor(label, evidence, rule, points=None, text=None, core=False):
    return {"label": label, "evidence": evidence, "rule": rule, "points": points,
            "applied": points is not None, "text": text, "core": core}


def _group_score(factors):
    technical_factors = [
        factor
        for factor in factors
        if factor.get("score") is not None and factor.get("weight") is not None
    ]
    if technical_factors:
        total_weight = sum(factor["weight"] for factor in technical_factors)
        if total_weight < 60:
            return None
        weighted_score = sum(
            factor["score"] * factor["weight"] for factor in technical_factors
        ) / total_weight
        return _clamp(weighted_score)
    if not any(f["core"] and f["points"] is not None for f in factors):
        return None
    return _clamp(50 + sum(f["points"] for f in factors if f["points"] is not None))


def _texts(factors):
    reasons, risks = [], []
    for f in factors:
        if not f.get("text") or f["points"] is None:
            continue
        if f["points"] > 0 and f["text"] not in reasons:
            reasons.append(f["text"])
        elif f["points"] < 0 and f["text"] not in risks:
            risks.append(f["text"])
    return reasons, risks


BASE_FACTOR = ("Điểm nền", "50 điểm", "Bắt đầu mỗi nhóm từ 50 điểm.")

TECHNICAL_FACTOR_WEIGHTS = {
    "Xu hướng": 45,
    "Động lượng": 25,
    "Khối lượng": 20,
    "Hỗ trợ/kháng cự": 10,
}


def _technical_factor(label, category, weight, score, evidence, rule, text=None):
    if score is None:
        return {
            "label": label,
            "category": category,
            "weight": weight,
            "score": None,
            "evidence": evidence,
            "rule": rule,
            "points": None,
            "applied": False,
            "text": None,
            "core": True,
        }
    score = max(0, min(100, float(score)))
    return {
        "label": label,
        "category": category,
        "weight": weight,
        "score": score,
        "evidence": evidence,
        "rule": rule,
        "points": round(score - 50),
        "applied": True,
        "text": text,
        "core": True,
    }


# ======================= 3. CHẤM ĐIỂM TỪNG NHÓM (LOGIC CHUYÊN SÂU) =======================
def _technical_factors(t):
    close = t.get("signal_close", t.get("close"))
    ma20, ma50, ma200 = t.get("MA20"), t.get("MA50"), t.get("MA200")
    rsi = t.get("RSI")
    macd, sig = t.get("MACD"), t.get("MACD_signal")
    vol = t.get("volume_ratio")
    fs = []

    def add_ma_factor(label, left, right, weight, rule):
        if not _ok(left, right):
            fs.append(_technical_factor(
                label, "Xu hướng", weight, None, "Chưa đủ lịch sử giá", rule
            ))
            return
        is_bullish = left > right
        factor_notes = {
            "Giá so với MA20": (
                "Giá đang trên MA20, cho thấy xu hướng ngắn hạn đang thuận lợi."
                if is_bullish else
                "Giá dưới MA20, cho thấy áp lực ngắn hạn; cần lấy lại đường này để cải thiện tín hiệu."
            ),
            "MA20 so với MA50": (
                "MA20 trên MA50, xác nhận đà ngắn hạn đang tốt hơn trung hạn."
                if is_bullish else
                "MA20 dưới MA50, cho thấy đà ngắn hạn yếu hơn xu hướng trung hạn."
            ),
            "MA50 so với MA200": (
                "MA50 trên MA200, ủng hộ xu hướng tăng trung hạn."
                if is_bullish else
                "MA50 dưới MA200, xu hướng trung hạn chưa xác nhận trạng thái tăng."
            ),
        }
        fs.append(_technical_factor(
            label,
            "Xu hướng",
            weight,
            100 if is_bullish else 0,
            f"{_price_vnd(left)} {'>' if is_bullish else '<='} {_price_vnd(right)}",
            rule,
            factor_notes[label],
        ))

    add_ma_factor("Giá so với MA20", close, ma20, 10, "Giá > MA20: 100; ngược lại: 0.")
    add_ma_factor("MA20 so với MA50", ma20, ma50, 10, "MA20 > MA50: 100; ngược lại: 0.")
    add_ma_factor("MA50 so với MA200", ma50, ma200, 15, "MA50 > MA200: 100; ngược lại: 0.")

    ma_cross = t.get("ma50_ma200_cross")
    if ma_cross is None:
        fs.append(_technical_factor(
            "Giao cắt MA50/MA200", "Xu hướng", 10, None,
            "Chưa đủ lịch sử giá", "Golden Cross phiên gần nhất: 100; Death Cross: 0; không giao cắt: 50.",
        ))
    else:
        cross_scores = {"golden_cross": 100, "death_cross": 0, "none": 50}
        cross_labels = {
            "golden_cross": "Golden Cross mới xác nhận",
            "death_cross": "Death Cross mới xác nhận",
            "none": "Không có giao cắt mới ở phiên gần nhất",
        }
        fs.append(_technical_factor(
            "Giao cắt MA50/MA200",
            "Xu hướng",
            10,
            cross_scores[ma_cross],
            cross_labels[ma_cross],
            "Golden Cross phiên gần nhất: 100; Death Cross: 0; không giao cắt: 50.",
            cross_labels[ma_cross] if ma_cross != "none" else None,
        ))

    if _ok(rsi):
        if rsi < 20:
            rsi_score, rsi_note = 20, "RSI rất thấp; quá bán không đồng nghĩa chắc chắn đảo chiều."
        elif rsi < 30:
            rsi_score, rsi_note = 35, "RSI quá bán; cần tín hiệu xác nhận đảo chiều."
        elif rsi < 40:
            rsi_score, rsi_note = 55, None
        elif rsi <= 60:
            rsi_score, rsi_note = 100, "RSI trong vùng động lượng cân bằng/tích cực."
        elif rsi <= 70:
            rsi_score, rsi_note = 75, None
        elif rsi <= 80:
            rsi_score, rsi_note = 35, "RSI quá mua; có rủi ro điều chỉnh."
        else:
            rsi_score, rsi_note = 15, "RSI quá mua mạnh; rủi ro điều chỉnh cao."
        fs.append(_technical_factor(
            "RSI (14)", "Động lượng", 15, rsi_score, f"{rsi:.2f}",
            "40–60: 100; 30–40: 55; 60–70: 75; ngoài vùng này giảm điểm; quá bán không tự động là tín hiệu mua.",
            rsi_note,
        ))
    else:
        fs.append(_technical_factor(
            "RSI (14)", "Động lượng", 15, None, "Chưa đủ lịch sử giá",
            "Điểm theo ngưỡng RSI; quá mua/quá bán được xem là rủi ro, không mặc định đảo chiều.",
        ))

    if _ok(macd, sig):
        if macd > sig and macd > 0:
            macd_score, macd_note = 100, "MACD trên Signal và trên mốc 0."
        elif macd > sig:
            macd_score, macd_note = 65, "MACD trên Signal nhưng còn dưới mốc 0."
        elif macd > 0:
            macd_score, macd_note = 35, "MACD dưới Signal dù còn trên mốc 0."
        else:
            macd_score, macd_note = 0, "MACD dưới Signal và dưới mốc 0."
        fs.append(_technical_factor(
            "MACD / Signal", "Động lượng", 10, macd_score,
            f"MACD {macd:.4f}; Signal {sig:.4f}",
            "MACD > Signal và > 0: 100; trên Signal nhưng < 0: 65; dưới Signal nhưng > 0: 35; còn lại: 0.",
            macd_note,
        ))
    else:
        fs.append(_technical_factor(
            "MACD / Signal", "Động lượng", 10, None, "Chưa đủ lịch sử giá",
            "Chấm đồng thời vị trí MACD so với Signal và mốc 0.",
        ))

    price_change = t.get("price_change_pct")
    breakout = t.get("breakout_20d")
    if _ok(vol, price_change):
        if breakout and vol >= 1.5:
            volume_score, volume_note = 100, "Vượt đỉnh 20 phiên kèm khối lượng ít nhất 1,5 lần trung bình."
        elif breakout and vol >= 1:
            volume_score, volume_note = 80, "Vượt đỉnh 20 phiên với khối lượng xác nhận."
        elif price_change > 0 and vol >= 1.3:
            volume_score, volume_note = 75, "Giá tăng cùng khối lượng cao hơn trung bình."
        elif price_change > 0 and vol < 0.8:
            volume_score, volume_note = 30, "Giá tăng nhưng khối lượng thấp; lực xác nhận yếu."
        elif price_change < 0 and vol >= 1.3:
            volume_score, volume_note = 20, "Giá giảm cùng khối lượng cao; áp lực bán được xác nhận."
        elif price_change < 0 and vol < 0.8:
            volume_score, volume_note = 65, "Giá giảm với khối lượng thấp; chưa đủ căn cứ kết luận đảo chiều."
        else:
            volume_score, volume_note = 50, None
        fs.append(_technical_factor(
            "Giá và khối lượng", "Khối lượng", 20, volume_score,
            f"Biến động giá {price_change:+.2f}%; KL/20 phiên {vol:.2f}x"
            + ("; vượt đỉnh 20 phiên" if breakout else ""),
            "Chấm kết hợp hướng biến động giá, breakout 20 phiên và khối lượng so với 20 phiên trước.",
            volume_note,
        ))
    else:
        fs.append(_technical_factor(
            "Giá và khối lượng", "Khối lượng", 20, None, "Chưa đủ dữ liệu giá/khối lượng",
            "Cần biến động giá và khối lượng so với 20 phiên trước.",
        ))

    support_distance = t.get("support_distance_pct")
    if _ok(support_distance):
        support_score = (
            0 if support_distance < 0
            else 90 if support_distance <= 0.03
            else 75 if support_distance <= 0.08
            else 55 if support_distance <= 0.15
            else 35
        )
        fs.append(_technical_factor(
            "Khoảng cách tới hỗ trợ 60 phiên",
            "Hỗ trợ/kháng cự",
            5,
            support_score,
            f"Cách đáy 60 phiên {support_distance:.2%}",
            "Gần vùng đáy 60 phiên được điểm cao hơn; thủng đáy tham chiếu: 0; vùng này không phải hỗ trợ chắc chắn.",
        ))
    else:
        fs.append(_technical_factor(
            "Khoảng cách tới hỗ trợ 60 phiên", "Hỗ trợ/kháng cự", 5, None,
            "Chưa đủ lịch sử OHLC", "Ước lượng từ đáy 60 phiên gần nhất.",
        ))

    resistance_distance = t.get("resistance_distance_pct")
    if _ok(resistance_distance):
        resistance_score = (
            70 if resistance_distance < 0
            else 25 if resistance_distance <= 0.03
            else 40 if resistance_distance <= 0.08
            else 55 if resistance_distance <= 0.15
            else 65
        )
        fs.append(_technical_factor(
            "Khoảng cách tới kháng cự 60 phiên",
            "Hỗ trợ/kháng cự",
            5,
            resistance_score,
            f"Cách đỉnh 60 phiên {resistance_distance:.2%}",
            "Gần vùng đỉnh 60 phiên bị trừ điểm do rủi ro cản giá; vượt đỉnh tham chiếu được chấm riêng ở volume; vùng này không phải kháng cự chắc chắn.",
        ))
    else:
        fs.append(_technical_factor(
            "Khoảng cách tới kháng cự 60 phiên", "Hỗ trợ/kháng cự", 5, None,
            "Chưa đủ lịch sử OHLC", "Ước lượng từ đỉnh 60 phiên gần nhất.",
        ))

    return fs


def _valid_historical_pe(fund):
    fund = fund or {}
    history = fund.get("historical_PE")
    if history is None or len(history) == 0:
        historical_valuation = fund.get("historical_valuation")
        if isinstance(historical_valuation, dict):
            history = historical_valuation.get("PE")

    valid_values = []
    for value in (history if history is not None else []):
        try:
            pe_value = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < pe_value <= MAX_VALID_PE:
            valid_values.append(pe_value)
    return valid_values


def _valid_positive_history(fund, field, nested_field):
    history = (fund or {}).get(field)
    if history is None or len(history) == 0:
        historical_valuation = (fund or {}).get("historical_valuation")
        if isinstance(historical_valuation, dict):
            history = historical_valuation.get(nested_field)

    values = []
    for value in history if history is not None else []:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if numeric_value > 0:
            values.append(numeric_value)
    return values


def _linear_pts(value, full_at, cap):
    """Điểm tuyến tính có chặn: value = full_at thì được đủ cap điểm, âm thì trừ tương ứng."""
    return int(round(max(-cap, min(cap, value / full_at * cap))))


def _bank_factors(f):
    """Chấm các chỉ số đặc thù ngân hàng. Thiếu dữ liệu thì ghi rõ, không chấm thay."""
    fs = []
    nim = f.get("net_interest_margin")
    npl = f.get("npl")
    car = f.get("capital_adequacy_ratio")
    coverage = f.get("loan_loss_coverage_ratio")

    if _ok(nim):
        if nim >= BANK_NIM_HIGH:
            pts, text = 10, f"NIM {nim:.2%} cao: biên lãi thuần tốt"
        elif nim < BANK_NIM_LOW:
            pts, text = -10, f"NIM {nim:.2%} thấp: biên lãi thuần bị thu hẹp"
        else:
            pts, text = 0, None
        fs.append(_factor("Biên lãi thuần (NIM)", f"{nim:.2%}",
                          f"NIM >= {BANK_NIM_HIGH:.1%}: +10; < {BANK_NIM_LOW:.1%}: -10 (ngưỡng giả định của nhóm).",
                          pts, text, core=True))
    else:
        fs.append(_factor("Biên lãi thuần (NIM)", "Chưa có dữ liệu", "Không được tính."))

    if _ok(npl):
        if npl < BANK_NPL_GOOD:
            pts, text = 10, f"Nợ xấu {npl:.2%} thấp: chất lượng tài sản tốt"
        elif npl > BANK_NPL_BAD:
            pts, text = -15, f"Nợ xấu {npl:.2%} cao: rủi ro chất lượng tài sản"
        else:
            pts, text = 0, None
        fs.append(_factor("Tỷ lệ nợ xấu (NPL)", f"{npl:.2%}",
                          f"NPL < {BANK_NPL_GOOD:.1%}: +10; > {BANK_NPL_BAD:.1%}: -15 (ngưỡng giả định của nhóm).",
                          pts, text, core=True))
    else:
        fs.append(_factor("Tỷ lệ nợ xấu (NPL)", "Chưa có dữ liệu", "Không được tính."))

    if _ok(car):
        pts = -15 if car < BANK_CAR_MIN else 0
        text = f"CAR {car:.2%} dưới {BANK_CAR_MIN:.0%}: đệm vốn mỏng" if pts else None
        fs.append(_factor("Hệ số an toàn vốn (CAR)", f"{car:.2%}",
                          f"CAR < {BANK_CAR_MIN:.0%}: -15.", pts, text, core=True))
    else:
        fs.append(_factor("Hệ số an toàn vốn (CAR)", "Chưa có dữ liệu", "Không được tính."))

    if _ok(coverage):
        pts = -10 if coverage < BANK_COVERAGE_MIN else 0
        text = f"Bao phủ nợ xấu {coverage:.0%} thấp: dự phòng mỏng" if pts else None
        fs.append(_factor("Tỷ lệ bao phủ nợ xấu", f"{coverage:.0%}",
                          f"Bao phủ < {BANK_COVERAGE_MIN:.0%}: -10.", pts, text, core=True))
    else:
        fs.append(_factor("Tỷ lệ bao phủ nợ xấu", "Chưa có dữ liệu", "Không được tính."))
    return fs


def _fundamental_factors(f):
    fs = [_factor(*BASE_FACTOR, points=0)]
    is_bank = f.get("valuation_type") == "bank"
    roe, eps_g = f.get("ROE"), f.get("EPS_growth")
    profit_g = f.get("profit_growth")
    rev_g, de = f.get("revenue_growth"), f.get("debt_to_equity")

    # 1. ROE
    if _ok(roe):
        if roe >= 0.20:
            pts, text = 20, f"ROE xuất sắc {roe:.1%} (>= 20%): năng lực sinh lời vượt trội"
        elif roe >= 0.12:
            pts, text = 12, f"ROE tốt {roe:.1%} (>= 12%): hiệu quả sử dụng vốn khá"
        elif roe < 0.06:
            pts, text = -15, f"ROE thấp {roe:.1%} (< 6%): hiệu quả sinh lời kém"
        else:
            pts, text = 0, None
        fs.append(_factor("Hiệu quả sinh lời (ROE)", f"{roe:.1%}", "ROE >= 20%: +20; >= 12%: +12; < 6%: -15.", pts, text, core=True))
    else:
        fs.append(_factor("ROE", "Chưa có dữ liệu", "Không được tính."))

    # 2. Tăng trưởng lợi nhuận.
    # Dùng LNST thay EPS khi là ngân hàng hoặc khi số cổ phiếu thay đổi (EPS bị pha loãng),
    # vì EPS nhà cung cấp chưa điều chỉnh theo số cổ phiếu hiện tại.
    use_profit = _ok(profit_g) and (is_bank or f.get("shares_changed") or not _ok(eps_g))
    growth = profit_g if use_profit else eps_g
    growth_name = "LNST" if use_profit else "EPS"
    growth_label = f"Tăng trưởng {growth_name}"
    if _ok(growth):
        pts = _linear_pts(growth, 0.15, 15)
        if pts > 0:
            text = f"{growth_name} tăng {growth:.1%} so với năm trước"
        elif pts < 0:
            text = f"{growth_name} giảm {abs(growth):.1%} so với năm trước"
        else:
            text = None
        fs.append(_factor(growth_label, f"{growth:+.1%}",
                          "Tuyến tính: +15% trở lên = +15 điểm; -15% trở xuống = -15 điểm.",
                          pts, text, core=True))
    else:
        fs.append(_factor("Tăng trưởng lợi nhuận", "Chưa có dữ liệu", "Không được tính."))

    # 3. Tăng trưởng doanh thu (ngân hàng: thu nhập lãi thuần)
    income_label = "thu nhập lãi thuần" if is_bank else "doanh thu"
    if _ok(rev_g):
        pts = _linear_pts(rev_g, 0.10, 10)
        if pts > 0:
            text = f"{income_label.capitalize()} tăng {rev_g:.1%}"
        elif pts < 0:
            text = f"{income_label.capitalize()} giảm {abs(rev_g):.1%}"
        else:
            text = None
        fs.append(_factor(f"Tăng trưởng {income_label}", f"{rev_g:+.1%}",
                          "Tuyến tính: +10% trở lên = +10 điểm; -10% trở xuống = -10 điểm.",
                          pts, text, core=True))
    else:
        fs.append(_factor(f"Tăng trưởng {income_label}", "Chưa có dữ liệu", "Không được tính."))

    # 4. Đòn bẩy (doanh nghiệp thường) hoặc chỉ số đặc thù (ngân hàng)
    if is_bank:
        fs.extend(_bank_factors(f))
    elif _ok(de):
        if de > 2.0:
            pts, text = -15, f"Tỷ lệ Nợ/VCSH cao {de:.1f} lần: rủi ro tài chính lớn"
        elif de < 0.8:
            pts, text = 10, f"Tỷ lệ Nợ/VCSH lành mạnh {de:.1f} lần: cấu trúc vốn an toàn"
        else:
            pts, text = 0, None
        fs.append(_factor("Cấu trúc Nợ/VCSH", f"{de:.2f}x", "D/E > 2x: -15; D/E < 0.8x: +10.", pts, text, core=True))
    else:
        fs.append(_factor("Cấu trúc Nợ/VCSH", "Chưa có dữ liệu", "Không được tính."))
    return fs



def score_technical(t):
    fs = _technical_factors(t)
    return (_group_score(fs),) + _texts(fs)


def score_fundamental(f):
    fs = _fundamental_factors(f)
    return (_group_score(fs),) + _texts(fs)


def _public_factors(factors):
    return [
        {
            key: factor[key]
            for key in (
                "label", "category", "evidence", "rule", "points",
                "applied", "score", "weight", "text",
            )
            if key in factor
        }
        for factor in factors
    ]


def _score_breakdown(factor_groups, scores, total, rec, decision_rule, decision_note, weights):
    available = [{"name": name, "score": score, "weight": weights[name]} for name, score in scores.items() if score is not None]
    for item in available:
        item["weighted_points"] = item["score"] * item["weight"]
    return {
        "groups": available,
        "available_weight": sum(item["weight"] for item in available),
        "total_score": total,
        "recommendation": rec,
        "decision_rule": decision_rule,
        "decision_note": decision_note,
        "technical_factors": _public_factors(factor_groups["technical"]),
        "technical_coverage": round(
            sum(
                factor["weight"]
                for factor in factor_groups["technical"]
                if factor.get("score") is not None
            )
        ),
        "fundamental_factors": _public_factors(factor_groups["fundamental"]),
    }


def get_dynamic_weights(fund=None):
    """Return fixed, equal weights for fundamental and technical analysis."""
    return {"fundamental": 0.50, "technical": 0.50}


# ======================= 4. ĐỊNH GIÁ =======================
def _share_adjusted_inputs(fund, close):
    """Tính lại EPS/BVPS theo SỐ CỔ PHIẾU HIỆN TẠI.

    EPS/BVPS lịch sử của nhà cung cấp tính trên số cổ phiếu từng năm. Sau các đợt chia
    cổ tức bằng cổ phiếu, đem trung vị EPS cũ nhân P/E rồi so với giá hôm nay sẽ thổi phồng
    upside. Hàm trả về None nếu thiếu số cổ phiếu hoặc lợi nhuận lịch sử (giữ cách cũ).

    Đơn vị: net profit tính bằng tỷ đồng, equity_latest và market_cap tính bằng đồng,
    close tính bằng nghìn đồng.
    """
    fund = fund or {}
    try:
        shares = float(fund.get("shares_outstanding") or 0)
    except (TypeError, ValueError):
        shares = 0
    profit_by_year = fund.get("historical_net_profit") or {}
    if shares <= 0 or not isinstance(profit_by_year, dict) or not profit_by_year:
        return None

    ordered = []
    for year, value in profit_by_year.items():
        try:
            ordered.append((int(year), float(value)))
        except (TypeError, ValueError):
            continue
    ordered.sort()
    if not ordered:
        return None

    eps_history = [value * 1e9 / shares for _, value in ordered if value > 0]
    latest_profit = ordered[-1][1]
    latest_eps = latest_profit * 1e9 / shares if latest_profit > 0 else None

    bvps = None
    try:
        equity = float(fund.get("equity_latest"))
        if equity > 0:
            bvps = equity / shares
    except (TypeError, ValueError):
        pass

    warnings = []
    vendor_gap = None
    try:
        vendor_eps = float(fund.get("EPS"))
        if vendor_eps > 0 and latest_profit > 0:
            vendor_gap = abs(latest_profit * 1e9 / vendor_eps / shares - 1)
    except (TypeError, ValueError):
        pass
    if vendor_gap is not None and vendor_gap > SHARE_TOLERANCE:
        warnings.append(
            f"EPS của nhà cung cấp lệch {vendor_gap:.0%} so với số cổ phiếu đang lưu hành; "
            "đã tính lại EPS và BVPS theo số cổ phiếu hiện tại."
        )

    market_gap = None
    try:
        market_cap = float(fund.get("market_cap"))
        if market_cap > 0 and close and close > 0:
            market_gap = abs(market_cap / (close * 1000) / shares - 1)
    except (TypeError, ValueError):
        pass

    return {
        "shares": shares,
        "eps_history": eps_history,
        "latest_eps": latest_eps,
        "bvps": bvps,
        "shares_changed": vendor_gap is not None and vendor_gap > SHARE_TOLERANCE,
        "market_cap_gap": market_gap,
        "warnings": warnings,
    }


def compute_target_price(tech, fund):
    history = _valid_historical_pe(fund)
    median_pe = statistics.median(history) if history else None
    pb_history = _valid_positive_history(fund, "historical_PB", "PB")
    median_pb = statistics.median(pb_history) if pb_history else None
    bvps_history = _valid_positive_history(fund, "historical_BVPS", "BVPS")
    bvps = (fund or {}).get("BVPS")
    try:
        bvps = float(bvps)
    except (TypeError, ValueError):
        bvps = None
    if bvps is None or bvps <= 0:
        bvps = bvps_history[-1] if bvps_history else None
    adjusted = _share_adjusted_inputs(fund, (tech or {}).get("close"))
    if adjusted and adjusted["bvps"]:
        bvps = adjusted["bvps"]
    pb_target = (
        median_pb * bvps / 1000
        if (
            len(pb_history) >= MIN_PE_HISTORY
            and median_pb is not None
            and bvps is not None
            and bvps > 0
        )
        else None
    )
    close = (tech or {}).get("close")
    try:
        latest_eps = float((fund or {}).get("EPS"))
    except (TypeError, ValueError):
        latest_eps = None
    if adjusted and adjusted["latest_eps"]:
        latest_eps = adjusted["latest_eps"]
    current_pe = (
        close * 1000 / latest_eps
        if _ok(close, latest_eps) and close > 0 and latest_eps > 0
        else None
    )

    eps_history = _valid_positive_history(fund, "historical_EPS", "EPS")
    if adjusted and adjusted["eps_history"]:
        eps_history = adjusted["eps_history"]
    eps_history = eps_history[-NORMALIZED_EPS_PERIODS:]
    normalized_eps = (
        statistics.median(eps_history)
        if len(eps_history) >= MIN_NORMALIZED_EPS_HISTORY
        else None
    )
    eps_is_normalized = normalized_eps is not None
    eps_count = len(eps_history)
    if not eps_is_normalized:
        normalized_eps = latest_eps if latest_eps is not None and latest_eps > 0 else None

    def unavailable(reason):
        if len(history) < MIN_PE_HISTORY:
            summary_reason = (
                f"{reason} Cần tối thiểu {MIN_PE_HISTORY} kỳ P/E hợp lệ."
            )
        else:
            summary_reason = reason
        return {
            "target_price": None, "upside": None,
            "method": f"Không hiển thị giá mục tiêu P/E: {reason}",
            "confidence": None,
            "valuation_methods": [
                {
                    "method_name": "P/E chuẩn hóa theo trung vị lịch sử",
                    "value": None,
                    "low": None,
                    "high": None,
                    "role": "Phương pháp chính",
                    "note": reason,
                },
                {
                    "method_name": "P/B lịch sử × BVPS gần nhất",
                    "value": pb_target,
                    "role": "Đối chiếu",
                    "note": (
                        f"P/B trung vị {median_pb:.2f} × BVPS {bvps:,.0f} đ"
                        if pb_target is not None
                        else "Chưa đủ dữ liệu P/B hoặc BVPS."
                    ),
                },
            ],
            "valuation_summary": (
                f"Chưa thể đưa ra giá mục tiêu ước tính: {summary_reason} "
                "P/B được giữ riêng để đối chiếu, không gộp tùy ý vào P/E."
            ),
            "valuation_reference_pe": median_pe,
            "valuation_current_pe": current_pe,
            "valuation_history_count": len(history),
            "valuation_pe_low": None,
            "valuation_pe_high": None,
            "valuation_eps": normalized_eps,
            "valuation_eps_count": len(eps_history),
            "valuation_eps_normalized": eps_is_normalized,
            "valuation_eps_basis": (
                f"Trung vị {eps_count} năm EPS dương"
                if eps_is_normalized
                else f"EPS gần nhất; chỉ có {eps_count} năm EPS dương"
            ),
            "valuation_pb_reference": median_pb,
            "valuation_pb_count": len(pb_history),
            "valuation_pb_target": pb_target,
        }

    if not _ok(close) or close <= 0: return unavailable("thiếu giá hiện tại.")
    if adjusted and adjusted["market_cap_gap"] is not None and adjusted["market_cap_gap"] > SHARE_TOLERANCE:
        return unavailable(
            f"số cổ phiếu lưu hành lệch {adjusted['market_cap_gap']:.0%} so với vốn hóa ÷ giá; "
            "dữ liệu không nhất quán nên không xuất giá mục tiêu."
        )
    if normalized_eps is None or normalized_eps <= 0:
        return unavailable("EPS chuẩn hóa hoặc EPS gần nhất không dương.")
    if len(history) < MIN_PE_HISTORY: return unavailable(f"chỉ có {len(history)} kỳ P/E hợp lệ.")

    pe_quartiles = statistics.quantiles(history, n=4, method="inclusive")
    pe_low, pe_high = pe_quartiles[0], pe_quartiles[2]
    target_low = normalized_eps * pe_low / 1000
    target_high = normalized_eps * pe_high / 1000
    target = normalized_eps * median_pe / 1000
    upside_low = target_low / close - 1
    upside_high = target_high / close - 1
    upside = round(target / close - 1, 4)
    warnings = list(adjusted["warnings"]) if adjusted else []
    if len(history) < MIN_PE_FOR_MEDIUM_CONFIDENCE:
        warnings.append(
            f"Chỉ có {len(history)} kỳ P/E lịch sử (cần ít nhất {MIN_PE_FOR_MEDIUM_CONFIDENCE}); "
            "dải Q1–Q3 chỉ mang tính tham khảo."
        )
    if not eps_is_normalized:
        warnings.append(
            f"Chỉ có {len(eps_history)} năm EPS dương; giá P/E tạm dùng EPS gần nhất, "
            "chưa đủ dữ liệu chuẩn hóa."
        )
    if upside_high > LARGE_UPSIDE_WARNING:
        warnings.append(
            f"Cận trên của dải P/E cho upside {upside_high:+.1%}, vượt ngưỡng "
            f"cảnh báo {LARGE_UPSIDE_WARNING:.0%}; cần kiểm tra giả định và lợi nhuận."
        )
    if pb_target is not None and abs(pb_target / target - 1) > LARGE_UPSIDE_WARNING:
        warnings.append(
            "Giá đối chiếu P/B chênh trên 50% so với giá giữa dải P/E; "
            "hai phương pháp đang cho tín hiệu khác nhau."
        )
    valuation_warning = " ".join(warnings) or None
    valuation_range = (round(target_low, 2), round(target_high, 2))
    upside_range = (round(upside_low, 4), round(upside_high, 4))
    confidence = (
        "Thấp"
        if valuation_warning or not eps_is_normalized
        else "Trung bình"
    )
    eps_basis = (
        f"trung vị {len(eps_history)} năm EPS dương"
        + (" (tính lại theo số cổ phiếu hiện tại)" if adjusted else "")
        if eps_is_normalized
        else f"EPS gần nhất (chỉ có {eps_count} năm EPS dương, chưa đủ chuẩn hóa)"
    )
    eps_method_name = (
        "P/E lịch sử × EPS chuẩn hóa"
        if eps_is_normalized
        else "P/E lịch sử × EPS gần nhất (chưa chuẩn hóa)"
    )
    valuation_methods = [
        {
            "method_name": eps_method_name,
            "value": target,
            "low": target_low,
            "high": target_high,
            "role": "Phương pháp chính",
            "note": (
                f"EPS {eps_basis}: {normalized_eps:,.0f} đ; "
                f"P/E Q1–Q3: {pe_low:.2f}–{pe_high:.2f} lần."
            ),
        },
        {
            "method_name": "P/B lịch sử × BVPS gần nhất",
            "value": pb_target,
            "role": "Đối chiếu",
            "note": (
                f"P/B trung vị {median_pb:.2f} lần × BVPS {bvps:,.0f} đ"
                if pb_target is not None
                else "Chưa đủ dữ liệu P/B hoặc BVPS."
            ),
        },
    ]
    return {
        "target_price": target, "upside": upside,
        "target_price_range": valuation_range,
        "upside_range": upside_range,
        "method": (
            f"{eps_basis.capitalize()} {normalized_eps:,.0f} đ × P/E trung vị "
            f"{median_pe:.2f} lần; dải Q1–Q3 P/E {pe_low:.2f}–{pe_high:.2f} lần"
        ),
        "confidence": confidence,
        "valuation_methods": valuation_methods,
        "valuation_summary": (
            f"Dải giá P/E dùng EPS {eps_basis} và Q1–Q3 của {len(history)} kỳ P/E "
            f"hợp lệ. Giá giữa dải dùng P/E trung vị {median_pe:.2f}. "
            "P/B × BVPS được trình bày riêng để đối chiếu, không trộn trọng số "
            "vì chưa có cơ sở thống kê để gán trọng số."
            + (f" {valuation_warning}" if valuation_warning else "")
        ),
        "valuation_reference_pe": median_pe,
        "valuation_current_pe": current_pe,
        "valuation_history_count": len(history),
        "valuation_pe_low": pe_low,
        "valuation_pe_high": pe_high,
        "valuation_eps": normalized_eps,
        "valuation_eps_count": len(eps_history),
        "valuation_eps_normalized": eps_is_normalized,
        "valuation_eps_basis": (
            f"Trung vị {eps_count} năm EPS dương"
            if eps_is_normalized
            else f"EPS gần nhất; chỉ có {eps_count} năm EPS dương"
        ),
        "valuation_pb_reference": median_pb,
        "valuation_pb_count": len(pb_history),
        "valuation_pb_target": pb_target,
        "valuation_warning": valuation_warning,
        "valuation_shares_adjusted": bool(adjusted),
        "valuation_shares_changed": bool(adjusted and adjusted["shares_changed"]),
    }



# ======================= 5. KHUYẾN NGHỊ =======================
def _decision_rule_text():
    return (
        f"Với dải định giá, MUA khi điểm tổng >= {BUY_THRESHOLD}, cận thấp "
        f"upside >= {BUY_MIN_UPSIDE:.0%} và cận cao <= {LARGE_UPSIDE_WARNING:.0%}; "
        f"BÁN khi điểm tổng < {SELL_THRESHOLD} và toàn dải upside <= "
        f"{SELL_MAX_UPSIDE:.0%}; còn lại là GIỮ."
    )


def _decide(total, upside, upside_range=None):
    if upside is None:
        return "GIỮ", "Chưa đủ dữ liệu định giá để phát tín hiệu MUA/BÁN."
    if upside_range:
        downside, upside_limit = upside_range
        if total < SELL_THRESHOLD and upside_limit <= SELL_MAX_UPSIDE:
            return (
                "BÁN",
                f"Toàn bộ dải upside P/E ({downside:+.1%} đến {upside_limit:+.1%}) "
                "đều thấp hơn ngưỡng cảnh báo.",
            )
        if (
            total >= BUY_THRESHOLD
            and downside >= BUY_MIN_UPSIDE
            and upside_limit <= LARGE_UPSIDE_WARNING
        ):
            return (
                "MUA",
                f"Điểm tổng {total}/100 đạt chuẩn và cận thấp của dải upside "
                f"({downside:+.1%}) vẫn vượt ngưỡng {BUY_MIN_UPSIDE:.0%}.",
            )
        return (
            "GIỮ",
            f"Dải upside P/E ({downside:+.1%} đến {upside_limit:+.1%}) chưa đủ "
            "đồng thuận để phát tín hiệu MUA/BÁN.",
        )
    if upside > LARGE_UPSIDE_WARNING:
        return (
            "GIỮ",
            f"Upside mô hình {upside:+.1%} vượt ngưỡng cảnh báo "
            f"{LARGE_UPSIDE_WARNING:.0%}; cần xác minh định giá trước khi dùng "
            "làm tín hiệu MUA.",
        )
    if total >= BUY_THRESHOLD and upside >= BUY_MIN_UPSIDE:
        return "MUA", f"Điểm tổng {total}/100 đạt chuẩn và tiềm năng tăng trưởng {upside:+.1%} thỏa mãn kỳ vọng."
    if total < SELL_THRESHOLD and upside <= SELL_MAX_UPSIDE:
        return "BÁN", f"Điểm tổng {total}/100 thấp và biên an toàn âm ({upside:+.1%})."
    return "GIỮ", f"Điểm số ({total}/100) và tiềm năng giá ({upside:+.1%}) nằm ở vùng quan sát trung lập."


def get_recommendation(tech, fund, news=None, use_ai=True):
    tech, fund = tech or {}, normalize_fund(fund)
    share_check = _share_adjusted_inputs(fund, tech.get("close"))
    if share_check and share_check["shares_changed"]:
        fund["shares_changed"] = True   # EPS bị pha loãng -> chấm tăng trưởng theo LNST
    weights = get_dynamic_weights(fund)
    factor_groups = {
        "technical": _technical_factors(tech),
        "fundamental": _fundamental_factors(fund),
    }
    scores = {k: _group_score(v) for k, v in factor_groups.items()}
    # Giữ 1 chữ số thập phân: so ngưỡng bằng số chưa làm tròn (41.5 không bị đẩy thành 42).
    total = None
    if all(scores.get(name) is not None for name in weights):
        total_raw = max(0.0, min(100.0, sum(weights[name] * scores[name] for name in weights)))
        total = int(total_raw) if float(total_raw).is_integer() else round(total_raw, 1)

    reasons, risks = [], []
    for key in ("fundamental", "technical"):
        r, k = _texts(factor_groups[key])
        reasons += r; risks += k

    tp = compute_target_price(tech, fund)
    rec, decision_note = (
        ("GIỮ", "Chưa đủ dữ liệu ở cả phân tích cơ bản và kỹ thuật để tính điểm tổng 50/50.")
        if total is None
        else _decide(total, tp["upside"], tp.get("upside_range"))
    )
    result = {
        "symbol": tech.get("symbol") or fund.get("symbol"), "recommendation": rec,
        "decision_note": decision_note, "decision_rule": _decision_rule_text(),
        "total_score": total, "scores": scores, "target_price": tp["target_price"],
        "upside": tp["upside"], "target_method": tp["method"], "target_confidence": tp["confidence"],
        "valuation_methods": tp["valuation_methods"], "valuation_summary": tp["valuation_summary"],
        "valuation_reference_pe": tp["valuation_reference_pe"],
        "valuation_current_pe": tp.get("valuation_current_pe"),
        "valuation_history_count": tp["valuation_history_count"],
        "target_price_range": tp.get("target_price_range"),
        "upside_range": tp.get("upside_range"),
        "valuation_pe_low": tp.get("valuation_pe_low"),
        "valuation_pe_high": tp.get("valuation_pe_high"),
        "valuation_eps": tp.get("valuation_eps"),
        "valuation_eps_count": tp.get("valuation_eps_count", 0),
        "valuation_eps_normalized": tp.get("valuation_eps_normalized", False),
        "valuation_eps_basis": tp.get("valuation_eps_basis", "Chưa đủ dữ liệu"),
        "valuation_pb_reference": tp.get("valuation_pb_reference"),
        "valuation_pb_count": tp.get("valuation_pb_count", 0),
        "valuation_pb_target": tp.get("valuation_pb_target"),
        "valuation_warning": tp.get("valuation_warning"),
        "reasons": reasons, "risks": risks,
        "ai_summary": "", "ai_generated": False,
    }
    result["score_breakdown"] = _score_breakdown(factor_groups, scores, total, rec, result["decision_rule"], decision_note, weights)

    facts = build_facts(tech, fund, result)
    if use_ai:
        try:
            text, _ = call_ai(build_prompt(facts))
            result["ai_summary"] = text
            result["ai_generated"] = True
        except Exception as e:
            print(f"[WARN] AI lỗi: {e}", file=sys.stderr)
    if not result["ai_summary"]:
        result["ai_summary"] = fallback_summary(result)
    return result


# ======================= 6. AI: NHẬN ĐỊNH CHUYÊN BIỆT CHO TỪNG TAB & TỔNG QUAN =======================
def build_facts(tech, fund, result):
    tech = tech or {}
    fund = fund or {}
    result = result or {}
    
    # Lấy mã cổ phiếu an toàn từ nhiều nguồn dự phòng
    symbol = (
        result.get("symbol") or 
        result.get("ticker") or 
        tech.get("symbol") or 
        fund.get("symbol") or 
        "N/A"
    )
    
    rec = result.get("recommendation", "N/A")
    score = result.get("total_score")
    score_text = f"{score}/100" if score is not None else "Chưa đủ dữ liệu để tính điểm tổng 50/50"
    note = result.get("decision_note", "N/A")
    scores_dict = result.get("scores", {})
    target_p = result.get("target_price", "N/A")
    upside_val = result.get("upside", "N/A")
    target_m = result.get("target_method", "N/A")
    score_breakdown = result.get("score_breakdown") or {}
    technical_observations = [
        factor["text"]
        for factor in score_breakdown.get("technical_factors", [])
        if factor.get("text")
    ]
    fundamental_factors = score_breakdown.get("fundamental_factors", [])
    fundamental_strengths = [
        factor["label"] for factor in fundamental_factors
        if factor.get("points") is not None and factor["points"] > 0
    ]
    fundamental_constraints = [
        factor["label"] for factor in fundamental_factors
        if factor.get("points") is not None and factor["points"] < 0
    ]
    fundamental_facts = (
        f"ROE: {_p(fund.get('ROE'))}; "
        f"Tăng trưởng EPS: {_p(fund.get('EPS_growth'))}; "
        f"Tăng trưởng doanh thu: {_p(fund.get('revenue_growth'))}; "
        f"Lợi nhuận sau thuế: {_f(fund.get('net_profit'), 1, ' tỷ đồng')} "
        f"(năm {fund.get('net_profit_year', 'N/A')}); "
        f"P/E: {_f(fund.get('PE'), 2)}; P/B: {_f(fund.get('PB'), 2)}; "
        f"Nợ/VCSH: {_f(fund.get('debt_to_equity'), 2)}."
    )

    return f"""Mã cổ phiếu: {symbol}
Khuyến nghị hệ thống: {rec} (Tổng điểm: {score_text})
Trọng số điểm tổng: 50% cơ bản + 50% kỹ thuật; tin tức không tham gia chấm điểm.
Lý do: {note}
Điểm thành phần: {scores_dict}
Số liệu cơ bản: {fundamental_facts}
Giá giữa dải P/E: {target_p} nghìn đồng (Upside trung vị: {upside_val})
Dải giá P/E Q1–Q3: {result.get('target_price_range', 'N/A')}; dải upside: {result.get('upside_range', 'N/A')}
Giá đối chiếu P/B: {result.get('valuation_pb_target', 'N/A')} nghìn đồng
Phương pháp định giá: {target_m}
Yếu tố cơ bản hỗ trợ: {", ".join(fundamental_strengths) or "chưa có yếu tố được xác nhận"}
Yếu tố cơ bản hạn chế: {", ".join(fundamental_constraints) or "chưa có yếu tố bị trừ điểm"}
Tín hiệu kỹ thuật đáng chú ý: {"; ".join(technical_observations) or "chưa đủ dữ liệu chỉ báo"}"""


def build_fundamental_facts(fund, result):
    fund = fund or {}
    factors = (
        (result or {}).get("score_breakdown", {}).get("fundamental_factors", [])
    )
    positive_factors = [
        factor["label"] for factor in factors
        if factor.get("points") is not None and factor["points"] > 0
    ]
    negative_factors = [
        factor["label"] for factor in factors
        if factor.get("points") is not None and factor["points"] < 0
    ]
    contribution_summary = (
        f"Điểm được hỗ trợ bởi {', '.join(positive_factors) or 'chưa có yếu tố nổi bật'}; "
        f"bị hạn chế bởi {', '.join(negative_factors) or 'chưa có yếu tố trừ điểm'}."
    )
    bank_metrics = ""
    if fund.get("valuation_type") == "bank":
        bank_metrics = (
            f"\nChỉ tiêu ngân hàng — NIM: {_p(fund.get('net_interest_margin'))}; "
            f"NPL: {_p(fund.get('npl'))}; bao phủ nợ xấu: "
            f"{_p(fund.get('loan_loss_coverage_ratio'))}; "
            f"CAR: {_p(fund.get('capital_adequacy_ratio'))}."
        )
    return f"""Mã cổ phiếu: {fund.get('symbol', 'N/A')}
Điểm cơ bản: {(result or {}).get('scores', {}).get('fundamental', 'Chưa đủ dữ liệu')}/100
ROE: {_p(fund.get('ROE'))}; ROA: {_p(fund.get('ROA'))}.
Tăng trưởng EPS: {_p(fund.get('EPS_growth'))}; tăng trưởng doanh thu: {_p(fund.get('revenue_growth'))}; tăng trưởng lợi nhuận sau thuế hợp nhất: {_p(fund.get('profit_growth'))}.
Lợi nhuận sau thuế hợp nhất: {_f(fund.get('net_profit'), 1, ' tỷ đồng')} (năm {fund.get('net_profit_year', 'N/A')}); EPS hiện tại: {_f(fund.get('EPS'), 0, ' đồng/cổ phiếu')}.
Biên lợi nhuận gộp: {_p(fund.get('gross_margin'))}; biên lợi nhuận ròng: {_p(fund.get('net_margin'))}.
Nợ vay/VCSH: {_f(fund.get('debt_to_equity'), 2)} lần; thanh toán hiện hành: {_f(fund.get('current_ratio'), 2)} lần.
Dòng tiền kinh doanh: {_f(fund.get('operating_cash_flow'), 1, ' tỷ đồng')}.
{bank_metrics}
Yếu tố ảnh hưởng đến điểm cơ bản: {contribution_summary}"""


def build_valuation_facts(tech, fund, result):
    tech = tech or {}
    fund = fund or {}
    result = result or {}
    eps = fund.get("EPS")
    current_price = tech.get("close")
    current_pe = result.get("valuation_current_pe")
    if current_pe is None and _ok(current_price, eps) and current_price > 0 and eps > 0:
        current_pe = current_price * 1000 / eps
    historical_pe_values = _valid_historical_pe(fund)
    historical_pe = result.get("valuation_reference_pe")
    if historical_pe is None and historical_pe_values:
        historical_pe = statistics.median(historical_pe_values)
    history_count = result.get("valuation_history_count")
    if history_count is None:
        history_count = len(historical_pe_values)
    target_range = result.get("target_price_range")
    range_text = (
        f"{_f(target_range[0], 2)}–{_f(target_range[1], 2)} nghìn đồng/cổ phiếu"
        if target_range
        else "chưa đủ dữ liệu"
    )
    upside_range = result.get("upside_range")
    upside_range_text = (
        f"{_p(upside_range[0])} đến {_p(upside_range[1])}"
        if upside_range
        else "chưa đủ dữ liệu"
    )
    normalized_eps = result.get("valuation_eps")
    normalized_eps_count = result.get("valuation_eps_count", 0)
    eps_basis = result.get("valuation_eps_basis", "chưa đủ dữ liệu")
    pb_reference = result.get("valuation_pb_reference")
    pb_count = result.get("valuation_pb_count", 0)
    pb_target = result.get("valuation_pb_target")
    bvps = fund.get("BVPS")
    eps_formula = (
        "trung vị EPS dương nhiều năm"
        if result.get("valuation_eps_normalized")
        else "EPS gần nhất do chưa đủ dữ liệu lịch sử"
    )

    valuation_type = fund.get("valuation_type", "non-bank")
    if valuation_type == "bank":
        sector_note = (
            "Doanh nghiệp thuộc nhóm ngân hàng; mô hình hiện tại vẫn dùng P/E lịch sử, "
            "nên cần đối chiếu thêm P/B và ROE trước khi kết luận."
        )
    elif valuation_type == "cyclical":
        sector_note = (
            "Doanh nghiệp có tính chu kỳ; EPS tại một thời điểm có thể ở đỉnh hoặc đáy, "
            "nên cần xem xét thêm lợi nhuận qua chu kỳ."
        )
    else:
        sector_note = (
            "Phương pháp đang so với lịch sử của chính doanh nghiệp, không phải P/E nhóm doanh nghiệp cùng ngành. "
            "BVPS kế toán có thể khác giá trị kinh tế thực; với doanh nghiệp bất động sản, "
            "giá trị đất và dự án có thể chưa được phản ánh đầy đủ."
        )

    return f"""Mã cổ phiếu: {tech.get('symbol') or fund.get('symbol', 'N/A')}
Giá hiện tại: {_f(current_price, 2)} nghìn đồng/cổ phiếu.
EPS gần nhất: {_f(eps, 0)} đồng/cổ phiếu.
P/E hiện tại: {_f(current_pe, 2)} lần.
P/E trung vị lịch sử: {_f(historical_pe, 2)} lần, từ {history_count} kỳ hợp lệ.
EPS dùng để định giá: {_f(normalized_eps, 0)} đồng/cổ phiếu ({eps_basis}; {normalized_eps_count} năm EPS dương được tìm thấy).
Dải giá tham khảo P/E Q1–Q3: {range_text}; upside toàn dải: {upside_range_text}.
Giá giữa dải theo P/E trung vị: {_f(result.get('target_price'), 2)} nghìn đồng/cổ phiếu.
P/B trung vị lịch sử: {_f(pb_reference, 2)} lần từ {pb_count} kỳ; BVPS gần nhất: {_f(bvps, 0)} đồng/cổ phiếu; giá P/B đối chiếu: {_f(pb_target, 2)} nghìn đồng/cổ phiếu.
Cách tính: {eps_formula} × Q1–Q3 P/E lịch sử hợp lệ. P/B × BVPS chỉ là phép đối chiếu riêng, không gộp trọng số với P/E.
Giới hạn theo loại hình: {sector_note}
Ghi chú: {result.get('valuation_summary', 'Giá mục tiêu ước tính chỉ là kết quả theo giả định, không đảm bảo thị trường hội tụ về mức giá này.')}"""


def build_technical_facts(tech, result):
    tech = tech or {}
    result = result or {}
    factors = result.get("score_breakdown", {}).get("technical_factors", [])
    factor_summary = "; ".join(
        f"{factor.get('category', 'Khác')} — {factor['label']}: "
        f"{factor['evidence']}; điểm {factor['score']:.0f}/100 "
        f"(trọng số {factor['weight']}%)"
        if factor.get("score") is not None
        else f"{factor.get('category', 'Khác')} — {factor['label']}: thiếu dữ liệu"
        for factor in factors
    )
    cross_labels = {
        "golden_cross": "Golden Cross phiên gần nhất",
        "death_cross": "Death Cross phiên gần nhất",
        "none": "Không giao cắt MA50/MA200 ở phiên gần nhất",
    }
    cross = cross_labels.get(
        tech.get("ma50_ma200_cross"), "Chưa đủ dữ liệu xác định giao cắt"
    )
    breakout = tech.get("breakout_20d")
    breakout_text = (
        "Có vượt đỉnh 20 phiên"
        if breakout is True
        else "Không vượt đỉnh 20 phiên"
        if breakout is False
        else "Chưa đủ dữ liệu xác định breakout"
    )
    return f"""Mã: {tech.get('symbol', 'N/A')}; phiên dữ liệu kỹ thuật: {tech.get('signal_as_of', tech.get('as_of', 'N/A'))}; nguồn: {tech.get('source', 'N/A')}.
Giá đóng cửa của phiên đã hoàn tất: {_f(tech.get('signal_close'), 2)} nghìn đồng; giá realtime (nếu có): {tech.get('current_price', 'N/A')}.
Xu hướng: MA20 {_f(tech.get('MA20'), 2)}, MA50 {_f(tech.get('MA50'), 2)}, MA200 {_f(tech.get('MA200'), 2)} nghìn đồng; {cross}.
Động lượng: RSI(14) {_f(tech.get('RSI'), 2)}; MACD {_f(tech.get('MACD'), 4)}; Signal {_f(tech.get('MACD_signal'), 4)}.
Khối lượng: biến động giá phiên gần nhất {_p(tech.get('price_change_pct'))}; khối lượng/20 phiên trước {_f(tech.get('volume_ratio'), 2)}x; {breakout_text}.
Vùng tham chiếu kỹ thuật 60 phiên: hỗ trợ ước lượng {_f(tech.get('support_60d'), 2)}; cách hỗ trợ {_p(tech.get('support_distance_pct'))}; kháng cự ước lượng {_f(tech.get('resistance_60d'), 2)}; cách kháng cự {_p(tech.get('resistance_distance_pct'))} (nghìn đồng).
Điểm kỹ thuật: {(result.get('scores') or {}).get('technical', 'Chưa đủ dữ liệu')}/100; độ bao phủ chỉ báo: {result.get('score_breakdown', {}).get('technical_coverage', 0)}% trọng số có dữ liệu.
Chi tiết nhóm/chỉ báo: {factor_summary or 'Chưa đủ dữ liệu để tính.'}"""


def build_prompt(facts):
    return f"""Bạn là chuyên viên phân tích tài chính. Hãy đưa ra nhận định tổng quan có tính phân tích, không phải bản đọc lại dữ liệu.

{facts}

Viết đúng 3 đoạn ngắn, mỗi đoạn tập trung vào một kết luận:
1. Tổng hợp hai trụ cột 50/50: giải thích vì sao kết quả cơ bản và kỹ thuật đồng thuận hay mâu thuẫn, và điều đó khiến khuyến nghị thận trọng đến mức nào.
2. Phân tích chất lượng và tính bền vững của hoạt động kinh doanh: diễn giải khả năng sinh lời, tăng trưởng và sức khỏe tài chính thay vì liệt kê chỉ tiêu.
3. Phân tích điểm nghẽn/rủi ro quan trọng nhất từ kỹ thuật và định giá; giải thích chúng có thể làm thay đổi triển vọng ra sao.

QUY TẮC BẮT BUỘC:
- Không chép lại bảng số liệu, không kể lần lượt từng chỉ tiêu, không nhắc lại công thức chấm điểm hay điểm cộng/trừ của từng chỉ tiêu.
- Tối đa 2 con số trong toàn bài; ưu tiên điểm tổng hoặc kết quả định giá khi thật sự cần. Phần còn lại phải phân tích ý nghĩa và mối liên hệ.
- Không nêu chỉ tiêu không áp dụng, dữ liệu N/A hoặc số liệu không có trong đầu vào.
- Phân biệt dữ kiện, suy luận và điều chưa thể kết luận; không bịa nguyên nhân, không hứa hẹn lợi nhuận."""


def build_tab_prompt(tab_type, facts):
    """Xây dựng prompt riêng biệt cho từng Tab hiển thị trên giao diện."""
    if tab_type == "technical":
        return f"""Bạn là chuyên gia phân tích kỹ thuật chứng khoán. Dựa trên dữ liệu sau:
{facts}
Hãy trình bày đúng 4 gạch đầu dòng ngắn bằng tiếng Việt:
- Tổng quan điểm số, độ bao phủ và xu hướng MA.
- Động lượng RSI và MACD.
- Diễn biến giá, khối lượng và breakout.
- Vùng hỗ trợ/kháng cự tham chiếu cùng rủi ro chính.
Mỗi gạch đầu dòng tối đa 2 câu. Nêu cả tín hiệu thuận và rủi ro; phân biệt dữ liệu thiếu với tín hiệu trung tính. Chỉ dùng số liệu đã cho, không suy diễn dòng tiền tổ chức, không khẳng định chắc chắn xu hướng tương lai hay đưa ra khuyến nghị mua/bán. Vùng 60 phiên chỉ là tham chiếu đơn giản, không phải vùng hỗ trợ/kháng cự đã được xác nhận."""
    elif tab_type == "fundamental":
        return f"""Bạn là chuyên gia phân tích cơ bản cổ phiếu. Dựa trên dữ liệu sau:
{facts}
Viết 4-5 câu tiếng Việt, ngắn gọn nhưng có lập luận. Không đọc lại bảng số và không lần lượt nhắc ROE, ROA, EPS, doanh thu, nợ, thanh khoản, dòng tiền. Hãy tổng hợp chúng để trả lời: doanh nghiệp đang sinh lời/tăng trưởng ra sao; chất lượng tăng trưởng có đi cùng sức khỏe tài chính hay không; điểm mạnh nào có ý nghĩa nhất và điểm yếu/dữ liệu thiếu nào hạn chế kết luận. Có thể dẫn tối đa 2 con số toàn bài, chỉ khi cần làm bằng chứng cho kết luận. Không lặp điểm cộng/trừ hoặc công thức chấm điểm. Chỉ phân tích NIM, NPL, bao phủ nợ xấu và CAR nếu đầu vào có dữ liệu ngân hàng; bỏ qua nếu không áp dụng. Không suy đoán nguyên nhân hoặc tính bền vững vượt quá dữ kiện. Phần định giá P/E, P/B thuộc tab Định giá nên không đánh giá ở đây."""
    elif tab_type == "valuation":
        return f"""Bạn là chuyên gia định giá cổ phiếu. Dựa trên dữ liệu sau:
{facts}
Hãy viết nhận xét diễn giải bằng tiếng Việt, gồm 4 gạch đầu dòng ngắn:
- So sánh P/E hiện tại với trung vị lịch sử nếu có đủ dữ liệu; giải thích điều đó có thể hàm ý gì.
- Dải giá tham khảo P/E Q1–Q3 và upside toàn dải cho biết điều gì; nhấn mạnh đây không phải biên an toàn chắc chắn.
- Đánh giá giả định dùng trung vị EPS dương nhiều năm; giải thích rủi ro khi lợi nhuận biến động hoặc khác chu kỳ.
- Nêu giá đối chiếu P/B × BVPS riêng, không gộp với P/E; nhắc giới hạn dữ liệu/loại hình doanh nghiệp.
Mỗi ý chỉ trình bày một lần, không lặp lại cùng một cảnh báo ở nhiều gạch đầu dòng. Không chỉ nhắc lại số liệu: hãy giải thích ý nghĩa, nêu cả khả năng tích cực lẫn rủi ro, không tự tạo số liệu hoặc so sánh peer, không khẳng định cổ phiếu rẻ/đắt chắc chắn và không đưa khuyến nghị mua/bán. Nếu EPS hoặc P/E chưa đủ lịch sử tối thiểu, hãy nêu số kỳ quan sát và nói rõ hạn chế. P/B là đối chiếu độc lập, không phải giá mục tiêu đã được gộp với P/E. Nếu không có số liệu, hãy nói rõ chưa đủ cơ sở kết luận."""
    else:
        return build_prompt(facts)



import streamlit as st

GEMINI_CANDIDATE_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
)


@st.cache_data(ttl=3600)  # Lưu kết quả AI trong 1 giờ, không phải gọi lại API khi bấm nút
def call_ai_for_tab(tab_type, tech, fund, result):
    if tab_type == "fundamental":
        facts = build_fundamental_facts(fund, result)
    elif tab_type == "technical":
        facts = build_technical_facts(tech, result)
    elif tab_type == "valuation":
        facts = build_valuation_facts(tech, fund, result)
    else:
        facts = build_facts(tech, fund, result)
    prompt = build_tab_prompt(tab_type, facts)
    try:
        text = _gemini(prompt)
        return text
    except Exception as e:
        return f"[⚠️ Chưa gọi được AI Gemini]: {str(e)}"


def call_ai_for_tabs(analyses):
    """Generate all dashboard commentaries in one Gemini request."""
    if not analyses:
        return {}

    try:
        return _cached_ai_for_tabs(analyses)
    except Exception as exc:
        error = f"[⚠️ Chưa gọi được AI Gemini]: {exc}"
        return {tab_type: error for tab_type in analyses}


@st.cache_data(ttl=3600)
def _cached_ai_for_tabs(analyses):
    sections = []
    valid_tabs = ("general", "technical", "fundamental", "valuation")
    for tab_type in valid_tabs:
        analysis = analyses.get(tab_type)
        if analysis is None:
            continue
        tech = analysis.get("tech") or {}
        fund = analysis.get("fund") or {}
        result = analysis.get("result") or {}
        if tab_type == "fundamental":
            facts = build_fundamental_facts(fund, result)
        elif tab_type == "technical":
            facts = build_technical_facts(tech, result)
        elif tab_type == "valuation":
            facts = build_valuation_facts(tech, fund, result)
        else:
            facts = build_facts(tech, fund, result)
        sections.append(
            f"<<<{tab_type.upper()}>>>\n"
            f"{build_tab_prompt(tab_type, facts)}\n"
            f"<<<END_{tab_type.upper()}>>>"
        )

    if not sections:
        return {}

    prompt = (
        "Tạo nhận xét cho từng phần bên dưới trong cùng một lượt. "
        "Tuân thủ riêng yêu cầu và định dạng của từng phần, không trộn dữ liệu "
        "giữa các phần. Chỉ trả về đúng các khối có marker tương ứng, không "
        "thêm lời dẫn hay markdown fence.\n\n"
        + "\n\n".join(sections)
        + "\n\nĐịnh dạng đầu ra bắt buộc:\n"
        + "\n".join(
            f"<<<{tab_type.upper()}>>>\n(nội dung)\n"
            f"<<<END_{tab_type.upper()}>>>"
            for tab_type in valid_tabs
            if tab_type in analyses
        )
    )
    response = _gemini(prompt)
    commentaries = {}
    missing_tabs = []
    for tab_type in valid_tabs:
        if tab_type not in analyses:
            continue
        match = re.search(
            rf"<<<{tab_type.upper()}>>>(.*?)"
            rf"<<<END_{tab_type.upper()}>>>",
            response,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not match or not match.group(1).strip():
            missing_tabs.append(tab_type)
            continue
        commentaries[tab_type] = match.group(1).strip()

    for tab_type in missing_tabs:
        analysis = analyses[tab_type]
        try:
            commentaries[tab_type] = call_ai_for_tab(
                tab_type,
                analysis.get("tech") or {},
                analysis.get("fund") or {},
                analysis.get("result") or {},
            )
        except Exception as exc:
            commentaries[tab_type] = (
                f"[⚠️ Chưa gọi được AI Gemini cho phần {tab_type}: {exc}]"
            )
    return commentaries


def _gemini(prompt):
    from google import genai
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    last_err = None
    for model_name in GEMINI_CANDIDATE_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                return response.text.strip()
            last_err = RuntimeError(
                f"Gemini model {model_name} returned no text."
            )
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Lỗi gọi Gemini SDK: {last_err}")
def _anthropic(prompt):
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    m = client.messages.create(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-3-5"), max_tokens=900, messages=[{"role": "user", "content": prompt}])
    return m.content[0].text.strip()


def _openai(prompt):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    r = client.chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content.strip()


PROVIDERS = [("gemini", "GEMINI_API_KEY", _gemini), ("anthropic", "ANTHROPIC_API_KEY", _anthropic), ("openai", "OPENAI_API_KEY", _openai)]


def call_ai(prompt):
    errors = []
    for name, env, fn in PROVIDERS:
        if not os.getenv(env): continue
        try:
            return fn(prompt), name
        except Exception as e:
            errors.append(f"{name}: {str(e)[:100]}")
    raise RuntimeError(" | ".join(errors) if errors else "Chưa đặt API key AI nào.")


def fallback_summary(r):
    r = r or {}
    symbol = r.get("symbol") or r.get("ticker") or "HPG"
    # Lấy tổng điểm chuẩn xác từ total_score hoặc score_breakdown
    total_score = r.get("total_score")
    if total_score is None:
        total_score = r.get("score_breakdown", {}).get("total_score")
        
    rec = r.get("recommendation", "GIỮ")
    note = r.get("decision_note", "")
    target_p = _price_vnd(r.get("target_price"))
    
    upside_val = r.get("upside")
    if isinstance(upside_val, (int, float)):
        upside_p = f"{upside_val * 100:+.1f}%"
    else:
        upside_p = str(upside_val or "N/A")

    reasons = r.get("reasons") or r.get("investment_points") or []
    risks = r.get("risks") or r.get("key_risks") or []

    score_text = f"{total_score}/100 điểm định lượng" if total_score is not None else "chưa đủ dữ liệu để tính điểm tổng 50/50"
    p1 = f"Cổ phiếu {symbol} {score_text}, ứng với khuyến nghị {rec}. {note}"
    if r.get("target_price") is not None:
        price_range = r.get("target_price_range")
        range_upside = r.get("upside_range")
        if price_range:
            p1 += (
                f" Dải giá tham khảo P/E là "
                f"{_price_vnd(price_range[0])}–{_price_vnd(price_range[1])} "
                f"với upside {range_upside[0]:+.1%} đến {range_upside[1]:+.1%}."
            )
        else:
            p1 += f" Giá tham khảo theo mô hình là {target_p} với upside {upside_p}."

    p2 = f"Các luận điểm tích cực gồm: {'; '.join(reasons) if reasons else 'Chưa có luận điểm tích cực đủ dữ liệu xác nhận'}."
    risk_text = (
        "; ".join(risks)
        if risks
        else "Chưa có cảnh báo cụ thể từ các chỉ báo hiện có; điều này không đồng nghĩa không có rủi ro"
    )
    p3 = f"Các rủi ro cần lưu ý: {risk_text}."

    return "\n\n".join([p1, p2, p3])
def to_report_context(rec, tech, fund, company_name="", exchange="", industry=""):
    fund = normalize_fund(fund)
    sc = rec["scores"]
    labels = {"fundamental": "cơ bản", "technical": "kỹ thuật"}
    score_details = ", ".join(
        f"{labels[k]} {s}/100" if s is not None else f"{labels[k]} chưa đủ dữ liệu"
        for k, s in sc.items())
    
    if rec["target_price"] is not None:
        target_note = (f"Dải giá tham khảo P/E tính theo: {rec['target_method']} "
                       f"(độ tin cậy {rec.get('target_confidence') or 'N/A'}).")
    else:
        target_note = str(rec.get("target_method", "")) + "."
        
    total_score_text = (
        f"{rec['total_score']}/100 điểm"
        if rec.get("total_score") is not None
        else "chưa đủ dữ liệu để tính điểm tổng 50/50"
    )
    thesis = (f"Hệ thống chấm {total_score_text} ({score_details}), tương ứng "
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
        "ticker": rec.get("symbol") or tech.get("symbol") or fund.get("symbol"),
        "company_name": company_name or rec.get("symbol") or tech.get("symbol") or fund.get("symbol"),
        "report_date": datetime.now().strftime("%d/%m/%Y"),
        "recommendation": rec["recommendation"],
        "total_score": rec.get("total_score"),
        "scoring_weights": get_dynamic_weights(),
        "target_price": _price_vnd(rec["target_price"]),
        "target_price_range": (
            f"{_price_vnd(rec['target_price_range'][0])} – "
            f"{_price_vnd(rec['target_price_range'][1])}"
            if rec.get("target_price_range")
            else "N/A"
        ),
        "target_price_range_values": (
            [
                rec["target_price_range"][0] * 1000,
                rec["target_price_range"][1] * 1000,
            ]
            if rec.get("target_price_range")
            else None
        ),
        "upside": "N/A" if rec["upside"] is None else f"{rec['upside'] * 100:+.1f}%",
        "upside_range": (
            f"{rec['upside_range'][0] * 100:+.1f}% đến "
            f"{rec['upside_range'][1] * 100:+.1f}%"
            if rec.get("upside_range")
            else "N/A"
        ),
        "target_method": rec.get("target_method", "P/E trung vị lịch sử"),  # KHÓA ĐÃ ĐƯỢC BỔ SUNG
        "target_confidence": rec.get("target_confidence") or "Chưa xác định",
        "valuation_reference_pe": rec.get("valuation_reference_pe"),
        "valuation_current_pe": rec.get("valuation_current_pe"),
        "valuation_history_count": rec.get("valuation_history_count", 0),
        "valuation_pe_low": rec.get("valuation_pe_low"),
        "valuation_pe_high": rec.get("valuation_pe_high"),
        "valuation_eps": rec.get("valuation_eps"),
        "valuation_eps_count": rec.get("valuation_eps_count", 0),
        "valuation_eps_normalized": rec.get("valuation_eps_normalized", False),
        "valuation_eps_basis": rec.get("valuation_eps_basis", "Chưa đủ dữ liệu"),
        "valuation_pb_reference": rec.get("valuation_pb_reference"),
        "valuation_pb_count": rec.get("valuation_pb_count", 0),
        "valuation_pb_target": (
            _price_vnd(rec.get("valuation_pb_target"))
            if rec.get("valuation_pb_target") is not None
            else "N/A"
        ),
        "valuation_methods": [
            {"method_name": item["method_name"],
             "fair_value": (
                 f"{_price_vnd(item['low'])} – {_price_vnd(item['high'])}"
                 if item.get("low") is not None and item.get("high") is not None
                 else _price_vnd(item["value"])
                 if item.get("value") is not None
                 else "Chưa đủ dữ liệu"
             ),
             "role": item.get("role", "Tham khảo"),
             "note": item.get("note", "")}
            for item in rec.get("valuation_methods", [])],
        "valuation_summary": rec.get("valuation_summary", ""),
        "valuation_warning": rec.get("valuation_warning"),
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
        "ai_summary": rec.get("ai_summary", ""),
        "ai_generated": rec.get("ai_generated", False),
        "scores": rec.get("scores", {}),
        "score_breakdown": rec.get("score_breakdown", {}),
        "decision_note": rec.get("decision_note", ""),
        "decision_rule": rec.get("decision_rule", ""),
        "technical_score": rec.get("scores", {}).get("technical"),
        "fundamental_score": rec.get("scores", {}).get("fundamental"),
        "technical_coverage": rec.get("score_breakdown", {}).get(
            "technical_coverage", 0
        ),
        "investment_points": rec.get("reasons", []),
        "key_risks": rec.get("risks", []),
        "data_sources": sources,
        "data_as_of": tech.get("as_of") or fund.get("as_of") or "N/A",
        "analyst_name": "Nhóm phân tích",
        "analyst_contact": "",
        "disclaimer": "",
    }