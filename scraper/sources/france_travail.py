"""France Travail 官方 API（francetravail.io，免費）。

需要環境變數 FT_CLIENT_ID / FT_CLIENT_SECRET（沒有就自動跳過，不影響其他來源）。

註冊步驟（一次性，約 5 分鐘）：
1. 到 https://francetravail.io 註冊帳號
2. 建立一個 application，訂閱「API Offres d'emploi v2」
3. 拿到 client_id / client_secret，設成 GitHub repo secrets：
   FT_CLIENT_ID、FT_CLIENT_SECRET
"""
from __future__ import annotations

import collections
import logging
import os
import time

import requests

from scraper.net import TIMEOUT, Budget, session

from scraper.util import to_date_str

log = logging.getLogger("chasse.francetravail")
HTTP = session()

PAGE_SIZE = 150  # API 單次上限
PAGES = 2
TIME_BUDGET_S = 300

# origineOffre：1＝France Travail 自己收的職缺，2＝合作職缺網站轉來的（API 文件：
# 「collectées par France Travail ou reçues des partenaires」）。APEC 自 2026-10-01 起
# 擋掉 GitHub Actions 的機房 IP（DataDome），它的職缺若經合作管道進到 France Travail，
# 透過這個有金鑰、有授權的官方 API 就拿得到——這不是繞過封鎖，是走對方同意的管道。
# 預設查詢是否已含合作職缺沒有文件可查，所以兩種各查一次，用 id 去重。
ORIGINS = (None, "2")

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
    origins: collections.Counter = collections.Counter()   # 職缺實際來自哪裡，寫進 log
    budget = Budget(TIME_BUDGET_S)
    for term in search_terms:
        for origine in ORIGINS:
            if budget.expired():
                log.warning("France Travail 時間預算用完，%r 之後的搜尋詞跳過", term)
                break
            label = "合作職缺" if origine == "2" else "全部"
            for page in range(PAGES):
                if budget.expired():
                    break
                offers = _search(token, term, max_days_old, page, origine)
                for o in offers:
                    oid = o.get("id")
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                    job = _to_job(o)
                    origins[_origin_label(o)] += 1
                    jobs.append(job)
                log.info("France Travail %r（%s）p%d → %d 筆", term, label, page, len(offers))
                time.sleep(1)
                if len(offers) < PAGE_SIZE:
                    break  # 不足一頁代表沒有下一頁
    # 這行回答「APEC 的職缺有沒有經 France Travail 進來」
    log.info("France Travail 職缺來源分布：%s", dict(origins.most_common()))
    return jobs


def _get_token(client_id: str, client_secret: str) -> str | None:
    try:
        r = HTTP.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": f"api_offresdemploiv2 o2dsoffre",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as e:
        log.warning("France Travail 取得 token 失敗: %s", e)
        return None


def _search(token: str, term: str, max_days_old: int, page: int = 0,
            origine: str | None = None) -> list[dict]:
    lo = page * PAGE_SIZE
    params = {
        "motsCles": term,
        "publieeDepuis": str(min(max_days_old, 31)),  # API 接受 1/3/7/14/31：新鮮度
        "sort": "0",  # 0=關聯度遞減（1=日期、2=距離）。新鮮度已由 publieeDepuis 把關
        "range": f"{lo}-{lo + PAGE_SIZE - 1}",
    }
    if origine:
        params["origineOffre"] = origine
    try:
        r = HTTP.get(
            SEARCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT,
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


def _partner(o: dict) -> dict | None:
    """合作職缺的來源網站（{"nom": "APEC", "url": ...}）；France Travail 自己的職缺回 None。"""
    origine = o.get("origineOffre") or {}
    partners = [p for p in (origine.get("partenaires") or []) if isinstance(p, dict)]
    if str(origine.get("origine")) != "2" and not partners:
        return None
    return partners[0] if partners else {}


def _origin_label(o: dict) -> str:
    p = _partner(o)
    if p is None:
        return "France Travail"
    return f"合作夥伴 {p.get('nom') or '（未具名）'}"


def _to_job(o: dict) -> dict:
    lieu = o.get("lieuTravail") or {}
    contrat = o.get("typeContrat") or None  # CDI / CDD / MIS(intérim)…
    if contrat == "MIS":
        contrat = "Intérim"
    partner = _partner(o)
    origine = o.get("origineOffre") or {}
    # 合作職缺連到原站（例如 apec.fr 的那則職缺），France Travail 自己的連到它的職缺頁
    url = ((partner or {}).get("url") or origine.get("urlOrigine")
           or f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}")
    # APEC 的職缺標成 APEC：網站的 APEC 篩選與統計照舊適用，使用者不必知道它是繞
    # France Travail 進來的。其他合作網站維持 France Travail，免得來源 chip 一直變多。
    source = "APEC" if partner and "apec" in (partner.get("nom") or "").lower() else "France Travail"
    return {
        "title": o.get("intitule") or "",
        "company": (o.get("entreprise") or {}).get("nom") or "",
        "location": lieu.get("libelle") or "France",
        "description": o.get("description") or "",
        "url": url,
        "source": source,
        "date_posted": to_date_str(o.get("dateCreation")),
        "contract": contrat,
        "work_mode": None,  # 由 pipeline 從描述判斷 télétravail 字樣
        "salary": (o.get("salaire") or {}).get("libelle"),
    }
