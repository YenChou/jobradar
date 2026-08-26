"""France Travail 官方 API（francetravail.io，免費）。

需要環境變數 FT_CLIENT_ID / FT_CLIENT_SECRET（沒有就自動跳過，不影響其他來源）。

註冊步驟（一次性，約 5 分鐘）：
1. 到 https://francetravail.io 註冊帳號
2. 建立一個 application，訂閱「API Offres d'emploi v2」
3. 拿到 client_id / client_secret，設成 GitHub repo secrets：
   FT_CLIENT_ID、FT_CLIENT_SECRET
"""
from __future__ import annotations

import logging
import os
import time

import requests

from scraper.util import to_date_str

log = logging.getLogger("chasse.francetravail")

PAGE_SIZE = 150  # API 單次上限
PAGES = 2

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"


def fetch(search_terms: list[str], max_days_old: int = 7) -> list[dict]:
    client_id = os.environ.get("FT_CLIENT_ID")
    client_secret = os.environ.get("FT_CLIENT_SECRET")
    if not client_id or not client_secret:
        log.info("France Travail：未設定 FT_CLIENT_ID / FT_CLIENT_SECRET，跳過（其他來源照常）")
        return []

    token = _get_token(client_id, client_secret)
    if not token:
        return []

    jobs: list[dict] = []
    seen_ids: set[str] = set()
    for term in search_terms:
        for page in range(PAGES):
            offers = _search(token, term, max_days_old, page)
            for o in offers:
                oid = o.get("id")
                if oid in seen_ids:
                    continue
                seen_ids.add(oid)
                jobs.append(_to_job(o))
            log.info("France Travail %r p%d → %d 筆", term, page, len(offers))
            time.sleep(1)
            if len(offers) < PAGE_SIZE:
                break  # 不足一頁代表沒有下一頁
    return jobs


def _get_token(client_id: str, client_secret: str) -> str | None:
    try:
        r = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": f"api_offresdemploiv2 o2dsoffre",
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as e:
        log.warning("France Travail 取得 token 失敗: %s", e)
        return None


def _search(token: str, term: str, max_days_old: int, page: int = 0) -> list[dict]:
    lo = page * PAGE_SIZE
    params = {
        "motsCles": term,
        "publieeDepuis": str(min(max_days_old, 31)),  # API 接受 1/3/7/14/31：新鮮度
        "sort": "0",  # 0=關聯度遞減（1=日期、2=距離）。新鮮度已由 publieeDepuis 把關
        "range": f"{lo}-{lo + PAGE_SIZE - 1}",
    }
    try:
        r = requests.get(
            SEARCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if r.status_code == 204:  # 無結果
            return []
        if r.status_code == 206:  # 部分內容：正常，代表還有更多筆
            return r.json().get("resultats", [])
        r.raise_for_status()
        return r.json().get("resultats", [])
    except Exception as e:
        log.warning("France Travail 搜尋 %r 失敗: %s", term, e)
        return []


def _to_job(o: dict) -> dict:
    lieu = o.get("lieuTravail") or {}
    contrat = o.get("typeContrat") or None  # CDI / CDD / MIS(intérim)…
    if contrat == "MIS":
        contrat = "Intérim"
    return {
        "title": o.get("intitule") or "",
        "company": (o.get("entreprise") or {}).get("nom") or "",
        "location": lieu.get("libelle") or "France",
        "description": o.get("description") or "",
        "url": (o.get("origineOffre") or {}).get("urlOrigine")
        or f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}",
        "source": "France Travail",
        "date_posted": to_date_str(o.get("dateCreation")),
        "contract": contrat,
        "work_mode": None,  # 由 pipeline 從描述判斷 télétravail 字樣
        "salary": (o.get("salaire") or {}).get("libelle"),
    }
