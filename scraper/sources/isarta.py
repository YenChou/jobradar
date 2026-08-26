"""Isarta France — 行銷/傳播職缺板，傳統 CGI 列表頁。

列表頁：https://isarta.fr/cgi-bin/emplois/jobs?cat=<分類>
一次回傳整個分類（站上沒有分頁連結），每筆是一個 <tr>，欄位都有語意
class（poste-/compagnie-/lieu-listing-monopage），直接取即可。
列表本身依「Publiée」日期由新到舊排序，所以新鮮度已經在前面。

只抓 marketing 一個分類會漏掉數位行銷與網路類職缺，所以多抓幾個相關分類
（站上共 39 個分類，其餘是人資、業務、地區別，與五類無關）。

詳情頁 https://isarta.fr/?job=<ID> 的內文是 JS（monopage.showDetail）載入的，
用 requests 只會拿到站台骨架，所以不抓詳情頁——分類改以職稱為準。
"""
from __future__ import annotations

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("chasse.isarta")

LIST_URL = "https://isarta.fr/cgi-bin/emplois/jobs"
CATEGORIES = ["marketing", "marketing-numerique-communication", "web-numerique", "teletravail"]
JOB_LINK = re.compile(r"\?job=(\d+)")
HEADERS = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
DATE_PAT = re.compile(r"Publi[ée]e?\s*:?\s*(\d{2})/(\d{2})/(\d{4})")
CONTRACT_PAT = re.compile(r"\b(CDI|CDD|Temporaire|Int[ée]rim|Permanent|Pigiste)\b", re.I)


def fetch(known_urls: set[str] | None = None) -> list[dict]:
    # known_urls 目前用不到（不抓詳情頁），保留參數讓 main.py 的註冊方式一致
    jobs: list[dict] = []
    seen: set[str] = set()
    for cat in CATEGORIES:
        _category(cat, jobs, seen)
    log.info("Isarta 合計 → %d 筆", len(jobs))
    return jobs


def _category(cat: str, jobs: list[dict], seen: set[str]) -> None:
    try:
        r = requests.get(LIST_URL, params={"cat": cat}, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.warning("Isarta 列表 cat=%s 失敗: %s", cat, e)
        return

    found = 0
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
        found += 1

    log.info("Isarta cat=%s → %d 筆", cat, found)
    time.sleep(2)


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
