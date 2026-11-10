"""公司官網職缺頁（Greenhouse、Lever 公開 API）：地點、公告日、找不到的公司、被擋。

開發環境連不到這兩個 API，用假 HTTP 驅動。

執行：.venv/bin/python tests/test_career_pages.py（或 pytest tests/test_career_pages.py）
"""
import contextlib
import logging
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.CRITICAL)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper import enrich, net
from scraper.main import load_cfg
from scraper.sources import career_pages as cp

cfg = load_cfg()
IDF = cfg["idf_cities"]
NOW = datetime.now(timezone.utc)
iso = lambda h: (NOW - timedelta(hours=h)).isoformat()
ms = lambda h: int((NOW - timedelta(hours=h)).timestamp() * 1000)


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
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}
        self.text = "{}" if data is not None else ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHTTP:
    def __init__(self, by_slug):
        self.by_slug, self.urls = by_slug, []

    def get(self, url, **kw):
        self.urls.append(url)
        slug = url.rstrip("/").split("/")[-2 if url.endswith("/jobs") else -1]
        return self.by_slug.get(slug, Resp(404, {"error": "not found"}))


def run(fn, companies, by_slug):
    net.BLOCKED.clear()
    http = FakeHTTP(by_slug)
    with patched(cp, HTTP=http, time=types.SimpleNamespace(sleep=lambda s: None)):
        return fn(companies, 72, IDF), http


fail = 0
def ok(c, m):
    global fail
    print(("✅" if c else "❌") + " " + m)
    if not c:
        fail += 1


def gh(title, loc, first=None, updated=None, **kw):
    return {"title": title, "location": {"name": loc}, "first_published": first, "updated_at": updated,
            "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{abs(hash(title + loc))}",
            "content": "&lt;p&gt;You will run &lt;strong&gt;CRM&lt;/strong&gt; campaigns.&lt;/p&gt;", **kw}


print("── Greenhouse ──")
data = {"jobs": [
    gh("CRM Manager", "Paris, France", first=iso(5), updated=iso(1)),
    gh("CRM Manager", "New York, NY", first=iso(5)),
    gh("Growth Marketing Specialist", "Boulogne-Billancourt", first=iso(10)),
    gh("Product Marketing Manager", "Paris", first=iso(24 * 20), updated=iso(2)),
    gh("Marketing Analyst", "Remote - France", updated=iso(3)),
    gh("Marketing Intern", "Paris", first=iso(2)),
    gh("Data Analyst", "Paris"),
]}
jobs, http = run(cp.fetch_greenhouse, {"acme": "Acme"}, {"acme": Resp(200, data)})
titles = [j["title"] for j in jobs]
ok(titles == ["CRM Manager", "Growth Marketing Specialist", "Marketing Analyst"],
   f"只收法國（含巴黎近郊、Remote - France）、72 小時內、非實習：{titles}")
ok("Product Marketing Manager" not in titles,
   "first_published 是 20 天前、只是最近更新過 → 不收（舊缺不算新職缺）")
j = jobs[0]
ok(j["date_posted"] == (NOW - timedelta(hours=5)).date().isoformat(), "公告日用 first_published，不是 updated_at")
ok(jobs[2]["date_posted"] is None, "沒有 first_published：只用 updated_at 篩新舊，不當公告日")
ok(j["description"] == "You will run CRM campaigns.", f"內文：HTML 先解跳脫再去標籤 → {j['description']!r}")
ok(j["company"] == "Acme" and j["source"] == "Greenhouse", "公司名用設定檔的名稱、來源標 Greenhouse")
ok(http.urls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs"], "打的是公開 Job Board API")
r = enrich.classify(j, cfg)
ok(r is not None and "crm" in r["categories"] and r["region"] == "paris", "照一般職類規則分類")

print()
print("── Lever ──")
lv = [
    {"text": "Lifecycle Marketing Specialist", "country": "FR", "createdAt": ms(6), "workplaceType": "hybrid",
     "categories": {"location": "Paris", "commitment": "CDI"}, "hostedUrl": "https://jobs.lever.co/acme/1",
     "descriptionPlain": "You will own our lifecycle.", "lists": [{"text": "Profile", "content": "<li>HubSpot</li>"}]},
    {"text": "Account Executive", "country": "DE", "createdAt": ms(6),
     "categories": {"location": "Berlin"}, "hostedUrl": "https://jobs.lever.co/acme/2"},
    {"text": "CRM Specialist", "createdAt": ms(30), "categories": {"location": "Remote", "allLocations": ["Paris", "Remote"]},
     "hostedUrl": "https://jobs.lever.co/acme/3"},
    {"text": "Customer Success Manager", "country": "FR", "createdAt": ms(24 * 10),
     "categories": {"location": "Paris"}, "hostedUrl": "https://jobs.lever.co/acme/4"},
    {"text": "Marketing Assistant", "country": "FR", "createdAt": ms(3),
     "categories": {"location": "Paris", "commitment": "Stage"}, "hostedUrl": "https://jobs.lever.co/acme/5"},
]
jobs, http = run(cp.fetch_lever, {"acme": "Acme"}, {"acme": Resp(200, lv)})
titles = [j["title"] for j in jobs]
ok(titles == ["Lifecycle Marketing Specialist", "CRM Specialist"],
   f"country=FR 或地點含巴黎、72 小時內、合約不是實習：{titles}")
j = jobs[0]
ok(j["work_mode"] == "hybrid" and j["contract"] == "CDI" and "HubSpot" in j["description"],
   "工作型態、合約、內文（含條列）都帶進來")
ok(http.urls == ["https://api.lever.co/v0/postings/acme"], "打的是公開 Postings API")

print()
print("── 找不到的公司、被擋 ──")
jobs, http = run(cp.fetch_lever, {"nope": "Nope", "acme": "Acme"}, {"acme": Resp(200, lv)})
ok(len(jobs) == 2 and len(http.urls) == 2, "找不到的公司（404）跳過，其他照查")
jobs, http = run(cp.fetch_greenhouse, {"a": "A", "b": "B"},
                 {"a": Resp(403, headers={"server": "cloudflare"}), "b": Resp(200, data)})
ok(jobs == [] and len(http.urls) == 1 and "Greenhouse" in net.BLOCKED, "被擋就整組收工並回報")
jobs, _ = run(cp.fetch_greenhouse, {}, {})
ok(jobs == [], "沒設定公司就不查")

print()
print("全部通過 ✅" if not fail else f"{fail} 項失敗 ❌")


def test_all():
    assert fail == 0


if __name__ == "__main__":
    sys.exit(1 if fail else 0)
