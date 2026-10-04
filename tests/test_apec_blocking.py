"""APEC 被站方擋下（403／405）時要整組收工，不能每個搜尋詞都再打一次。

被擋的路徑靠手動測試抓不到——要站方剛好在擋人。用假 HTTP 直接驅動控制流。

執行：.venv/bin/python tests/test_apec_blocking.py
"""
import logging
import pathlib
import sys

logging.basicConfig(level=logging.WARNING, format="   %(message)s")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper.sources import apec

apec.time.sleep = lambda s: None   # 測試不必真的等

fail = 0
def ok(c, m):
    global fail
    print(("✅" if c else "❌") + " " + m)
    if not c: fail += 1

class Resp:
    def __init__(self, status, data=None, headers=None, text=""):
        self.status_code, self._data = status, data or {}
        self.headers, self.text = headers or {}, text
    def json(self): return self._data
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")

class FakeHTTP:
    """statuses：依序回應的狀態碼；用完之後一律回最後一個。"""
    def __init__(self, statuses, results=1):
        self.statuses, self.results, self.calls = list(statuses), results, 0
    def post(self, url, data=None, headers=None, timeout=None):
        self.calls += 1
        st = self.statuses[min(self.calls, len(self.statuses)) - 1]
        offers = [{"numeroOffre": f"{self.calls}-{k}", "intitule": "Chargé CRM"} for k in range(self.results)]
        return Resp(st, {"resultats": offers})

TERMS = [f"terme {i}" for i in range(22)]

print("── 第一個請求就被擋 ──")
for code in (403, 405):
    apec.HTTP = FakeHTTP([code])
    jobs = apec.fetch(TERMS)
    ok(apec.HTTP.calls == 1, f"{code}：只送 1 個請求就收工（修正前會送 22 個），實際 {apec.HTTP.calls}")
    ok(jobs == [], f"{code}：沒有職缺")

print()
print("── 抓到一半才被擋：已抓到的保留 ──")
apec.HTTP = FakeHTTP([200, 200, 403])   # 每頁 1 筆 < 頁大小 → 每個詞只抓一頁
jobs = apec.fetch(TERMS)
ok(apec.HTTP.calls == 3, f"前兩個詞正常、第三個被擋 → 共 3 個請求，實際 {apec.HTTP.calls}")
ok(len(jobs) == 2, f"被擋之前的 2 筆保留，實際 {len(jobs)}")

print()
print("── 其他錯誤只跳過那個詞，不整組收工 ──")
apec.HTTP = FakeHTTP([500, 200])
jobs = apec.fetch(TERMS)
ok(apec.HTTP.calls == len(TERMS), f"500 只影響第一個詞，22 個詞都有送，實際 {apec.HTTP.calls}")
ok(len(jobs) == len(TERMS) - 1, f"其餘 21 個詞的職缺都在，實際 {len(jobs)}")

print()
print("── log 要寫出是不是 DataDome ──")
ok("確認是 DataDome" in apec._block_signature(Resp(403, headers={"X-DataDome": "protected"})), "x-datadome header → DataDome")
ok("確認是 DataDome" in apec._block_signature(Resp(403, text="<script src='https://geo.captcha-delivery.com/x'>")),
   "captcha-delivery.com 頁面 → DataDome")
sig = apec._block_signature(Resp(403, headers={"Server": "nginx"}, text="Forbidden"))
ok("不像 DataDome" in sig and "nginx" in sig, f"其他來源的 403 → {sig}")

print()
print(f"失敗: {fail}")
sys.exit(1 if fail else 0)
