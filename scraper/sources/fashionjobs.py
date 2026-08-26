"""Fashion Jobs France — 時尚產業職缺板（requests + BeautifulSoup）。

搜尋：GET https://fr.fashionjobs.com/s/?keyword=<關鍵字>
職缺連結模式：/emploi/<company>/<title>,<ID>.html

列表卡片有職稱、公司、描述摘要，但沒有地點；詳情頁帶 JSON-LD 的
schema.org/JobPosting，地點、日期、合約型態一次到位。

詳情頁一則要一次請求，所以有 DETAIL_LIMIT 上限（對站方友善）。這個站三成是
實習／建教，列表前段又多是店長、門市主管這類非行銷職缺，名額全花在會被丟掉
的職缺上，下次執行又被當成「還沒補過」，永遠推進不到真正要的職缺。

所以先把所有列表頁收完，再依 detail_priority() 排序花名額：標題就被硬性排除
的完全不花，卡片看得出是目標職缺的優先花，剩下的用餘額補——卡片摘要是截斷的，
不能只憑它判死，否則靠完整描述才分得出類的職缺會被永久跳過。

另外有 TIME_BUDGET_S 這道牆：詳情頁逐則請求，最壞情況（每則都吃滿 timeout）
會遠超過 workflow 的 timeout-minutes，一旦整個 job 被砍掉，連帶所有來源的結果
都不會被 commit。寧可這個來源少補幾則，也不能拖垮整批。
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("chasse.fashionjobs")

SEARCH_URL = "https://fr.fashionjobs.com/s/"
PAGES = 3  # 站上沒有排序參數，只能靠翻頁擴大涵蓋範圍
JOB_LINK = re.compile(r"/emploi/[^\"'#?]+,(\d+)\.html")
DETAIL_LIMIT = 150      # 排過優先序才花名額，實際用量遠低於此；上限只是防爆
TIME_BUDGET_S = 900     # 這個來源總共最多花 15 分鐘（列表＋詳情），超過就收工
LIST_TIMEOUT = 20
DETAIL_TIMEOUT = 15     # 詳情頁不值得為單頁卡住 30 秒
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


def fetch(known_urls: set[str] | None = None, detail_priority=None) -> list[dict]:
    """detail_priority(job) → None／0／1，決定詳情頁名額的花法（由 main.py 帶入分類規則）。"""
    known_urls = known_urls or set()
    detail_priority = detail_priority or (lambda _job: 0)
    ctx = _Ctx(known_urls, detail_priority)

    for term in SEARCH_TERMS:
        if ctx.expired():
            log.warning("Fashion Jobs 時間預算用完，%r 之後的搜尋詞跳過", term)
            break
        for page in range(1, PAGES + 1):
            if ctx.expired() or not _page(term, page, ctx):
                break

    _spend_details(ctx)

    log.info("Fashion Jobs 合計 %d 筆（詳情頁抓了 %d 次，標題排除跳過 %d 筆，名額／時間不足未補 %d 筆）",
             len(ctx.jobs), DETAIL_LIMIT - ctx.budget, ctx.skipped, ctx.unfetched)
    return ctx.jobs


def _spend_details(ctx: "_Ctx") -> None:
    """列表全部收完後，依優先序把詳情頁名額花掉。"""
    pending = sorted(ctx.pending, key=lambda t: t[0])
    for i, (_prio, job) in enumerate(pending):
        if ctx.budget <= 0 or ctx.expired():
            ctx.unfetched = len(pending) - i
            break
        ctx.budget -= 1
        job.update(_detail(job["url"]))
        time.sleep(1.5)



class _Ctx:
    """跨搜尋詞／跨頁共用的狀態：已見過的職缺、詳情頁名額、統計。"""

    def __init__(self, known_urls: set[str], detail_priority):
        self.known_urls = known_urls
        self.detail_priority = detail_priority
        self.jobs: list[dict] = []
        self.pending: list[tuple[int, dict]] = []   # (優先序, job) — 列表收完才動詳情頁
        self.seen: set[str] = set()
        self.budget = DETAIL_LIMIT
        self.skipped = 0
        self.unfetched = 0
        self.deadline = time.monotonic() + TIME_BUDGET_S

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline


def _page(term: str, page: int, ctx: "_Ctx") -> bool:
    """抓一頁；回傳 False 表示這個搜尋詞不用再往下翻。"""
    try:
        r = requests.get(SEARCH_URL, params={"keyword": term, "page": page},
                         headers=HEADERS, timeout=LIST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        log.warning("Fashion Jobs 列表 %r p%d 失敗: %s", term, page, e)
        return False

    soup = BeautifulSoup(r.text, "html.parser")
    found = 0
    for a in soup.find_all("a", href=JOB_LINK):
        href = a.get("href", "")
        m = JOB_LINK.search(href)
        if not m or m.group(1) in ctx.seen:
            continue
        # 職稱優先用 title 屬性：卡片內文是被 line-clamp 截斷的
        title = (a.get("title") or a.get_text(" ", strip=True)).strip()
        if len(title) < 4:
            continue
        ctx.seen.add(m.group(1))
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
        # 排進詳情頁候補：JSON-LD 有地點／日期／合約與完整描述，列表卡片都沒有
        prio = ctx.detail_priority(job)
        if prio is None:
            ctx.skipped += 1
        elif full_url not in ctx.known_urls:
            ctx.pending.append((prio, job))
        ctx.jobs.append(job)
        found += 1

    log.info("Fashion Jobs %r p%d → %d 筆", term, page, found)
    time.sleep(2)
    return found > 0  # 這頁全是看過的或已翻到底


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
        r = requests.get(url, headers=HEADERS, timeout=DETAIL_TIMEOUT)
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
