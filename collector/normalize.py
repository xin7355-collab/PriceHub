"""商品正規化與跨平台指紋

這是整個系統最難、也最有價值的一層。爬蟲只是把資料搬進來，
真正決定「這是不是一個比價系統」的，是能不能判斷
  蝦皮的「【現貨免運】Logitech 羅技 MX Master 3S 無線滑鼠 石墨灰 台灣公司貨」
  PChome 的「Logitech MX Master 3S 靜音無線滑鼠-石墨灰」
是同一件商品。

v1 刻意只用規則，不上 AI：
  1. 規則可解釋、可單元測試、零成本、在 Actions 上零延遲
  2. 先把錯誤案例累積起來，未來才知道 AI 要解什麼問題
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# ---------------------------------------------------------------- 雜訊字典
# 促銷詞：出現在標題裡但與「是哪個商品」無關的字串。
NOISE_PATTERNS = [
    r"【[^】]*】", r"\[[^\]]*\]", r"（[^）]*現貨[^）]*）",
    r"免運", r"現貨", r"預購", r"下殺", r"限時", r"特價", r"福利品",
    r"買一送一", r"任選", r"滿額", r"贈品?", r"加購", r"組合價",
    r"公司貨", r"平行輸入", r"原廠", r"正品", r"保固\d*[年月]?",
    r"最低價", r"熱賣", r"爆款", r"新品", r"官方", r"旗艦店",
    r"分期0利率", r"\d+期0利率", r"免利率",
    r"快速出貨", r"當日出貨", r"24h", r"24小時",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

# 品牌別名 → 正規化品牌鍵。中英混寫是台灣電商標題的常態。
BRAND_ALIASES = {
    "logitech": "logitech", "羅技": "logitech",
    "apple": "apple", "蘋果": "apple",
    "samsung": "samsung", "三星": "samsung",
    "sony": "sony", "索尼": "sony",
    "asus": "asus", "華碩": "asus",
    "acer": "acer", "宏碁": "acer",
    "msi": "msi", "微星": "msi",
    "hp": "hp", "惠普": "hp",
    "dell": "dell", "戴爾": "dell",
    "lenovo": "lenovo", "聯想": "lenovo",
    "anker": "anker",
    "bose": "bose",
    "philips": "philips", "飛利浦": "philips",
    "panasonic": "panasonic", "國際牌": "panasonic",
    "dyson": "dyson",
    "xiaomi": "xiaomi", "小米": "xiaomi", "米家": "xiaomi",
    "razer": "razer", "雷蛇": "razer",
    "steelseries": "steelseries", "賽睿": "steelseries",
    "kingston": "kingston", "金士頓": "kingston",
    "seagate": "seagate", "希捷": "seagate",
    "wd": "wd", "western digital": "wd", "威騰": "wd",
    "nintendo": "nintendo", "任天堂": "nintendo",
    "garmin": "garmin",
    "jbl": "jbl",
}
# 依長度排序，避免 "wd" 先吃掉 "western digital"
_BRAND_KEYS = sorted(BRAND_ALIASES, key=len, reverse=True)

# 型號：至少含一個數字，允許英數與連字號。
# 尾碼可為空 —— 否則 DDR5、XM5 這類「字母結尾接數字」的型號會整個抓不到。
MODEL_RE = re.compile(r"\b[A-Za-z]{0,8}-?\d[A-Za-z0-9\-]{0,20}\b")
MODEL_MIN_LEN = 2

# 純數字或單位詞不算型號（會誤抓「16GB」「2入」這種規格詞）
UNIT_ONLY_RE = re.compile(
    r"^\d+(gb|tb|mb|ml|cm|mm|kg|g|w|v|hz|吋|入|件|包|組|支|台)$", re.IGNORECASE
)

# 顏色詞：不是型號，但足以區分不同 SKU，單獨抽出來當次要鍵。
COLOR_WORDS = [
    "石墨灰", "太空灰", "曜石黑", "午夜色", "星光色", "銀色", "金色", "玫瑰金",
    "黑色", "白色", "灰色", "藍色", "紅色", "綠色", "紫色", "粉色", "米色",
    "black", "white", "silver", "gold", "blue", "red", "grey", "gray",
]


def _fullwidth_to_half(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def clean_title(raw: str) -> str:
    """剝除促銷雜訊，回傳可比對的乾淨標題。"""
    if not raw:
        return ""
    t = _fullwidth_to_half(raw)
    t = re.sub(r"<[^>]+>", " ", t)          # PChome 回傳有 <b> 標記
    t = NOISE_RE.sub(" ", t)
    t = re.sub(r"[，。、！？!?,;；|｜/／\\+*~‧·•]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -_")
    return t.strip()


def extract_brand(title: str) -> str | None:
    low = title.lower()
    for key in _BRAND_KEYS:
        if key in low:
            return BRAND_ALIASES[key]
    return None


def extract_model(title: str) -> str | None:
    """抽出最像型號的字串。

    策略：候選中選「英文字母 + 數字混合、且最長」的那個。
    純數字（年份、容量）排在最後，因為它們區分力最弱。
    """
    candidates = []
    for m in MODEL_RE.finditer(title):
        tok = m.group(0)
        if len(tok) < MODEL_MIN_LEN or UNIT_ONLY_RE.match(tok):
            continue
        if tok.isdigit() and len(tok) <= 4:      # 2024、512 這種
            continue
        has_alpha = any(c.isalpha() for c in tok)
        candidates.append((has_alpha, len(tok), tok))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2].lower().strip("-")


def extract_color(title: str) -> str | None:
    low = title.lower()
    for c in COLOR_WORDS:
        if c.lower() in low:
            return c.lower()
    return None


def tokens(title: str) -> set[str]:
    """粗分詞：英數以空白切，中文以 2-gram 切。無需外部斷詞套件。"""
    t = clean_title(title).lower()
    out: set[str] = set()
    for chunk in re.findall(r"[a-z0-9\-]+", t):
        if len(chunk) >= 2:
            out.add(chunk)
    han = re.findall(r"[\u4e00-\u9fff]+", t)
    for run in han:
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def latin_content_tokens(title: str, brand: str | None = None) -> set[str]:
    """只取英數詞，並剔除品牌別名本身。

    品牌在不同平台可能寫在【】內（會被清理掉）或直接寫在標題中，
    留著它會讓同商品的 token 集合不一致。
    """
    drop = {k for k, v in BRAND_ALIASES.items() if brand and v == brand}
    out = set()
    for tok in re.findall(r"[a-z0-9\-]+", clean_title(title).lower()):
        if len(tok) >= 2 and tok not in drop:
            out.add(tok)
    return out


def similarity(a: str, b: str) -> float:
    """Jaccard 相似度。0.0 - 1.0。"""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fingerprint(title: str) -> tuple[str, dict]:
    """產生跨平台商品指紋。

    回傳 (fp, meta)。fp 是 12 碼十六進位字串。

    分三級，強度遞減：
      brand+model+color  → 最可信，不同平台只要都寫了型號就能對上
      brand+model        → 可信
      正規化標題雜湊      → 幾乎對不上跨平台，但至少能追蹤同一平台的歷史價
    """
    cleaned = clean_title(title)
    # 品牌必須從「原始」標題抽取。momo 的格式是【SONY 索尼】WH-1000XM5…，
    # 而 clean_title 會整段剝除【】—— 在清理後的字串上找品牌會靜默失敗，
    # 導致同一件商品在不同平台產生不同指紋。
    brand = extract_brand(_fullwidth_to_half(title))
    model = extract_model(cleaned) or extract_model(_fullwidth_to_half(title))
    color = extract_color(cleaned) or extract_color(_fullwidth_to_half(title))

    if brand and model:
        key = f"{brand}|{model}|{color or ''}"
        level = "strong" if color else "medium"
    elif model:
        key = f"?|{model}|{color or ''}"
        level = "medium"
    elif brand:
        # 有品牌但抓不到型號（AirPods Pro、Dyson Airwrap 這類純字母命名）。
        # 只取英數詞並剔除品牌本身 —— 中文描述在各平台差異太大，
        # 納入反而會把同一件商品拆成好幾個指紋。
        key = f"{brand}|~|" + " ".join(sorted(latin_content_tokens(cleaned, brand)))
        level = "loose"
    else:
        key = "t|" + " ".join(sorted(tokens(cleaned)))
        level = "weak"

    fp = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return fp, {
        "brand": brand,
        "model": model,
        "color": color,
        "level": level,
        "clean": cleaned,
    }
