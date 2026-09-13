"""共用工具：字串正規化、日期處理。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# pandas 缺值 str() 之後的樣子。用大小寫敏感比對，免得誤殺 "Nan"（法文人名）
# 這種正當值。
_NA_REPRS = frozenset({"nan", "NaT", "<NA>", "None"})


def is_missing(value) -> bool:
    """這個值是不是「缺值」——涵蓋 None、float NaN、pd.NaT、pd.NA。

    不直接 import pandas：scraper 只有 JobSpy 那條路徑會碰到 pandas 型別，
    其他來源是純 requests。用行為判斷而不是型別判斷。
    """
    if value is None:
        return True
    try:
        if value != value:      # NaN 與 NaT 都不等於自己
            return True
    except TypeError:
        return True             # pd.NA 的比較結果無法轉 bool → 視為缺值
    return False


def clean_str(value) -> str:
    """把來源給的值轉成乾淨字串。

    JobSpy 回的是 pandas DataFrame，缺值是 float('nan')／pd.NaT／pd.NA——
    而這些都是 truthy，所以 `row.get("x") or ""` 這個慣用寫法完全擋不住
    它們：NaN 會一路傳到 .strip() 炸掉（實際發生過，排程連兩天失敗），
    或被 str() 變成 "nan"／"NaT"／"<NA>" 混進關鍵字比對與 dedupe key。
    所有來源的文字欄位都該過這個函式。
    """
    if is_missing(value):
        return ""
    s = str(value).strip()
    return "" if s in _NA_REPRS else s


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def norm(text: str | None) -> str:
    """小寫、去重音、壓空白 — 所有關鍵字比對前都先過這個。"""
    text = clean_str(text)
    if not text:
        return ""
    text = strip_accents(text).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def norm_title_for_dedupe(title: str) -> str:
    """去重用職稱正規化：移除 (H/F)、F/H、M/W 等性別標記與標點。"""
    t = norm(title)
    t = re.sub(r"\(?\b[hfmw]\s*/\s*[hfmw](\s*/\s*[dx])?\b\)?", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def job_id(company: str, title: str) -> str:
    key = f"{norm(company)}|{norm_title_for_dedupe(title)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


PARIS = ZoneInfo("Europe/Paris")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paris_today() -> str:
    """站上所有日期都以巴黎當地日為準（職缺公告日本來就是法國當地日期）。"""
    return datetime.now(PARIS).strftime("%Y-%m-%d")


def to_date_str(value) -> str | None:
    """把各來源的日期格式統一成 YYYY-MM-DD。接受 date/datetime/timestamp/字串。"""
    # 缺值要先擋：pd.NaT 本身就是 datetime 的實例，會走進下面的分支，
    # 而 NaTType.strftime() 直接拋 ValueError。classify() 會呼叫這個函式，
    # 所以每個來源都碰得到。
    if is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None
