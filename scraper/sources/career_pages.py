"""公司官網職缺頁：Greenhouse 與 Lever 的公開 Job Board API。

很多公司的「Careers」頁其實是 Greenhouse 或 Lever 托管的，兩家都提供不用登入的
公開 JSON API，本來就是給人拿去串接的：
- Greenhouse：GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
- Lever：     GET https://api.lever.co/v0/postings/<公司>?mode=json

要查哪些公司寫在 keywords.yml 的 career_pages（網址最後那段 → 公司名）。
公司名打錯或公司換了 ATS 會回 404：這一輪跳過那家，Actions 頁面會列出是哪幾家。

兩家都回傳整個公司的全部職缺（全球），所以先篩地點（法國），再篩公告日：
- Greenhouse 用 first_published（第一次刊登）；沒有這個欄位才退回 updated_at，
  而且只拿來篩新舊、不當公告日——updated_at 是「最後更新」，舊缺改個字也會變新。
- Lever 用 createdAt（建立時間）。
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from scraper.net import TIMEOUT, Budget, blocked_reason, report_blocked, session
from scraper.util import clean_str, norm

log = logging.getLogger("chasse.career_pages")
HTTP = session()

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
HEADERS = {"accept": "application/json", "user-agent": "jos-chasse/1.0 (personal job search)"}
TIME_BUDGET_S = 120   # 每個 ATS 各自的上限；一家公司一個請求，正常 15 家約半分鐘
PAUSE_S = 1
FRANCE_PAT = re.compile(r"\b(france|paris|ile-de-france|french)\b")
INTERN_PAT = re.compile(r"\b(intern|internship|stage|stagiaire|alternance|apprenti|apprentissage)\b")


def fetch_greenhouse(companies: dict, hours_old: int = 72, idf_cities: list[str] | None = None) -> list[dict]:
    return _fetch("Greenhouse", companies, hours_old, idf_cities or [], _greenhouse_jobs)


def fetch_lever(companies: dict, hours_old: int = 72, idf_cities: list[str] | None = None) -> list[dict]:
    return _fetch("Lever", companies, hours_old, idf_cities or [], _lever_jobs)


def _fetch(source: str, companies: dict, hours_old: int, idf_cities: list[str], parse) -> list[dict]:
    if not companies:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    budget = Budget(TIME_BUDGET_S)
    jobs: list[dict] = []
    missing: list[str] = []
    for i, (slug, name) in enumerate(companies.items()):
        if budget.expired():
            log.warning("%s 時間預算用完，剩 %d 家下一輪再查", source, len(companies) - i)
            break
        url = (GREENHOUSE_URL if source == "Greenhouse" else LEVER_URL).format(slug=slug)
        params = {"content": "true"} if source == "Greenhouse" else {"mode": "json"}
        try:
            r = HTTP.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        except Exception as e:
            log.warning("%s %s 連線失敗: %s", source, slug, e)
            continue
        reason = blocked_reason(r)
        if reason:
            report_blocked(source, f"{reason}；剩 {len(companies) - i} 家不查")
            break
        if r.status_code == 404:
            missing.append(slug)
            continue
        try:
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("%s %s 回應有問題: %s", source, slug, e)
            continue
        found = [j for j in parse(data, clean_str(name) or slug, cutoff, idf_cities) if j]
        jobs += found
        log.info("%s %s → %d 筆（法國、%d 小時內）", source, slug, len(found), hours_old)
        time.sleep(PAUSE_S)
    if missing:
        msg = "、".join(missing)
        log.warning("%s 找不到這些公司（網址寫錯或已換 ATS）：%s", source, msg)
        print(f"::warning title={source} 找不到的公司::{msg}——請檢查 keywords.yml 的 career_pages", flush=True)
    log.info("%s 合計 → %d 筆", source, len(jobs))
    return jobs


def _greenhouse_jobs(data, company: str, cutoff: datetime, idf_cities: list[str]):
    for j in (data.get("jobs") or []) if isinstance(data, dict) else []:
        if not isinstance(j, dict):
            continue
        loc = clean_str((j.get("location") or {}).get("name") if isinstance(j.get("location"), dict) else "")
        offices = " ".join(clean_str(o.get("name")) for o in j.get("offices") or [] if isinstance(o, dict))
        if not _in_france(f"{loc} {offices}", idf_cities):
            continue
        first = _iso(j.get("first_published"))
        when = first or _iso(j.get("updated_at"))
        if when is None or when < cutoff:   # 沒有日期的不收：可能是開了很久的缺
            continue
        title = clean_str(j.get("title"))
        if INTERN_PAT.search(norm(title)):
            continue
        yield {
            "title": title,
            "company": clean_str(j.get("company_name")) or company,
            "location": loc or offices,
            "description": _text(html.unescape(clean_str(j.get("content")))),
            "url": clean_str(j.get("absolute_url")),
            "source": "Greenhouse",
            "date_posted": first.date().isoformat() if first else None,
            "contract": None,
            "work_mode": None,
            "salary": None,
        }


def _lever_jobs(data, company: str, cutoff: datetime, idf_cities: list[str]):
    for j in data if isinstance(data, list) else []:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories") if isinstance(j.get("categories"), dict) else {}
        locs = [clean_str(cats.get("location"))] + [clean_str(x) for x in cats.get("allLocations") or []]
        loc_text = " ".join(x for x in locs if x)
        if clean_str(j.get("country")).upper() != "FR" and not _in_france(loc_text, idf_cities):
            continue
        created = _epoch_ms(j.get("createdAt"))
        if created is None or created < cutoff:
            continue
        title = clean_str(j.get("text"))
        commitment = clean_str(cats.get("commitment"))
        if INTERN_PAT.search(norm(f"{title} {commitment}")):
            continue
        parts = [clean_str(j.get("descriptionPlain"))]
        for block in j.get("lists") or []:
            if isinstance(block, dict):
                parts += [clean_str(block.get("text")), _text(clean_str(block.get("content")))]
        parts.append(clean_str(j.get("additionalPlain")))
        mode = clean_str(j.get("workplaceType")).lower()
        yield {
            "title": title,
            "company": company,
            "location": clean_str(cats.get("location")) or loc_text,
            "description": " ".join(p for p in parts if p),
            "url": clean_str(j.get("hostedUrl")),
            "source": "Lever",
            "date_posted": created.date().isoformat(),
            "contract": commitment or None,   # enrich 會把 CDI／Full-time／CDD 換成統一寫法
            "work_mode": {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}.get(mode),
            "salary": None,
        }


def _in_france(text: str, idf_cities: list[str]) -> bool:
    t = norm(text)
    if FRANCE_PAT.search(t):
        return True
    t_sp = t.replace("-", " ")
    return any(re.search(rf"\b{re.escape(c.replace('-', ' '))}\b", t_sp) for c in idf_cities)


def _text(markup: str) -> str:
    return BeautifulSoup(markup, "html.parser").get_text(" ", strip=True) if markup else ""


def _iso(value) -> datetime | None:
    s = clean_str(value)
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _epoch_ms(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
