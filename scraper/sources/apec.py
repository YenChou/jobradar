"""APEC — 站內搜尋背後的 JSON webservice。

APEC 前端是 SPA，搜尋時 POST JSON 到 /cms/webservices/rechercheOffre。
注意：APEC 有 DataDome 反爬保護，機房 IP（GitHub Actions）有機率被擋；
被擋時這個來源會整組跳過並在 log 標示，不影響其他來源。
欄位名稱做了多候選防禦性解析——APEC 改版時看 log 的 sample 即可對症修。
"""
from __future__ import annotations

import json
import logging
import time

import requests

from scraper.util import to_date_str

log = logging.getLogger("chasse.apec")

SEARCH_URL = "https://www.apec.fr/cms/webservices/rechercheOffre"
DETAIL_URL = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{id}"

HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.apec.fr",
    "referer": "https://www.apec.fr/candidat/recherche-emploi.html",
}


def _pick(d: dict, *candidates, default=None):
    """依序嘗試多個候選欄位名（APEC 欄位名不穩定時的防禦）。"""
    for c in candidates:
        if c in d and d[c] not in (None, ""):
            return d[c]
    return default


PAGE_SIZE = 100  # API 上限，給更大的值會被當成無效而退回 20 筆
PAGES = 3


def fetch(search_terms: list[str], results_per_term: int = 100) -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    for term in search_terms:
        for page in range(PAGES):
            if not _page(term, page, jobs, seen, results_per_term):
                break
    return jobs


def _page(term: str, page: int, jobs: list[dict], seen: set[str], results_per_term: int) -> bool:
    """抓一頁；回傳 False 表示這個搜尋詞不用再往下翻。"""
    # 不指定 sorts：APEC 的 motsCles 是寬鬆 OR 比對，改用日期排序會把相關性最低的
    # 結果推到最前面（實測 'marketing operations' 會回土木、保險職缺）。
    # 預設的相關性排序才拿得到真正對得上的職缺。
    size = min(results_per_term, PAGE_SIZE)
    payload = {
        "motsCles": term,
        "pagination": {"range": size, "startIndex": page * size},
        "activeFiltre": True,
    }
    try:
        r = requests.post(SEARCH_URL, data=json.dumps(payload), headers=HEADERS, timeout=30)
        if r.status_code in (403, 405):
            log.warning("APEC 回 %s（很可能是 DataDome 反爬），整組跳過", r.status_code)
            return False
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("APEC 搜尋 %r p%d 失敗: %s", term, page, e)
        return False

    results = _pick(data, "resultats", "offres", "results", default=[])
    if not results and not jobs:
        log.info("APEC %r 無結果；回應 keys=%s", term, list(data)[:8])
    for o in results:
        oid = str(_pick(o, "numeroOffre", "id", "reference", default=""))
        if not oid or oid in seen:
            continue
        seen.add(oid)
        jobs.append(_to_job(o, oid))
    log.info("APEC %r p%d → %d 筆", term, page, len(results))
    time.sleep(2)
    return len(results) >= size  # 不足一頁代表沒有下一頁


def _to_job(o: dict, oid: str) -> dict:
    contrat = _pick(o, "typeContratLibelle", "typeContrat", "contractType")
    if isinstance(contrat, (int, float)):  # 有些欄位是代碼
        contrat = None
    return {
        "title": str(_pick(o, "intitule", "title", default="")),
        "company": str(_pick(o, "nomCommercial", "nomCommercialEntreprise", "enterpriseName", "companyName", default="")),
        "location": str(_pick(o, "lieuTexte", "lieux", "localisation", "location", default="France")),
        "description": str(_pick(o, "texteOffre", "description", "resume", default="")),
        "url": DETAIL_URL.format(id=oid),
        "source": "APEC",
        "date_posted": to_date_str(_pick(o, "datePublication", "dateCreation", "publicationDate")),
        "contract": contrat,
        "work_mode": None,  # pipeline 從描述判斷 télétravail
        "salary": _pick(o, "salaireTexte", "salaire"),
    }
