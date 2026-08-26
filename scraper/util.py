"""共用工具：字串正規化、日期處理。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def norm(text: str | None) -> str:
    """小寫、去重音、壓空白 — 所有關鍵字比對前都先過這個。"""
    if not text:
        return ""
    text = strip_accents(str(text)).lower()
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
    if value is None:
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
