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

log = logging.getLogger("jobradar.apec")

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


def fetch(search_terms: list[str], results_per_term: int = 40) -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    for term in search_terms:
        # 不指定 sorts：APEC 的 motsCles 是寬鬆 OR 比對，改用日期排序會把
        # 相關性最低的結果推到最前面（實測 'marketing operations' 會回土木、保險職缺）。
        # 預設的相關性排序才拿得到真正對得上的職缺。
        payload = {
            "motsCles": term,
            "pagination": {"range": min(results_per_term, 100), "startIndex": 0},
            "activeFiltre": True,
        }
        try:
            r = requests.post(SEARCH_URL, data=json.dumps(payload), headers=HEADERS, timeout=30)
            if r.status_code in (403, 405):
                log.warning("APEC 回 %s（很可能是 DataDome 反爬），整組跳過", r.status_code)
                return jobs
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning("APEC 搜尋 %r 失敗: %s", term, e)
            continue

        results = _pick(data, "resultats", "offres", "results", default=[])
        if not results and jobs == []:
            log.info("APEC %r 無結果；回應 keys=%s", term, list(data)[:8])
        for o in results:
            oid = str(_pick(o, "numeroOffre", "id", "reference", default=""))
            if not oid or oid in seen:
                continue
            seen.add(oid)
            jobs.append(_to_job(o, oid))
        log.info("APEC %r → %d 筆", term, len(results))
        time.sleep(2)
    return jobs


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
