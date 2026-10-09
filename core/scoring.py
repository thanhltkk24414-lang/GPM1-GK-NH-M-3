"""
scoring.py - Logic Engine (TV3 - Phụng)  [phiên bản 2]
Nhận dữ liệu từ TV1 (tech, news) và TV2 (fund) -> khuyến nghị MUA / GIỮ / BÁN
+ giá mục tiêu + nhận định AI + dữ liệu điền vào report_template.html.

Chạy thử:  py core/scoring.py            (đọc data/sample_FPT.json)
"""
import json
import os
import re
import sys
from datetime import datetime

# ======================= 1. CẤU HÌNH (chỉnh ở đây) =======================
WEIGHTS = {"fundamental": 0.40, "technical": 0.35, "news": 0.25}
BUY_THRESHOLD = 65    # total >= 65 -> MUA
SELL_THRESHOLD = 45   # total <  45 -> BÁN, còn lại GIỮ

POSITIVE_WORDS = ["tăng trưởng", "lợi nhuận tăng", "vượt kế hoạch", "kỷ lục", "cổ tức",
                  "mua vào", "nâng dự báo", "khởi sắc", "hợp đồng", "mở rộng", "lãi"]
NEGATIVE_WORDS = ["giảm", "lỗ", "sụt giảm", "bán ròng", "vi phạm", "bị phạt", "thanh tra",
                  "đình chỉ", "nợ xấu", "hạ dự báo", "khó khăn", "kiện", "cảnh báo"]


# ======================= 2. HÀM TIỆN ÍCH =======================
def _clamp(x):
    return max(0, min(100, round(x)))


def _ok(*vals):
    """True nếu tất cả giá trị đều có (không None)."""
    return all(v is not None for v in vals)


def _f(x, nd=1, suffix=""):
    return "N/A" if x is None else f"{x:,.{nd}f}{suffix}"


def _p(x, nd=1):
    return "N/A" if x is None else f"{x * 100:.{nd}f}%"


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
            reasons.append(f"Giá {close} nằm trên MA50 ({ma50}): xu hướng trung hạn tích cực")
        else:
            s -= 10
            risks.append(f"Giá {close} nằm dưới MA50 ({ma50}): xu hướng trung hạn yếu")
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


def compute_target_price(tech, fund):
    """Định giá tương đối đơn giản: giá mục tiêu = giá hiện tại x (P/E ngành / P/E của mã).
    Trả về (target_price, upside) hoặc (None, None) nếu thiếu dữ liệu hoặc P/E <= 0."""
    close, pe, pe_n = tech.get("close"), fund.get("PE"), fund.get("PE_nganh")
    if not _ok(close, pe, pe_n) or pe <= 0 or pe_n <= 0:
        return None, None
    target = close * pe_n / pe
    return round(target, 1), round(target / close - 1, 4)


# ======================= 4. TỔNG HỢP =======================
def get_recommendation(tech, fund, news, use_ai=True):
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
    target_price, upside = compute_target_price(tech, fund)

    result = {
        "symbol": tech.get("symbol") or fund.get("symbol"),
        "recommendation": rec,
        "total_score": total,
        "scores": scores,
        "target_price": target_price,   # nghìn đồng, None nếu thiếu dữ liệu
        "upside": upside,               # 0.26 = +26%, None nếu thiếu dữ liệu
        "reasons": reasons,
        "risks": risks or ["Chưa phát hiện rủi ro nổi bật theo bộ quy tắc hiện tại"],
        "ai_summary": "",
        "ai_generated": False,
        "ai_provider": None,
        "ai_unverified_numbers": [],
    }

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
        f"Giá mục tiêu (nghìn đồng): {result['target_price']}, tiềm năng: {result['upside']}",
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
- Viết tiếng Việt, văn phong chuyên nghiệp, trung lập như báo cáo CTCK.
- Độ dài 120-180 từ, gồm 3 đoạn ngắn: (1) bức tranh chung và khuyến nghị, (2) luận điểm chính về cơ bản và kỹ thuật, (3) rủi ro cần theo dõi.
- CHỈ sử dụng các con số có trong dữ liệu ở trên. Tuyệt đối không bịa thêm số liệu, giá mục tiêu hay dự báo.
- Không hứa hẹn lợi nhuận, không dùng từ "chắc chắn", "đảm bảo".
- Khuyến nghị phải đúng với kết quả của hệ thống, không tự đổi.
- Không dùng markdown, không dùng tiêu đề, chỉ văn bản thường."""


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
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=800,
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


def check_numbers(text, facts):
    """Trả về các số trong văn bản AI mà KHÔNG xuất hiện trong dữ liệu đầu vào (nghi bịa số)."""
    def nums(s):
        return {x.replace(",", ".").rstrip(".") for x in re.findall(r"\d+(?:[.,]\d+)?", s)}
    allowed = nums(facts)
    allowed |= {f"{float(x) * 100:g}" for x in allowed if _isnum(x)}  # 0.21 -> 21
    return sorted(n for n in nums(text) if n not in allowed)


def _isnum(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


def fallback_summary(r):
    """Văn bản dự phòng khi API lỗi: ghép từ kết quả rule engine."""
    p1 = (f"Theo bộ tiêu chí định lượng, cổ phiếu {r['symbol']} đạt {r['total_score']}/100 điểm, "
          f"tương ứng khuyến nghị {r['recommendation']}.")
    p2 = ("Các yếu tố ủng hộ: " + "; ".join(r["reasons"]) + ".") if r["reasons"] else \
         "Hiện chưa có yếu tố ủng hộ nổi bật."
    p3 = "Rủi ro cần theo dõi: " + "; ".join(r["risks"]) + "."
    return "\n\n".join([p1, p2, p3])


# ======================= 6. DỮ LIỆU ĐIỀN VÀO TEMPLATE PDF =======================
def to_report_context(rec, tech, fund, company_name="", exchange="", industry=""):
    """Đổi kết quả sang đúng tên biến trong core/report_template.html (TV4, TV5 dùng).
    Truyền dict này vào template.render(**context)."""
    sc = rec["scores"]
    thesis = (f"Hệ thống chấm {rec['total_score']}/100 điểm "
              f"(cơ bản {sc['fundamental']}, kỹ thuật {sc['technical']}, tin tức {sc['news']}), "
              f"tương ứng khuyến nghị {rec['recommendation']}. "
              f"Giá mục tiêu được tính theo phương pháp P/E ngành.")
    price_range = "N/A"
    if _ok(tech.get("low_52w"), tech.get("high_52w")):
        price_range = f"{tech['low_52w']:,.1f} - {tech['high_52w']:,.1f}"
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
        {"title": "Cấu trúc vốn", "rows": [row("Nợ/Vốn chủ sở hữu (lần)", _f(fund.get("debt_to_equity")))]},
    ]
    return {
        "ticker": rec["symbol"],
        "company_name": company_name or rec["symbol"],
        "report_date": datetime.now().strftime("%d/%m/%Y"),
        "recommendation": rec["recommendation"],
        "target_price": _f(rec["target_price"]),
        "upside": "N/A" if rec["upside"] is None else f"{rec['upside'] * 100:+.1f}%",
        "exchange": exchange or "N/A",
        "industry": industry or "N/A",
        "current_price": _f(tech.get("close")),
        "investment_horizon": "12 tháng",
        "price_chart": tech.get("chart_path") or "",
        "price_source": tech.get("source") or "",
        "market_cap": _f(fund.get("market_cap"), 0),
        "shares_outstanding": _f(fund.get("shares_outstanding"), 0),
        "avg_volume_20d": _f(tech.get("avg_volume_20d"), 0),
        "price_52w_range": price_range,
        "foreign_ownership": _p(fund.get("foreign_ownership")),
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
        "analyst_name": "Nhóm phân tích",   # sửa thành tên nhóm / tên thành viên nếu muốn
        "analyst_contact": "",
        "disclaimer": "",                   # để trống: template dùng đoạn miễn trừ trách nhiệm mặc định
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