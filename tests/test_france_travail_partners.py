"""France Travail 的合作職缺（origineOffre=2）：APEC 被 DataDome 擋掉之後，它的職缺
若經合作管道進到 France Travail，要能透過官方 API 拿到，並標成 APEC。

真的 API 需要金鑰、開發環境也連不到，用假 HTTP 直接驅動。

執行（兩種都可以）：
    .venv/bin/python tests/test_france_travail_partners.py
    .venv/bin/python -m pytest tests/test_france_travail_partners.py
"""
import contextlib
import logging
import os
import pathlib
import sys
import types

logging.basicConfig(level=logging.CRITICAL)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper.sources import france_travail as ft


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


def offer(oid, partner=None, title="Chargé CRM"):
    o = {"id": oid, "intitule": title, "entreprise": {"nom": "Acme"},
         "lieuTravail": {"libelle": "75 - Paris"}, "description": "CRM, emailing",
         "dateCreation": "2026-10-06T10:00:00Z"}
    if partner:
        o["origineOffre"] = {"origine": "2", "urlOrigine": f"https://ft.example/{oid}",
                             "partenaires": [{"nom": partner, "url": f"https://{partner.lower()}.example/{oid}"}]}
    else:
        o["origineOffre"] = {"origine": "1",
                             "urlOrigine": f"https://candidat.francetravail.fr/offres/recherche/detail/{oid}"}
    return o


class Resp:
    def __init__(self, data):
        self.status_code, self._data = 200, data
    def json(self): return self._data
    def raise_for_status(self): pass


class FakeHTTP:
    """一般查詢回 France Travail 自己的職缺；origineOffre=2 回合作職缺。記下每次查詢的參數。"""
    def __init__(self, own, partner):
        self.own, self.partner, self.params = own, partner, []
    def post(self, url, data=None, timeout=None):
        return Resp({"access_token": "t"})
    def get(self, url, params=None, headers=None, timeout=None):
        self.params.append(dict(params))
        return Resp({"resultats": self.partner if params.get("origineOffre") == "2" else self.own})


def run(own, partner, terms=("crm",)):
    http = FakeHTTP(own, partner)
    env = {"FT_CLIENT_ID": "id", "FT_CLIENT_SECRET": "secret"}
    with patched(ft, HTTP=http, time=types.SimpleNamespace(sleep=lambda s: None)), \
            patched(os, environ={**os.environ, **env}):
        jobs = ft.fetch(list(terms))
    return jobs, http


def test_partner_query_is_sent():
    """每個搜尋詞都另外查一次合作職缺（origineOffre=2）"""
    _, http = run([offer("1")], [])
    assert [p.get("origineOffre") for p in http.params] == [None, "2"], http.params


def test_apec_partner_offer_is_labelled_apec():
    """經 France Travail 轉來的 APEC 職缺：來源標 APEC、連到 APEC 原站"""
    jobs, _ = run([], [offer("A1", partner="APEC")])
    assert len(jobs) == 1
    assert jobs[0]["source"] == "APEC", jobs[0]["source"]
    assert jobs[0]["url"] == "https://apec.example/A1", jobs[0]["url"]


def test_other_partners_stay_france_travail():
    """其他合作網站維持 France Travail 標籤（來源 chip 不會一直變多），但連到原站"""
    jobs, _ = run([], [offer("H1", partner="HelloWork")])
    assert jobs[0]["source"] == "France Travail"
    assert jobs[0]["url"] == "https://hellowork.example/H1"


def test_own_offers_unchanged():
    """France Travail 自己的職缺照舊：來源 France Travail、連到它的職缺頁"""
    jobs, _ = run([offer("1")], [])
    assert jobs[0]["source"] == "France Travail"
    assert jobs[0]["url"].startswith("https://candidat.francetravail.fr/"), jobs[0]["url"]


def test_offers_returned_by_both_queries_are_not_duplicated():
    """一般查詢若本來就含合作職缺，兩次查詢重疊的部分用 id 去重"""
    apec = offer("A1", partner="APEC")
    jobs, _ = run([offer("1"), apec], [apec])
    assert sorted(j["source"] for j in jobs) == ["APEC", "France Travail"], jobs


def test_origin_breakdown_logged():
    """log 要寫出職缺來源分布，一看就知道 APEC 有沒有經 France Travail 進來"""
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    ft.log.addHandler(handler)
    old_level = ft.log.level
    ft.log.setLevel(logging.INFO)
    try:
        run([offer("1")], [offer("A1", partner="APEC")])
    finally:
        ft.log.removeHandler(handler)
        ft.log.setLevel(old_level)
    summary = [r.getMessage() for r in records if "來源分布" in r.getMessage()]
    assert summary and "合作夥伴 APEC" in summary[0] and "France Travail" in summary[0], summary


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
