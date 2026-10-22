"""HelloWork — 法國大型求職平台，列表頁透過 JobSpy，詳情頁自己抓。

列表：JobSpy 的 HelloWork scraper（解析 /fr-fr/emploi/recherche.html 的搜尋結果卡片）。
卡片只有職稱、公司、地點、合約，沒有內文——而「明確要求中文」幾乎都寫在內文裡，
所以另外抓詳情頁，從 schema.org 的 JobPosting（ld+json）取內文與公告日。

詳情頁逐則請求，所以有名額上限（DETAIL_LIMIT）與間隔：
- 已經補過詳情的職缺（known_urls）不重抓。
- 中文相關搜尋詞（mandarin、chinois⋯）撈到的排最前面——沒有內文就判斷不了要不要收。
- 沒補到詳情的不給公告日，下一輪會再排進名額（跟 Isarta 以前的做法一樣）。

robots.txt：每輪先讀，搜尋頁或詳情頁被 Disallow 就整組跳過。加這個來源時
開發環境連不到 hellowork.com，沒能人工確認，所以交給程式每次檢查。
"""
from __future__ import annotations

import json
import logging
import time
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from scraper.net import TIMEOUT, Budget, blocked_reason, report_blocked, retry_call, session
from scraper.sources.indeed_jobspy import _salary
from scraper.util import clean_str, to_date_str

log = logging.getLogger("chasse.hellowork")
HTTP = session()

BASE_URL = "https://www.hellowork.com"
SEARCH_URL = f"{BASE_URL}/fr-fr/emploi/recherche.html"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
SAMPLE_JOB_URL = f"{BASE_URL}/fr-fr/emplois/12345678.html"   # 只拿來問 robots.txt 詳情頁的路徑
HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "accept-language": "fr-FR,fr;q=0.9",
}

TIME_BUDGET_S = 600     # 列表＋詳情合計
LIST_BUDGET_S = 360     # 列表最多用這麼久，剩下的時間留給詳情頁
RESULTS_PER_TERM = 60   # 一頁 30 筆 → 每個詞最多翻 2 頁（hours_old 已經濾掉大部分舊職缺）
DETAIL_LIMIT = 100
DETAIL_PAUSE_S = 1.5
# 搜尋詞含這些字的結果，詳情頁排最前面
PRIORITY_WORDS = ("mandarin", "chinese", "chinois")


def fetch(search_terms: list[str], hours_old: int = 72, known_urls: set[str] | None = None) -> list[dict]:
    known_urls = known_urls or set()
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.error("python-jobspy 未安裝：pip install python-jobspy")
        return []
    budget = Budget(TIME_BUDGET_S)
    if not _robots_allow() or _blocked():
        return []

    found: dict[str, dict] = {}
    priority: list[str] = []
    list_budget = Budget(LIST_BUDGET_S)
    for term in search_terms:
        if list_budget.expired():
            log.warning("HelloWork 列表時間預算用完，%r 之後的搜尋詞跳過", term)
            break
        try:
            df = retry_call(
                lambda: scrape_jobs(
                    site_name=["hellowork"],
                    search_term=term,
                    location="France",
                    distance=None,          # 不帶 ray：location 是整個法國，不是某個城市周邊
                    results_wanted=RESULTS_PER_TERM,
                    hours_old=hours_old,
                    fetch_description=False,  # 詳情頁自己抓，才能控制名額與間隔
                    verbose=0,
                ),
                what=f"HelloWork {term!r}",
            )
        except Exception as e:  # 單一搜尋詞失敗不影響其他
            log.warning("HelloWork 搜尋 %r 失敗: %s", term, e)
            continue

        new = 0
        is_priority = any(w in term.lower() for w in PRIORITY_WORDS)
        for _, row in df.iterrows():
            job = _to_job(row)
            if job is None or not job["url"] or job["url"] in found:
                continue
            url = job["url"]
            found[url] = job
            new += 1
            if is_priority:
                priority.append(url)
        log.info("HelloWork %r → %d 筆（新 %d）", term, len(df), new)
        time.sleep(3)  # 對來源友善；JobSpy 翻頁之間也會自己等 2–5 秒

    _details(found, priority, known_urls, budget)
    log.info("HelloWork 合計 → %d 筆", len(found))
    return list(found.values())


def _to_job(row) -> dict | None:
    job_type = clean_str(row.get("job_type")).lower()
    if "internship" in job_type:
        return None   # 實習／建教（enrich 也會排除），別讓它們佔掉詳情頁名額
    return {
        "title": clean_str(row.get("title")),
        "company": clean_str(row.get("company")),
        "location": clean_str(row.get("location")),
        "description": "",
        "url": clean_str(row.get("job_url")),
        "source": "HelloWork",
        # 列表上的「il y a 3 jours」不當公告日：有日期的職缺會被當成「詳情頁補過了」，
        # 下一輪就不會再排進名額，內文永遠補不到。公告日等詳情頁的 datePosted。
        "date_posted": None,
        "contract": _contract(job_type),
        "work_mode": "remote" if row.get("is_remote") is True else None,
        "salary": _salary(row),
    }


def _contract(job_type: str) -> str | None:
    """JobSpy 的 job_type 是英文分類：CDI 沒有自己的標籤，只會是 fulltime／parttime；
    CDD 和 Intérim 都是 temporary——分不出來就留給 enrich 從內文判斷。"""
    if not job_type or any(t in job_type for t in ("temporary", "contract", "internship")):
        return None
    if "fulltime" in job_type or "parttime" in job_type:
        return "CDI"
    return None


def _details(found: dict[str, dict], priority: list[str], known_urls: set[str], budget: Budget) -> None:
    rest = [u for u in found if u not in set(priority)]
    todo = [u for u in priority + rest if u not in known_urls][:DETAIL_LIMIT]
    done = 0
    for i, url in enumerate(todo):
        if budget.expired():
            log.warning("HelloWork 時間預算用完，剩 %d 則詳情頁下一輪再補", len(todo) - i)
            break
        try:
            r = HTTP.get(url, headers=HEADERS, timeout=TIMEOUT)
        except Exception as e:
            log.warning("HelloWork 詳情頁失敗 %s: %s", url, e)
            continue
        reason = blocked_reason(r)
        if reason:
            report_blocked("HelloWork", f"詳情頁 {reason}；剩 {len(todo) - i} 則不抓（列表照常收）")
            break
        if r.status_code != 200:
            log.warning("HelloWork 詳情頁 HTTP %d：%s", r.status_code, url)
            continue
        found[url].update(_parse_detail(r.text, found[url]))
        done += 1
        time.sleep(DETAIL_PAUSE_S)
    log.info("HelloWork 詳情頁：補了 %d 則（需要 %d、上限 %d）", done,
             sum(1 for u in found if u not in known_urls), DETAIL_LIMIT)


def _parse_detail(html: str, job: dict) -> dict:
    """詳情頁的 JobPosting → {description, date_posted, company}；認不出來就回空 dict。"""
    posting = _job_posting(BeautifulSoup(html, "html.parser"))
    if not posting:
        return {}
    out: dict = {}
    desc = clean_str(posting.get("description"))
    if desc:
        out["description"] = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
    # datePosted 是巴黎當地時間寫成 UTC 格式（JobSpy 的註解），取日期部分就是巴黎的那一天
    date = to_date_str(clean_str(posting.get("datePosted"))[:10] or None)
    if date:
        out["date_posted"] = date
    org = posting.get("hiringOrganization")
    if not job.get("company") and isinstance(org, dict) and clean_str(org.get("name")):
        out["company"] = clean_str(org.get("name"))
    return out


def _job_posting(soup) -> dict | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _robots_allow() -> bool:
    """robots.txt 不允許搜尋頁或詳情頁就整組跳過。讀不到（連線失敗、5xx）也跳過：
    寧可少一輪資料，也不在不確定的情況下抓；404 表示沒有限制。"""
    try:
        r = HTTP.get(ROBOTS_URL, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        log.warning("HelloWork robots.txt 讀不到，這輪跳過：%s", e)
        return False
    reason = blocked_reason(r)
    if reason:
        report_blocked("HelloWork", f"robots.txt {reason}；整組跳過")
        return False
    if r.status_code == 404:
        return True
    if r.status_code != 200:
        log.warning("HelloWork robots.txt HTTP %d，這輪跳過", r.status_code)
        return False
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    for url in (f"{SEARCH_URL}?k=marketing&l=France", SAMPLE_JOB_URL):
        if not rp.can_fetch("*", url):
            log.error("HelloWork 的 robots.txt 不允許 %s，整組跳過——要重新評估這個來源", url)
            print(f"::warning title=HelloWork robots.txt::不允許 {url}，這個來源整組跳過", flush=True)
            return False
    return True


def _blocked() -> bool:
    """JobSpy 遇到 403 只會 log 一行然後回 0 筆，看起來像「今天沒職缺」。
    先自己送一個搜尋請求，被擋就回報（網站統計列會標「被擋」）。"""
    try:
        r = HTTP.get(SEARCH_URL, params={"k": "marketing", "l": "France"}, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        log.warning("HelloWork 搜尋頁連不上，這輪跳過：%s", e)
        return True
    reason = blocked_reason(r)
    if reason:
        report_blocked("HelloWork", f"{reason}；整組跳過")
        return True
    time.sleep(2)
    return False
