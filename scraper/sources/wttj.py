"""Welcome to the Jungle — 公開 Algolia 搜尋 API（前端同一組公開金鑰）。

不解析網頁，直接拿 JSON。用預設 index（關聯度排序）——實測它本身就把當日
發布的職缺排在前面，等於關聯度＋新鮮度兼顧；另一個 replica index
wk_cms_jobs_production_published_at_desc 是純日期排序，會把「主動應徵」
這類不相關的職缺推到最前面，所以不用。

單一搜尋詞的命中數可達三千筆（'marketing' 有 3082 筆），抓不完也不該抓完，
但原本每詞只抓 2 頁 × 50 筆＝100 筆太少，放寬到 6 頁。
"""
from __future__ import annotations

import json
import logging
import time

import requests

from scraper.net import session

from scraper.util import to_date_str

log = logging.getLogger("chasse.wttj")
HTTP = session()

ALGOLIA_APP = "CSEKHVMS53"
ALGOLIA_KEY = "4bd8f6215d0cc52b26430765769e65a0"  # 公開搜尋金鑰（網站前端使用的同一組）
ENDPOINT = f"https://{ALGOLIA_APP.lower()}-dsn.algolia.net/1/indexes/*/queries"
INDEX = "wk_cms_jobs_production"

HEADERS = {
    "x-algolia-application-id": ALGOLIA_APP,
    "x-algolia-api-key": ALGOLIA_KEY,
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://www.welcometothejungle.com",
    "referer": "https://www.welcometothejungle.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}


def fetch(search_terms: list[str], pages_per_term: int = 6, hits_per_page: int = 50) -> list[dict]:
    jobs: list[dict] = []
    for term in search_terms:
        for page in range(pages_per_term):
            hits = _query(term, page, hits_per_page)
            if hits is None:
                break  # 這個詞失敗，換下一個
            for h in hits:
                jobs.append(_to_job(h))
            if len(hits) < hits_per_page:
                break  # 沒有下一頁了
            time.sleep(1)
        log.info("WTTJ %r → 累計 %d 筆", term, len(jobs))
        time.sleep(2)
    return jobs


def _query(term: str, page: int, hits_per_page: int) -> list[dict] | None:
    params = (
        f"query={requests.utils.quote(term)}"
        f"&hitsPerPage={hits_per_page}&page={page}"
        f"&filters={requests.utils.quote('offices.country_code:FR')}"
    )
    payload = {"requests": [{"indexName": INDEX, "params": params}]}
    try:
        r = HTTP.post(ENDPOINT, data=json.dumps(payload), headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()["results"][0].get("hits", [])
    except Exception as e:
        log.warning("WTTJ 查詢 %r p%d 失敗: %s", term, page, e)
        return None


def _to_job(h: dict) -> dict:
    org = h.get("organization") or {}
    offices = h.get("offices") or []
    fr_offices = [o for o in offices if (o.get("country_code") or "").upper() == "FR"]
    city = (fr_offices[0].get("city") if fr_offices else (offices[0].get("city") if offices else "")) or ""

    remote_flag = (h.get("remote") or "").lower()
    work_mode = {"fulltime": "remote", "partial": "hybrid", "punctual": "onsite", "no": "onsite"}.get(remote_flag)

    org_slug = org.get("slug") or ""
    job_slug = h.get("slug") or ""
    url = (
        f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
        if org_slug and job_slug
        else f"https://www.welcometothejungle.com/fr/jobs?query={requests.utils.quote(h.get('name') or '')}"
    )

    return {
        "title": h.get("name") or "",
        "company": org.get("name") or "",
        "location": city or "France",
        "description": _text(h),
        "url": url,
        "source": "Welcome to the Jungle",
        "date_posted": to_date_str(h.get("published_at_timestamp") or h.get("published_at")),
        "contract": h.get("contract_type") or None,
        "work_mode": work_mode,
        "salary": None,
    }


def _text(h: dict) -> str:
    """組出可供關鍵字比對的文字（Algolia hit 不含完整描述，用 profession/摘要欄位補）。"""
    parts = [h.get("name") or ""]
    prof = h.get("profession") or {}
    if isinstance(prof, dict):
        parts += [str(v) for v in prof.values() if isinstance(v, str)]
    for k in ("description", "summary", "profile"):
        v = h.get(k)
        if isinstance(v, str):
            parts.append(v)
    return " \n".join(parts)
