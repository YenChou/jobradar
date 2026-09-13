"""Fashion Jobs France — 時尚產業職缺板（tls_client + BeautifulSoup）。

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

from bs4 import BeautifulSoup
from tls_client import Session

from scraper.net import Budget, retry_call

log = logging.getLogger("chasse.fashionjobs")

BASE = "https://fr.fashionjobs.com"
SEARCH_URL = f"{BASE}/s/"
PAGES = 2  # 站方用 Cloudflare 擋機器人，把請求足跡壓低是為了不再被擋
JOB_LINK = re.compile(r"/emploi/[^\"'#?]+,(\d+)\.html")
DETAIL_LIMIT = 60       # 排過優先序才花名額；壓低是為了減少對站方的請求量
TIME_BUDGET_S = 900     # 這個來源總共最多花 15 分鐘（列表＋詳情），超過就收工
# tls_client 的 timeout 只吃純量秒數（沒有 connect/read 之分），別寫成 tuple
LIST_TIMEOUT = 20
DETAIL_TIMEOUT = 15     # 詳情頁不值得為單頁卡住 20 秒
# 站方推回來的時候不該靠重試加大足跡，所以這個來源用比 net 預設更低的重試次數
RETRIES = 1
HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "fr-FR,fr;q=0.9",
}

# 被擋的狀態碼。Cloudflare 限流／挑戰常用 429 和 503，不是只有 403。
BLOCKED_STATUS = (403, 429, 503)
# Cloudflare 的 JS 挑戰頁回的是 200，body 卻不是職缺列表。沒有這組偵測的話
# 整個來源會靜靜回 0 筆，log 看起來像「今天就是沒職缺」。
# 分兩類是因為誤判的代價是「整組收工，而且偽裝成今天剛好沒職缺」：
#   CF 專屬字串不可能出現在正常頁面，全頁比對安全；
#   "just a moment" 這種通用英文正常內文也可能出現，只在 <title> 裡比對。
CHALLENGE_CF = (
    "cf-browser-verification",
    "challenge-platform",
    "_cf_chl",
    "enable javascript and cookies to continue",
)
CHALLENGE_TITLES = ("just a moment", "attention required")
TITLE_PAT = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class _Blocked(Exception):
    """站方在擋我們。不重試——重試只會在對方推回來的當下加大足跡。"""

# 這個站在 Cloudflare 後面，會依 TLS 指紋擋掉 requests/urllib3（連 robots.txt
# 允許的路徑也擋）。用 tls_client 模擬瀏覽器指紋才拿得到內容。
# tls_client 不吃 net.session 那層 urllib3 Retry，所以重試改用 net.retry_call。
_HTTP = None


def _http() -> Session:
    """取得已暖身的 session。Cloudflare 要先有首頁發的 cookie 才放行其他頁。

    暖身成功才指派給 _HTTP：先指派的話，暖身請求一旦失敗（逾時、DNS 抖動這種
    暫時性問題），之後每次都會拿到這顆沒有 cookie 的壞 session，暖身再也不會
    重跑——一次網路抖動就讓整個來源當次歸零，而且救不回來。
    """
    global _HTTP
    if _HTTP is None:
        s = Session(client_identifier="chrome_120", random_tls_extension_order=True)
        r = s.get(BASE, headers=HEADERS, timeout_seconds=LIST_TIMEOUT)
        # 暖身回應本身也要驗：被擋時回的是 200 挑戰頁、不會拋例外，
        # 沒驗的話這顆從來沒拿到有效 cookie 的 session 會被快取起來，
        # 而且之後的 log 會說是「列表被擋」，看不出問題出在暖身。
        reason = blocked_reason(r)
        if reason:
            raise _Blocked(f"暖身就被擋（{reason}）")
        time.sleep(1)
        _HTTP = s          # 只有走到這裡才算暖身成功
    return _HTTP


def _get(url: str, params: dict | None = None, timeout: int = LIST_TIMEOUT):
    # _http() 放在 retry_call 外面：暖身被擋時拋的 _Blocked 不該被重試，
    # 那只會在站方推回來的當下多送請求。
    http = _http()
    return retry_call(
        lambda: http.get(url, params=params, headers=HEADERS, timeout_seconds=timeout),
        what=f"Fashion Jobs {url}",
        retries=RETRIES,
    )


def blocked_reason(r) -> str | None:
    """這個回應是不是「站方在擋我們」？是的話回傳原因，否則 None。"""
    if r.status_code in BLOCKED_STATUS:
        return f"HTTP {r.status_code}"
    if r.status_code == 200:
        head = (r.text or "")[:4000].lower()
        for marker in CHALLENGE_CF:
            if marker in head:
                return f"Cloudflare 挑戰頁（{marker}）"
        m = TITLE_PAT.search(head)
        title = m.group(1).strip() if m else ""
        for marker in CHALLENGE_TITLES:
            if marker in title:
                return f"Cloudflare 挑戰頁（title: {title[:40]}）"
    return None


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
        if ctx.blocked:
            break
        if ctx.expired():
            log.warning("Fashion Jobs 時間預算用完，%r 之後的搜尋詞跳過", term)
            break
        for page in range(1, PAGES + 1):
            if ctx.blocked or ctx.expired() or not _page(term, page, ctx):
                break

    _spend_details(ctx)

    log.info("Fashion Jobs 合計 %d 筆（詳情頁抓了 %d 次，標題排除跳過 %d 筆，名額／時間不足未補 %d 筆）",
             len(ctx.jobs), DETAIL_LIMIT - ctx.budget, ctx.skipped, ctx.unfetched)
    return ctx.jobs


def _spend_details(ctx: "_Ctx") -> None:
    """列表全部收完後，依優先序把詳情頁名額花掉。"""
    if ctx.blocked:
        return
    pending = sorted(ctx.pending, key=lambda t: t[0])
    for i, (_prio, job) in enumerate(pending):
        if ctx.budget <= 0 or ctx.expired():
            ctx.unfetched = len(pending) - i
            break
        ctx.budget -= 1
        job.update(_detail(job["url"], ctx))
        if ctx.blocked:
            ctx.unfetched = len(pending) - i - 1
            break
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
        self.blocked = False
        # 注意 self.budget 是「詳情頁名額計數」，時間預算是另一回事。
        # 用 net.Budget：只在請求前比對截止時間的話，超出量等於一次請求的
        # 長度，而重試讓那個長度變成近百秒。Budget 會預留這個邊際。
        self.clock = Budget(TIME_BUDGET_S)

    def expired(self) -> bool:
        return self.clock.expired()


def _page(term: str, page: int, ctx: "_Ctx") -> bool:
    """抓一頁；回傳 False 表示這個搜尋詞不用再往下翻。"""
    try:
        r = _get(SEARCH_URL, {"keyword": term, "page": page}, LIST_TIMEOUT)
    except _Blocked as e:
        ctx.blocked = True
        log.warning("Fashion Jobs %s，整組跳過", e)
        return False
    try:
        # 站方擋人時繼續打剩下的搜尋詞只會讓情況更糟，直接整組收工。
        # 注意不是只有 403：429／503 也是限流，而 Cloudflare 的 JS 挑戰頁
        # 回的是 200，要看 body 才認得出來。
        reason = blocked_reason(r)
        if reason:
            ctx.blocked = True
            log.warning("Fashion Jobs 被站方擋下（%s），整組跳過", reason)
            return False
        if r.status_code != 200:
            log.warning("Fashion Jobs 列表 %r p%d 回 %s", term, page, r.status_code)
            return False
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


def _detail(url: str, ctx: "_Ctx" = None) -> dict:
    """詳情頁的 schema.org/JobPosting，比刮 HTML 穩定。"""
    try:
        r = _get(url, None, DETAIL_TIMEOUT)
    except _Blocked as e:
        if ctx is not None:
            ctx.blocked = True
        log.warning("Fashion Jobs 詳情頁：%s，停止補詳情", e)
        return {}
    try:
        reason = blocked_reason(r)
        if reason:
            if ctx is not None:
                ctx.blocked = True
            log.warning("Fashion Jobs 詳情頁被擋下（%s），停止補詳情", reason)
            return {}
        if r.status_code != 200:
            log.debug("Fashion Jobs 詳情頁 %s 回 %s", url, r.status_code)
            return {}
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
