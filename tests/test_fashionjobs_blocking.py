"""Fashion Jobs 被站方擋下時的控制流測試。

這些路徑靠手動測試幾乎抓不到——要嘛需要站方剛好在擋人，要嘛需要暖身請求
剛好失敗。用假 Session 直接驅動控制流。

執行：.venv/bin/python tests/test_fashionjobs_blocking.py
"""
import logging
import pathlib
import sys

logging.basicConfig(level=logging.WARNING, format="   %(message)s")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper.sources import fashionjobs as fj

fail = 0
def ok(c, m):
    global fail
    print(("✅" if c else "❌") + " " + m)
    if not c: fail += 1

class Resp:
    def __init__(self, status=200, text=""): self.status_code, self.text = status, text

class FakeSession:
    """warm_fail_times：暖身前幾次拋例外。"""
    made = []
    def __init__(self, *a, **k): self.warmed = False
    def get(self, url, params=None, headers=None, timeout_seconds=None):
        FakeSession.made.append(url)
        if url == fj.BASE:
            if FakeSession.warm_fail:
                FakeSession.warm_fail -= 1
                raise TimeoutError("暖身請求逾時")
            self.warmed = True
            return Resp(200, "home")
        return Resp(200, "ok") if self.warmed else Resp(403, "blocked")

def reset(warm_fail=0):
    fj._HTTP = None
    FakeSession.made = []
    FakeSession.warm_fail = warm_fail
    fj.Session = FakeSession

# ── 1) 暖身失敗一次後，下一次請求應該重新暖身 ──
print("── 暖身失敗後能否自癒 ──")
reset(warm_fail=1)
try: fj._get("https://fr.fashionjobs.com/x")
except Exception: pass
r2 = fj._get("https://fr.fashionjobs.com/x")
ok(r2.status_code == 200, f"暖身失敗後的下一次請求 → {r2.status_code}（修正前永遠 403）")
ok(fj._HTTP is not None and fj._HTTP.warmed, "_HTTP 只在暖身成功後才被指派")
ok(FakeSession.made.count(fj.BASE) == 2, f"暖身實際執行 {FakeSession.made.count(fj.BASE)} 次（會重試，不是只跑一次）")

# ── 2) blocked_reason ──
print()
print("── 被擋偵測 ──")
cases = [
    (Resp(403, "x"), True, "403"),
    (Resp(429, "x"), True, "429 限流"),
    (Resp(503, "x"), True, "503"),
    (Resp(200, "<title>Just a moment...</title>"), True, "Cloudflare JS 挑戰頁（HTTP 200）"),
    (Resp(403, "<html><head><title>Attention Required! | Cloudflare</title></head></html>"), True,
     "Cloudflare 封鎖頁（真實回應是 403＋title）"),
    (Resp(200, "<html><head><title>Attention Required! | Cloudflare</title></head></html>"), True,
     "同一頁若以 200 回覆，靠 title 仍認得出"),
    (Resp(200, "<html>Enable JavaScript and cookies to continue</html>"), True, "挑戰頁變體"),
    (Resp(200, "<a href='/emploi/acme/job,123.html'>Marketing</a>"), False, "正常職缺列表不誤判"),
    (Resp(404, "x"), False, "404 不算被擋（是真的沒這頁）"),
]
for r, exp, label in cases:
    got = fj.blocked_reason(r) is not None
    ok(got == exp, f"{label} → blocked={got}")

# ── 2b) 暖身當下就被擋 ──
print()
print("── 暖身當下就被擋（回 200 挑戰頁，不拋例外）──")
class WarmBlocked(FakeSession):
    def get(self, url, params=None, headers=None, timeout_seconds=None):
        FakeSession.made.append("暖身" if url == fj.BASE else "列表")
        return Resp(200, "<html><head><title>Just a moment...</title></head></html>")
reset(); fj.Session = WarmBlocked
jobs = fj.fetch(set(), lambda j: 0)
ok(fj._HTTP is None, "被擋的 session 不會被快取（_HTTP 仍為 None）")
ok(FakeSession.made == ["暖身"], f"請求序列 {FakeSession.made}（不會多送列表請求）")
ok(len(jobs) == 0, "回 0 筆且乾淨收工")

# ── 2c) 通用字串誤判防護 ──
print()
print("── 通用英文字串不該誤判 ──")
normal_body = ("<html><head><title>Emplois Marketing | FashionJobs</title></head><body>"
               "Just a moment, we are reviewing applications. Attention required for this role. "
               "<a href='/emploi/acme/job,123.html'>Marketing Manager</a></body></html>")
ok(fj.blocked_reason(Resp(200, normal_body)) is None,
   "內文含 'just a moment'／'attention required' 但 title 正常 → 不誤判")
ok(fj.blocked_reason(Resp(200, "<title>Just a moment...</title>")) is not None,
   "同樣字串出現在 <title> → 判定被擋")
ok(fj.blocked_reason(Resp(200, "<html>var x='/cdn-cgi/challenge-platform/h/b'</html>")) is not None,
   "CF 專屬字串全頁比對仍然生效")

# ── 3) 挑戰頁應該讓整組收工，而不是靜靜回 0 筆 ──
print()
print("── 挑戰頁觸發整組收工 ──")
class ChallengeSession(FakeSession):
    def get(self, url, params=None, headers=None, timeout_seconds=None):
        FakeSession.made.append(url)
        if url == fj.BASE:
            self.warmed = True; return Resp(200, "home")
        return Resp(200, "<title>Just a moment...</title>")
reset(); fj.Session = ChallengeSession
jobs = fj.fetch(set(), lambda j: 0)
list_reqs = [u for u in FakeSession.made if u != fj.BASE]
ok(len(jobs) == 0, "回 0 筆")
ok(len(list_reqs) == 1, f"只打了 {len(list_reqs)} 個列表請求就收工（8 個搜尋詞不會全打完）")

print()
print("失敗:", fail)
sys.exit(1 if fail else 0)
