"""HelloWork 來源：robots.txt、被擋、詳情頁名額與優先順序。

開發環境連不到 hellowork.com，用假 HTTP 與假 JobSpy 驅動控制流。

執行：.venv/bin/python tests/test_hellowork.py（或 pytest tests/test_hellowork.py）
"""
import contextlib
import logging
import pathlib
import sys
import types

import pandas as pd

logging.basicConfig(level=logging.CRITICAL)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import jobspy
from scraper import main, net
from scraper.sources import hellowork as hw


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


def detail_html(desc, date="2026-10-17T09:30:00Z", graph=False):
    import json
    posting = {"@type": "JobPosting", "description": f"<p>{desc}</p>", "datePosted": date,
               "hiringOrganization": {"@type": "Organization", "name": "Acme"}}
    data = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, posting]} if graph else posting
    return f'<html><script type="application/ld+json">{json.dumps(data)}</script></html>'


ROBOTS_OK = Resp(200, "User-agent: *\nDisallow: /fr-fr/candidat/\n")


class FakeHTTP:
    """依網址回應：robots.txt、搜尋頁、各詳情頁。記下所有請求。"""
    def __init__(self, robots=ROBOTS_OK, search=Resp(200, "<html>ok</html>"), details=None):
        self.robots, self.search, self.details, self.urls = robots, search, details or {}, []

    def get(self, url, **kw):
        self.urls.append(url)
        if url == hw.ROBOTS_URL:
            return self.robots
        if url == hw.SEARCH_URL:
            return self.search
        r = self.details.get(url, Resp(200, detail_html("Description standard.")))
        if isinstance(r, Exception):
            raise r
        return r


def row(n, **kw):
    return {"title": f"Chargé CRM {n}", "company": f"Co {n}", "location": "Paris, 75, FR",
            "job_url": f"https://www.hellowork.com/fr-fr/emplois/{n}.html", "job_type": "fulltime",
            "is_remote": False, "min_amount": None, "max_amount": None, **kw}


def fake_scrape(results):
    """results：搜尋詞 → 列。記下每次呼叫的參數。"""
    calls = []
    def scrape_jobs(**kw):
        calls.append(kw)
        return pd.DataFrame(results.get(kw["search_term"], []))
    return scrape_jobs, calls


def run(terms, results, http, known=None, **consts):
    scrape, calls = fake_scrape(results)
    net.BLOCKED.clear()
    with patched(hw, HTTP=http, time=types.SimpleNamespace(sleep=lambda s: None), **consts), \
         patched(jobspy, scrape_jobs=scrape):
        jobs = hw.fetch(terms, 72, known or set())
    return jobs, calls


fail = 0
def ok(c, m):
    global fail
    print(("✅" if c else "❌") + " " + m)
    if not c:
        fail += 1


print("── robots.txt ──")
http = FakeHTTP(robots=Resp(200, "User-agent: *\nDisallow: /fr-fr/emploi/recherche.html\n"))
jobs, calls = run(["crm"], {"crm": [row(1)]}, http)
ok(jobs == [] and calls == [], "搜尋頁被 Disallow → 整組跳過，JobSpy 一次都不叫")
http = FakeHTTP(robots=Resp(200, "User-agent: *\nDisallow: /fr-fr/emplois/\n"))
jobs, calls = run(["crm"], {"crm": [row(1)]}, http)
ok(jobs == [] and calls == [], "詳情頁被 Disallow → 整組跳過")
jobs, _ = run(["crm"], {"crm": [row(1)]}, FakeHTTP(robots=Resp(404)))
ok(len(jobs) == 1, "沒有 robots.txt（404）→ 照常抓")
jobs, calls = run(["crm"], {"crm": [row(1)]}, FakeHTTP(robots=Resp(500)))
ok(jobs == [] and calls == [], "robots.txt 讀不到（500）→ 保守起見這輪跳過")

print()
print("── 被擋 ──")
jobs, calls = run(["crm"], {"crm": [row(1)]}, FakeHTTP(search=Resp(403, headers={"server": "cloudflare"})))
ok(jobs == [] and calls == [] and "HelloWork" in net.BLOCKED,
   f"搜尋頁 403 → 回報被擋、整組跳過：{net.BLOCKED.get('HelloWork')}")
urls = [row(n)["job_url"] for n in range(1, 5)]
http = FakeHTTP(details={urls[1]: Resp(403)})
jobs, _ = run(["crm"], {"crm": [row(n) for n in range(1, 5)]}, http)
ok(len(jobs) == 4 and "HelloWork" in net.BLOCKED, "詳情頁被擋 → 回報，但列表抓到的照樣回傳")
ok(urls[2] not in http.urls and urls[3] not in http.urls, "詳情頁被擋後就不再送詳情頁請求")
ok(jobs[0]["description"] and not jobs[2]["description"], "被擋之前補到的內文保留")

print()
print("── 詳情頁 ──")
http = FakeHTTP(details={urls[0]: Resp(200, detail_html("Mandarin courant exigé.", graph=True))})
jobs, calls = run(["crm"], {"crm": [row(1)]}, http)
j = jobs[0]
ok(j["description"] == "Mandarin courant exigé." and j["date_posted"] == "2026-10-17",
   f"從 ld+json（@graph）取內文與公告日：{j['description']!r} {j['date_posted']}")
ok(j["source"] == "HelloWork" and j["contract"] == "CDI", "來源標 HelloWork，fulltime → CDI")
ok(calls[0]["site_name"] == ["hellowork"] and calls[0]["location"] == "France"
   and calls[0]["fetch_description"] is False and calls[0]["distance"] is None,
   "JobSpy：只抓列表、地點是整個法國、不帶半徑")
jobs, _ = run(["crm"], {"crm": [row(1)]}, FakeHTTP(details={urls[0]: Resp(404)}))
ok(jobs[0]["date_posted"] is None and jobs[0]["description"] == "",
   "沒補到詳情 → 不給公告日（下一輪會再排進名額）")

jobs, _ = run(["crm"], {"crm": [row(1, job_type="internship"), row(2, job_type="fulltime, temporary")]}, FakeHTTP())
ok(len(jobs) == 1 and jobs[0]["contract"] is None,
   "實習不收（不佔詳情頁名額）；temporary 分不出 CDD／Intérim → 留給內文判斷")

http = FakeHTTP()
results = {"crm": [row(n) for n in range(1, 6)], "mandarin": [row(n) for n in range(10, 13)]}
jobs, _ = run(["crm", "mandarin"], results, http, known={row(2)["job_url"]}, DETAIL_LIMIT=4)
detail_urls = [u for u in http.urls if "/emplois/" in u]
ok(detail_urls[:3] == [row(n)["job_url"] for n in (10, 11, 12)],
   "中文相關搜尋詞撈到的，詳情頁排最前面")
ok(len(detail_urls) == 4 and row(2)["job_url"] not in detail_urls,
   f"已補過的不重抓、名額上限生效：{len(detail_urls)} 則")
ok(len(jobs) == 8, "重複出現的職缺只留一筆")

print()
print("── 沒有內文的這一輪，不抹掉上次從內文得到的資訊 ──")
old = {"description_snippet": "Mandarin courant.", "categories": ["crm"], "skills": ["emailing"],
       "bonus_tags": ["chinese"], "score": 20}
new = {"description_snippet": "", "categories": ["crm"], "skills": [], "bonus_tags": [], "score": 10}
main.keep_detail_info(new, old)
ok(new["description_snippet"] == "Mandarin courant." and new["bonus_tags"] == ["chinese"]
   and new["skills"] == ["emailing"] and new["score"] == 20, "摘要、標籤、技能、分數接回來")
fresh = {"description_snippet": "Nouveau texte.", "categories": [], "skills": [], "bonus_tags": [], "score": 5}
main.keep_detail_info(fresh, old)
ok(fresh["bonus_tags"] == [] and fresh["score"] == 5, "這次有內文就以這次為準")

print()
print("全部通過 ✅" if not fail else f"{fail} 項失敗 ❌")


def test_all():
    assert fail == 0


if __name__ == "__main__":
    sys.exit(1 if fail else 0)
