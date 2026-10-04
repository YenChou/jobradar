"""站方擋人時的處理：net.blocked_reason 的判斷，以及 APEC 被擋就整組收工。

被擋的路徑靠手動測試抓不到——要站方剛好在擋人。用假 HTTP 直接驅動控制流。

執行（兩種都可以，CI 用第一種）：
    .venv/bin/python tests/test_apec_blocking.py
    .venv/bin/python -m pytest tests/test_apec_blocking.py
"""
import contextlib
import logging
import pathlib
import sys
import types

logging.basicConfig(level=logging.CRITICAL)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper import main, net
from scraper.sources import apec

TERMS = [f"terme {i}" for i in range(22)]


@contextlib.contextmanager
def patched(obj, **attrs):
    """暫時替換屬性，結束後一定還原——不能讓測試把真的 time.sleep 等東西換掉不還。"""
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


class Resp:
    def __init__(self, status=200, data=None, headers=None, text=None):
        self.status_code, self.headers = status, headers or {}
        self._data = data
        self.text = text if text is not None else ("{}" if data is not None else "")

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def ok_page(n):
    """正常的一頁：1 筆職缺（< 頁大小，所以每個詞只抓一頁）。"""
    return Resp(200, {"resultats": [{"numeroOffre": f"id-{n}", "intitule": "Chargé CRM"}]})


class FakeHTTP:
    """responses：依序回應；用完之後一律回最後一個。元素可以是 Resp、Exception 或 "ok"。"""
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls += 1
        r = self.responses[min(self.calls, len(self.responses)) - 1]
        if isinstance(r, Exception):
            raise r
        return ok_page(self.calls) if r == "ok" else r


def run_apec(responses):
    """用假 HTTP 跑一輪 APEC；回傳 (職缺, 請求數, 登記的被擋原因)。"""
    http = FakeHTTP(responses)
    net.BLOCKED.clear()
    with patched(apec, HTTP=http, time=types.SimpleNamespace(sleep=lambda s: None)), \
            contextlib.redirect_stdout(None):   # report_blocked 會印 GitHub annotation
        jobs = apec.fetch(TERMS)
    return jobs, http.calls, net.BLOCKED.get("APEC")


# ── net.blocked_reason：什麼算被擋 ──────────────────────────

def test_blocked_status_codes():
    """403／405／429／503 都算被擋；404、500 不算"""
    for code in (403, 405, 429, 503):
        assert net.blocked_reason(Resp(code, text="x")), code
    for code in (404, 500):
        assert net.blocked_reason(Resp(code, text="x")) is None, code


def test_challenge_pages_with_200():
    """回 200 的挑戰頁（DataDome、Cloudflare）也算被擋，正常回應不算"""
    dd = net.blocked_reason(Resp(200, text="<script src='https://geo.captcha-delivery.com/c.js'>"))
    assert dd and "DataDome" in dd, dd
    cf = net.blocked_reason(Resp(200, text="<title>Just a moment...</title>"))
    assert cf and "Cloudflare" in cf, cf
    assert net.blocked_reason(Resp(200, {"resultats": []})) is None
    assert net.blocked_reason(Resp(200, text="<a href='/emploi/x,1.html'>Marketing</a>")) is None


def test_vendor_named_in_reason():
    """原因要寫出是誰擋的：DataDome 從 header 認得出來"""
    r = net.blocked_reason(Resp(403, headers={"X-DataDome": "protected"}, text="x"))
    assert r == "HTTP 403（DataDome）", r
    assert net.blocked_reason(Resp(403, headers={"Server": "nginx"}, text="x")) == "HTTP 403"


# ── APEC：被擋就整組收工 ──────────────────────────────────

def test_apec_stops_on_first_block():
    """403／405／429／503 第一次出現就收工：只送 1 個請求（修正前 403 送 22 個、429 最多 66 個）"""
    for code in (403, 405, 429, 503):
        jobs, calls, reason = run_apec([Resp(code, text="blocked")])
        assert calls == 1, (code, calls)
        assert jobs == [], code
        assert reason and str(code) in reason, (code, reason)


def test_apec_stops_on_challenge_page():
    """DataDome 回 200 的驗證頁、或任何 HTML 頁（這是 JSON API）都整組收工"""
    for page in ("<html><script src='https://geo.captcha-delivery.com/c.js'></script></html>",
                 "<!doctype html><html><body>Vérification en cours…</body></html>"):
        jobs, calls, reason = run_apec([Resp(200, text=page)])
        assert calls == 1 and jobs == [] and reason, (page[:30], calls, reason)


def test_apec_keeps_jobs_found_before_block():
    """抓到一半才被擋：之前抓到的保留"""
    jobs, calls, reason = run_apec(["ok", "ok", Resp(403, text="x")])
    assert calls == 3, calls
    assert len(jobs) == 2, len(jobs)
    assert reason and "剩下 19 個搜尋詞不送" in reason, reason


def test_apec_other_errors_skip_one_term_only():
    """500、連線失敗只跳過那一個詞，不整組收工，也不登記成被擋"""
    for first in (Resp(500, text="oops"), ConnectionError("dns")):
        jobs, calls, reason = run_apec([first, "ok"])
        assert calls == len(TERMS), (first, calls)
        assert len(jobs) == len(TERMS) - 1, (first, len(jobs))
        assert reason is None, (first, reason)


def test_scrape_all_reports_blocked_sources():
    """main.scrape_all 每輪先清空登記，被擋的來源會出現在 net.BLOCKED（寫進 jobs.json）"""
    from scraper.sources import france_travail, indeed_jobspy, isarta, wttj
    net.BLOCKED["舊的"] = "上一輪留下的"
    stub = lambda *a, **k: []
    with patched(indeed_jobspy, fetch=stub), patched(wttj, fetch=stub), \
            patched(france_travail, fetch=stub), patched(isarta, fetch=stub), \
            patched(apec, HTTP=FakeHTTP([Resp(403, text="x")]),
                    time=types.SimpleNamespace(sleep=lambda s: None)), \
            contextlib.redirect_stdout(None):
        raw, stats = main.scrape_all(main.load_cfg(), set())
    assert stats["APEC"] == 0
    assert list(net.BLOCKED) == ["APEC"], net.BLOCKED
    net.BLOCKED.clear()


def test_patches_are_restored():
    """測試不會把真的 time.sleep、apec.HTTP 換掉不還"""
    import time
    assert apec.time is time and time.sleep.__module__ == "time"
    assert isinstance(apec.HTTP, type(net.session()))


if __name__ == "__main__":
    fail = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("✅", fn.__doc__)
            except AssertionError as e:
                fail += 1
                print("❌", fn.__doc__, "→", e)
    print(f"\n失敗: {fail}")
    sys.exit(1 if fail else 0)
