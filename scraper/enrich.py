"""過濾、分類、加權、去重 — pipeline 的核心邏輯。

規則全部來自 keywords.yml（見規劃書第 3 節）：
- 職稱關鍵字 → 分類（一個職缺可屬多類；職稱沒中再用描述補判）
- 技能關鍵字 → 描述加分
- Stage/Alternance → 硬性排除
- 職級/合約 → 加分排序（非硬過濾）；Senior/Director → 降權
- 西部城市 +10、Remote +6、其他城市 +2
"""
from __future__ import annotations

import re

from scraper.util import job_id, norm, norm_title_for_dedupe

FLAG_PAT = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")  # 國旗 emoji
FR_FLAG = "\U0001F1EB\U0001F1F7"  # 🇫🇷
REMOTE_PAT = re.compile(r"\b(remote|teletravail|full remote|100% remote)\b")
HYBRID_PAT = re.compile(r"\b(hybride|hybrid|teletravail partiel)\b")
CONTRACT_PAT = re.compile(r"\b(cdi|cdd|interim)\b")


def classify(job: dict, cfg: dict) -> dict | None:
    """回傳補完欄位的職缺；被硬性排除時回傳 None。"""
    title_n = norm(job.get("title"))
    desc_n = norm(job.get("description"))
    text_n = f"{title_n} \n {desc_n}"
    loc_n = norm(job.get("location"))

    # 1) 硬性排除：Stage/Alternance 與針對其他國家市場的職缺
    if excluded_title(job.get("title") or "", cfg):
        return None

    # 2) 分類：職稱優先，職稱沒中用描述前段補判
    categories: list[str] = []
    skills_hit: list[str] = []
    score = 0
    for key, cat in cfg["categories"].items():
        matched = any(kw in title_n for kw in cat["title_keywords"])
        if not matched:
            # 描述補判：標題泛稱（如 "Marketing Specialist"）但描述明確時仍可歸類
            matched = sum(1 for kw in cat["title_keywords"] if kw in desc_n) >= 1 and "marketing" in text_n
        if matched:
            categories.append(key)
            hits = [kw for kw in cat["skill_keywords"] if kw in desc_n]
            skills_hit += hits
            score += min(len(hits) * 2, 10)
    if not categories:
        return None  # 五類都沒中 → 不收

    # 3) 附加標籤（影音內容、需中文）。比對職稱＋描述——語言要求常寫在職稱裡
    #    （例如 "Marketing Strategy Manager - Mandarin speaker"）。
    bonus_tags = [
        key
        for key, tag in cfg.get("bonus_tags", {}).items()
        if any(kw in text_n for kw in tag["skill_keywords"])
    ]

    # 4) 職級加分／降權
    if any(kw in title_n for kw in cfg["seniority_boost_title"]):
        score += 6
    if any(kw in title_n for kw in cfg["seniority_penalty_title"]):
        score -= 8

    # 5) 合約：來源欄位優先，否則從文字判斷
    contract = _detect_contract(job.get("contract"), text_n)
    if contract:
        score += 4

    # 6) 工作型態：來源欄位優先，否則從文字判斷；預設 onsite
    work_mode = job.get("work_mode") or _detect_work_mode(text_n) or "onsite"

    # 7) 地區加權
    city, region = _detect_region(loc_n, cfg)
    if region == "west":
        score += cfg.get("west_boost", 10)
    elif work_mode == "remote":
        score += cfg.get("remote_boost", 6)
    elif region == "other":
        score += cfg.get("other_city_boost", 2)

    desc = (job.get("description") or "").strip()
    # 公司名缺失時用 URL 當識別，避免不同公司同職稱被誤併
    company_key = (job.get("company") or "").strip() or job.get("url", "")
    return {
        "id": job_id(company_key, job.get("title", "")),
        "_dedupe_key": company_key,
        "title": (job.get("title") or "").strip(),
        "company": (job.get("company") or "").strip(),
        "location": (job.get("location") or "").strip(),
        "city": city,
        "region": region,  # west / paris / other / unknown
        "work_mode": work_mode,  # remote / hybrid / onsite
        "contract": contract,
        "categories": categories,
        "skills": sorted(set(skills_hit)),
        "bonus_tags": bonus_tags,
        "score": score,
        "date_posted": job.get("date_posted"),
        "salary": job.get("salary"),
        "description_snippet": re.sub(r"\s+", " ", desc)[:400],
        "sources": [{"name": job["source"], "url": job.get("url", "")}],
    }


def excluded_title(title: str, cfg: dict) -> bool:
    """職稱層級的硬性排除。也用在 main.py 清洗歷史資料，
    所以規則更新後，既有的 jobs.json 也會在下一次執行時被重新過濾。"""
    title_n = norm(title)

    # Stage / Alternance
    for kw in cfg["exclude_title"]:
        if kw in title_n:
            return True

    # 同樣是實習／建教，但寫成縮寫（Stg - / Alt - ）。整字比對，避免誤殺。
    for kw in cfg.get("exclude_title_abbrev", []):
        if re.search(rf"\b{re.escape(kw)}\b", title_n):
            return True

    # 針對其他國家市場的職缺（法國公司替海外市場開缺會掛在巴黎辦公室下，
    # 騙過來源端的國家過濾）。職稱同時提到 France 就不套用（如 "France & BENELUX"）。
    mentions_fr = FR_FLAG in title or re.search(r"\bfrance\b|\bfrancais|\bfr\b", title_n)
    if not mentions_fr:
        for fl in FLAG_PAT.findall(title):
            if fl != FR_FLAG:
                return True
        for kw in cfg.get("exclude_title_foreign", []):
            if re.search(rf"\b{re.escape(kw)}\b", title_n):
                return True
    return False


def _detect_contract(raw, text_n: str) -> str | None:
    if raw:
        r = norm(raw)
        if "cdi" in r or "full" in r or "permanent" in r:
            return "CDI"
        if "cdd" in r or "temporary" in r or "fixed" in r:
            return "CDD"
        if "interim" in r:
            return "Intérim"
    m = CONTRACT_PAT.search(text_n)
    if m:
        return {"cdi": "CDI", "cdd": "CDD", "interim": "Intérim"}[m.group(1)]
    return None


def _detect_work_mode(text_n: str) -> str | None:
    if HYBRID_PAT.search(text_n):
        return "hybrid"
    if REMOTE_PAT.search(text_n):
        return "remote"
    if "presentiel" in text_n:
        return "onsite"
    return None


def _detect_region(loc_n: str, cfg: dict) -> tuple[str, str]:
    if not loc_n:
        return "", "unknown"
    for city in cfg["west_cities"]:
        if city in loc_n:
            return city.title(), "west"
    if "paris" in loc_n or "ile-de-france" in loc_n or "la defense" in loc_n:
        return "Paris", "paris"
    if "france" == loc_n or "remote" in loc_n:
        return "", "unknown"
    city = loc_n.split(",")[0].split("(")[0].strip().title()
    return city, "other"


def dedupe(jobs: list[dict]) -> list[dict]:
    """同公司＋正規化職稱視為同一職缺，合併來源連結。"""
    merged: dict[str, dict] = {}
    for j in jobs:
        key = f"{norm(j.pop('_dedupe_key', j['company']))}|{norm_title_for_dedupe(j['title'])}"
        if key in merged:
            m = merged[key]
            known = {s["name"] for s in m["sources"]}
            m["sources"] += [s for s in j["sources"] if s["name"] not in known]
            # 保留較完整的欄位與較早的發布日
            for f in ("date_posted", "salary", "contract", "city"):
                if not m.get(f) and j.get(f):
                    m[f] = j[f]
            if len(j.get("description_snippet", "")) > len(m.get("description_snippet", "")):
                m["description_snippet"] = j["description_snippet"]
            m["categories"] = sorted(set(m["categories"]) | set(j["categories"]))
            m["skills"] = sorted(set(m["skills"]) | set(j["skills"]))
            m["score"] = max(m["score"], j["score"])
        else:
            merged[key] = j
    out = list(merged.values())
    for j in out:
        j["score"] += (len(j["sources"]) - 1) * 2  # 多平台上架 = 熱門訊號，小幅加分
    return out
