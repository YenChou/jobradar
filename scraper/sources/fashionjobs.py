"""Fashion Jobs France — 時尚產業職缺板（requests + BeautifulSoup）。

搜尋：GET https://fr.fashionjobs.com/s/?keyword=<關鍵字>
職缺連結模式：/emploi/<company>/<title>,<ID>.html

列表卡片有職稱、公司、描述摘要，但沒有地點；詳情頁帶 JSON-LD 的
schema.org/JobPosting，地點、日期、合約型態一次到位。

詳情頁一則要一次請求，所以有 DETAIL_LIMIT 上限（對站方友善）。名額只花在
worth_detail() 認可的職缺上——這個站三成是實習／建教，列表前段又多是店長、
門市主管這類非行銷職缺，不先篩就會把名額全花在會被丟掉的職缺上，而且它們
不會進 jobs.json、下次執行又被當成「還沒補過」，永遠推進不到真正要的職缺。
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("jobradar.fashionjobs")

SEARCH_URL = "https://fr.fashionjobs.com/s/"
JOB_LINK = re.compile(r"/emploi/[^\"'#?]+,(\d+)\.html")
DETAIL_LIMIT = 150  # 先篩過才花名額，實際用量遠低於此；上限只是防爆
HEADERS = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 這個站是時尚產業職缺板，用少量泛搜尋詞就能涵蓋五類
SEARCH_TERMS = [
    "marketing", "crm", "acquisition", "data analyst",
    "chef de produit", "product marketing", "traffic manager", "e-commerce",
]

# schema.org 的 employmentType → 站上慣用的法文合約別
EMPLOYMENT_TYPE = {
    "FULL_TIME": "CDI", "PART_TIME": "CDI", "CONTRACTOR": "CDD",
    "TEMPORARY": "CDD", "INTERN": "Stage", "OTHER": None,
}


def fetch(known_urls: set[str] | None = None, worth_detail=None) -> list[dict]:
    """worth_detail(job) → 這則值不值得花詳情頁名額（由 main.py 帶入分類規則）。"""
    known_urls = known_urls or set()
    worth_detail = worth_detail or (lambda _job: True)
    jobs: list[dict] = []
    seen: set[str] = set()
    detail_budget = DETAIL_LIMIT
    skipped = 0

    for term in SEARCH_TERMS:
        try:
            r = requests.get(SEARCH_URL, params={"keyword": term}, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            log.warning("Fashion Jobs 列表 %r 失敗: %s", term, e)
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for a in soup.find_all("a", href=JOB_LINK):
            href = a.get("href", "")
            m = JOB_LINK.search(href)
            if not m or m.group(1) in seen:
                continue
            # 職稱優先用 title 屬性：卡片內文是被 line-clamp 截斷的
            title = (a.get("title") or a.get_text(" ", strip=True)).strip()
            if len(title) < 4:
                continue
            seen.add(m.group(1))
            full_url = href if href.startswith("http") else "https://fr.fashionjobs.com" + href

            job = {
                "title": title,
                "company": _card_company(a) or _company_from_url(href),
                "location": "France",
                "description": _card_snippet(a),
                "url": full_url,
                "source": "Fashion Jobs",
                "date_posted": None,
                "contract": None,
                "work_mode": None,
                "salary": None,
            }
            # 值得收、且沒補過的職缺 → 抓詳情頁的 JSON-LD 補地點／日期／合約與完整描述
            if not worth_detail(job):
                skipped += 1
            elif full_url not in known_urls and detail_budget > 0:
                detail_budget -= 1
                job.update(_detail(full_url))
                time.sleep(1.5)
            jobs.append(job)
            found += 1
        log.info("Fashion Jobs %r → %d 筆", term, found)
        time.sleep(2)
    log.info("Fashion Jobs 合計 %d 筆（詳情頁抓了 %d 次，預篩跳過 %d 筆）",
             len(jobs), DETAIL_LIMIT - detail_budget, skipped)
    return jobs


def _card_company(a) -> str:
    """卡片裡公司名是連到 /recrutement/ 的 span。"""
    card = a.parent.parent if a.parent else None
    if card is None:
        return ""
    span = card.find("span", attrs={"data-lien": re.compile(r"/recrutement/")})
    return span.get_text(" ", strip=True) if span else ""


def _card_snippet(a) -> str:
    """列表卡片的描述摘要——詳情頁抓不到時至少有東西給關鍵字比對。"""
    card = a.parent.parent if a.parent else None
    if card is None:
        return ""
    divs = card.find_all("div", recursive=False)
    return divs[-1].get_text(" ", strip=True)[:2000] if divs else ""


def _company_from_url(href: str) -> str:
    m = re.search(r"/emploi/([^/]+)/", href)
    return m.group(1).replace("-", " ").title() if m else ""


def _detail(url: str) -> dict:
    """詳情頁的 schema.org/JobPosting，比刮 HTML 穩定。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for sc in soup.find_all("script", type="application/ld+json"):
            raw = sc.string or ""
            if '"JobPosting"' not in raw:
                continue
            d = json.loads(raw)
            out: dict = {}
            desc = re.sub(r"<[^>]+>", " ", d.get("description") or "")
            desc = re.sub(r"\s+", " ", desc).strip()
            if desc:
                out["description"] = desc[:5000]
            city = ((d.get("jobLocation") or {}).get("address") or {}).get("addressLocality")
            if city:
                out["location"] = str(city).title()
            if d.get("datePosted"):
                out["date_posted"] = str(d["datePosted"])[:10]
            contract = EMPLOYMENT_TYPE.get(str(d.get("employmentType") or "").upper())
            if contract:
                out["contract"] = contract
            org = (d.get("hiringOrganization") or {}).get("name")
            if org:
                out["company"] = str(org)
            return out
        log.debug("Fashion Jobs 詳情頁沒有 JSON-LD: %s", url)
    except Exception as e:
        log.debug("Fashion Jobs 詳情頁失敗 %s: %s", url, e)
    return {}
