"""Jo's Chasse — 每日抓取主程式。

用法：
    python -m scraper.main            # 正常抓取（Actions 每天跑這個）
    python -m scraper.main --demo     # 不上網，用內建示範資料跑完整 pipeline（本地看網站用）

環境變數（都可不設）：
    HOURS_OLD=72          Indeed 抓最近幾小時的職缺（首次建議 168）
    RESULTS_PER_TERM=50   每個搜尋詞抓幾筆
    FT_CLIENT_ID / FT_CLIENT_SECRET   France Travail API 金鑰（沒有就跳過該來源）
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from scraper import enrich, net
from scraper.util import PARIS, norm, paris_today, utcnow_iso

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "jobs.json"
# 本機抓的 APEC（scraper/local_apec.py）。雲端被 APEC 擋下，改在使用者電腦上抓再推上來。
LOCAL_APEC = ROOT / "docs" / "data" / "apec.json"
LOCAL_MAX_AGE_H = 48   # 超過就不併：電腦好幾天沒開時，別把舊結果當成這一輪抓到的
# 這一輪併進來的本機資料 → {"generated_at", "count"}；main() 寫進 jobs.json 的 local_sources
LOCAL_SOURCES: dict[str, dict] = {}
RETENTION_DAYS = 30

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chasse")


def load_cfg() -> dict:
    with open(ROOT / "keywords.yml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 關鍵字全部先正規化（去重音、小寫），比對時才一致
    for cat in cfg["categories"].values():
        cat["title_keywords"] = [norm(k) for k in cat["title_keywords"]]
        cat["skill_keywords"] = [norm(k) for k in cat["skill_keywords"]]
        cat["exclude_title"] = [norm(k) for k in cat.get("exclude_title") or []]
    for tag in cfg.get("bonus_tags", {}).values():
        # `or []`：清單項目全被註解掉時 YAML 給的是 None，不擋的話整輪抓取會直接崩潰
        for key in ("skill_keywords", "keep_keywords", "ignore_phrases"):
            tag[key] = [norm(k) for k in tag.get(key) or []]
        # keep_patterns 是正規表示式，不能過 norm（會壓掉空白等）；本來就該寫成小寫、無重音
        tag["keep_patterns"] = tag.get("keep_patterns") or []
        tag["_keep_rx"] = enrich.keep_regexes(tag)
    for key in ("exclude_title", "exclude_title_foreign", "exclude_title_abbrev",
                "seniority_boost_title",
                "seniority_penalty_title", "contract_boost", "west_cities"):
        cfg[key] = [norm(k) for k in cfg.get(key, [])]
    return cfg


def scrape_all(cfg: dict, known_urls: set[str]) -> tuple[list[dict], dict]:
    from scraper.sources import apec, france_travail, indeed_jobspy, isarta, wttj

    hours_old = int(os.environ.get("HOURS_OLD", "72"))
    per_term = int(os.environ.get("RESULTS_PER_TERM", "50"))

    terms = cfg["search_terms"]
    net.BLOCKED.clear()   # 各來源被擋時會用 net.report_blocked 登記；main() 寫進 jobs.json

    raw: list[dict] = []
    stats: dict[str, int] = {}
    for name, fn in [
        ("Indeed", lambda: indeed_jobspy.fetch(terms, hours_old, per_term)),
        ("Welcome to the Jungle", lambda: wttj.fetch(terms)),
        ("France Travail", lambda: france_travail.fetch(terms, max_days_old=max(1, hours_old // 24))),
        ("APEC", lambda: apec.fetch(terms, results_per_term=per_term)),
        # Fashion Jobs 已移除，詳見 README「注意」與模組 docstring。
        # 要重新啟用的話，除了這行註冊，還要把 detail_priority()（排詳情頁名額
        # 順序用）加回來——git log 找 remove-fashionjobs 那個 commit。
        ("Isarta", lambda: isarta.fetch(known_urls)),
    ]:
        try:
            batch = fn()
        except Exception as e:  # 一個來源整組失敗也不中斷其他來源
            log.error("%s 整體失敗: %s", name, e)
            batch = []
        stats[name] = len(batch)
        raw += batch

    LOCAL_SOURCES.clear()
    local = local_apec()
    if local:
        raw += local["jobs"]
        stats["APEC"] = stats.get("APEC", 0) + len(local["jobs"])
        LOCAL_SOURCES["APEC"] = {"generated_at": local["generated_at"], "count": len(local["jobs"])}
    return raw, stats


def local_apec() -> dict | None:
    """讀本機推上來的 APEC 結果；沒有、讀不了或太舊就回 None。"""
    try:
        d = json.loads(LOCAL_APEC.read_text(encoding="utf-8"))
        generated = datetime.strptime(d["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("本機 APEC 資料讀不了，略過：%s", e)
        return None
    age_h = (datetime.now(timezone.utc) - generated).total_seconds() / 3600
    if age_h > LOCAL_MAX_AGE_H:
        log.warning("本機 APEC 資料是 %.0f 小時前的（上限 %d），略過——本機排程可能沒在跑", age_h, LOCAL_MAX_AGE_H)
        return None
    log.info("併入本機 APEC 資料：%d 筆（%s）", len(d.get("jobs", [])), d["generated_at"])
    return {"generated_at": d["generated_at"], "jobs": d.get("jobs", [])}


def demo_jobs() -> tuple[list[dict], dict]:
    with open(ROOT / "scraper" / "demo_data.json", encoding="utf-8") as f:
        raw = json.load(f)
    return raw, {"Demo": len(raw)}


def _desc(datestr: str | None) -> str:
    """日期字串反向排序鍵（新在前；缺日期排最後）。"""
    if not datestr:
        return "9999"
    return "".join(chr(255 - ord(c)) for c in datestr)


def main() -> int:
    demo = "--demo" in sys.argv
    cfg = load_cfg()

    # 先讀既有資料：讓列表型來源知道哪些職缺「已經補過詳情頁」，省去重抓。
    # 只收 date_posted 有值的——列表型來源的詳情頁預算有限，當天沒補到的
    # 職缺會缺日期／地點，下次執行要能再排進預算，否則永遠補不完。
    known_urls: set[str] = set()
    if OUT.exists():
        try:
            for j in json.loads(OUT.read_text(encoding="utf-8")).get("jobs", []):
                # date_assumed 的日期是推定值、不代表詳情頁補過，仍要重排進預算
                if j.get("date_posted") and not j.get("date_assumed"):
                    known_urls.update(s.get("url", "") for s in j.get("sources", []))
        except (json.JSONDecodeError, KeyError):
            pass

    raw, stats = demo_jobs() if demo else scrape_all(cfg, known_urls)
    log.info("原始抓到 %d 筆：%s", len(raw), stats)
    blocked = {} if demo else dict(net.BLOCKED)
    if blocked:
        log.warning("被站方擋下的來源：%s", blocked)

    # 分類 + 過濾
    kept = [j for j in (enrich.classify(r, cfg) for r in raw) if j]
    log.info("過濾後 %d 筆（排除 Stage/Alternance，以及職類全沒中又沒明確要求中文者）", len(kept))

    # 本次去重
    kept = enrich.dedupe(kept)

    # 與歷史合併（保留 first_seen，讓「最近24小時／7天」視圖有依據）
    today = paris_today()
    history = {}
    if OUT.exists():
        try:
            history = {j["id"]: j for j in json.loads(OUT.read_text(encoding="utf-8")).get("jobs", [])}
        except (json.JSONDecodeError, KeyError):
            log.warning("既有 jobs.json 無法解析，視為首次執行")

    for j in kept:
        old = history.get(j["id"])
        j["first_seen"] = old.get("first_seen", today) if old else today
        # kept 裡的 j 是這次重新分類出來的新物件，會整筆蓋掉舊紀錄。列表型來源
        # 這次若沒花詳情頁名額，date_posted 會是 None，下面就會被補成推定值——
        # 那不只讓畫面從真日期退化成「約」，還會讓下次執行把它當成「沒補過」而
        # 重抓同一頁，來回無限循環。所以先把上次拿到的真實公告日接回來。
        if old and not j.get("date_posted") and not old.get("date_assumed"):
            j["date_posted"] = old.get("date_posted")
        history[j["id"]] = j

    # 30 天過期下架 + 用「目前的」排除規則重新清洗歷史（規則更新即回溯生效）
    cutoff = (datetime.now(PARIS) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    jobs = [
        j for j in history.values()
        if (j.get("first_seen") or today) >= cutoff
        and not enrich.excluded_title(j.get("title", ""), cfg,
                                      requires_chinese="chinese" in (j.get("bonus_tags") or []))
    ]

    # 來源沒給公告日：假設「首次抓到的那天」就是公告日，並標記成推定值，
    # 讓前端顯示成「約 YYYY-MM-DD」而不是冒充精確資料。
    # 放在這裡而不是上面的迴圈，是為了連同「這次沒重抓到的歷史紀錄」一起補。
    for j in jobs:
        if not j.get("date_posted"):
            j["date_posted"] = j.get("first_seen") or today
            j["date_assumed"] = True

    # 排序：分數高在前，同分者新的在前
    jobs.sort(key=lambda j: (-j["score"], _desc(j.get("first_seen")), _desc(j.get("date_posted"))))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated_at": utcnow_iso(),
                "source_stats": stats,
                # 被擋的來源 → 原因。網站據此標「被擋」，免得和「今天剛好 0 筆」混在一起
                "blocked_sources": blocked,
                # 改在本機抓、這一輪併進來的來源（APEC），網站據此顯示「本機」而不是「被擋」
                "local_sources": {} if demo else dict(LOCAL_SOURCES),
                "count": len(jobs),
                "new_today": sum(1 for j in jobs if j.get("first_seen") == today),
                "jobs": jobs,
            },
            ensure_ascii=False,
            indent=1,
            # 標準 JSON 沒有 NaN／Infinity。Python 預設會寫出裸 NaN，前端的
            # JSON.parse 直接失敗、整個網站空白——與其悄悄寫出壞檔案，不如
            # 讓這次執行失敗、網站留著昨天的資料。
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    log.info("輸出 %d 筆 → %s（今日新增 %d）", len(jobs), OUT, sum(1 for j in jobs if j.get("first_seen") == today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
