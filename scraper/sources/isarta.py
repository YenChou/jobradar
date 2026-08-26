"""Isarta France — 行銷/傳播職缺板，傳統 CGI 列表頁。

列表頁：https://isarta.fr/cgi-bin/emplois/jobs?cat=marketing
一次回傳整個分類（實測約 90 筆，站上沒有分頁連結），每筆是一個 <tr>，
欄位都有語意 class（poste-/compagnie-/lieu-listing-monopage），直接取即可。

詳情頁 https://isarta.fr/?job=<ID> 的內文是 JS（monopage.showDetail）載入的，
用 requests 只會拿到站台骨架，所以不抓詳情頁——分類改以職稱為準。
"""
from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("chasse.isarta")

LIST_URL = "https://isarta.fr/cgi-bin/emplois/jobs"
JOB_LINK = re.compile(r"\?job=(\d+)")
HEADERS = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
DATE_PAT = re.compile(r"Publi[ée]e?\s*:?\s*(\d{2})/(\d{2})/(\d{4})")
CONTRACT_PAT = re.compile(r"\b(CDI|CDD|Temporaire|Int[ée]rim|Permanent|Pigiste)\b", re.I)


def fetch(known_urls: set[str] | None = None) -> list[dict]:
    # known_urls 目前用不到（不抓詳情頁），保留參數讓 main.py 的註冊方式一致
    try:
        r = requests.get(LIST_URL, params={"cat": "marketing"}, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.warning("Isarta 列表失敗: %s", e)
        return []

    jobs: list[dict] = []
    seen: set[str] = set()
    for row in BeautifulSoup(r.text, "html.parser").find_all("tr"):
        a = row.find("a", href=JOB_LINK)
        if not a:
            continue
        m = JOB_LINK.search(a.get("href", ""))
        if not m or m.group(1) in seen:
            continue

        title = _text(row, "h2", "poste-listing-monopage")
        if len(title) < 4:
            continue
        seen.add(m.group(1))
        row_text = row.get_text(" ", strip=True)

        jobs.append({
            "title": title,
            "company": _text(row, "h3", "compagnie-listing-monopage"),
            "location": _location(row),
            "description": "",  # 詳情頁需要 JS，拿不到；分類以職稱為準
            "url": f"https://isarta.fr/?job={m.group(1)}",
            "source": "Isarta",
            "date_posted": _date(row_text),
            "contract": _contract(row),
            "work_mode": "remote" if re.search(r"t[ée]l[ée]travail", row_text, re.I) else None,
            "salary": None,
        })

    log.info("Isarta → %d 筆", len(jobs))
    return jobs


def _text(row, tag: str, cls: str) -> str:
    el = row.find(tag, class_=cls)
    return el.get_text(" ", strip=True) if el else ""


def _location(row) -> str:
    """<h4 class="lieu-..">Paris<span>(75 - Paris)</span></h4> → "Paris (75)"。"""
    el = row.find("h4", class_="lieu-listing-monopage")
    if not el:
        return "France"
    span = el.find("span")
    dept = re.search(r"\((\d{2,3})", span.get_text(strip=True)) if span else None
    if span:
        span.extract()
    city = el.get_text(" ", strip=True).strip(" -")
    if not city:
        return "France"
    return f"{city} ({dept.group(1)})" if dept else city


def _contract(row) -> str | None:
    el = row.find("div", class_="type-horaire-listing-monopage")
    m = CONTRACT_PAT.search(el.get_text(" ", strip=True) if el else "")
    if not m:
        return None
    val = m.group(1)
    return "Intérim" if val.lower().startswith("int") else val.capitalize()


def _date(text: str) -> str | None:
    m = DATE_PAT.search(text)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
