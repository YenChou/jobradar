"""JobRadar France — 每日抓取主程式。

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

from scraper import enrich
from scraper.util import norm, utcnow_iso

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "jobs.json"
RETENTION_DAYS = 30

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jobradar")


def load_cfg() -> dict:
    with open(ROOT / "keywords.yml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 關鍵字全部先正規化（去重音、小寫），比對時才一致
    for cat in cfg["categories"].values():
        cat["title_keywords"] = [norm(k) for k in cat["title_keywords"]]
        cat["skill_keywords"] = [norm(k) for k in cat["skill_keywords"]]
    for tag in cfg.get("bonus_tags", {}).values():
        tag["skill_keywords"] = [norm(k) for k in tag["skill_keywords"]]
    for key in ("exclude_title", "exclude_title_foreign", "seniority_boost_title",
                "seniority_penalty_title", "contract_boost", "west_cities"):
        cfg[key] = [norm(k) for k in cfg.get(key, [])]
    return cfg


def scrape_all(cfg: dict, known_urls: set[str]) -> tuple[list[dict], dict]:
    from scraper.sources import apec, fashionjobs, france_travail, indeed_jobspy, isarta, wttj

    hours_old = int(os.environ.get("HOURS_OLD", "72"))
    per_term = int(os.environ.get("RESULTS_PER_TERM", "50"))
    terms = cfg["search_terms"]

    raw: list[dict] = []
    stats: dict[str, int] = {}
    for name, fn in [
        ("Indeed", lambda: indeed_jobspy.fetch(terms, hours_old, per_term)),
        ("Welcome to the Jungle", lambda: wttj.fetch(terms)),
        ("France Travail", lambda: france_travail.fetch(terms, max_days_old=max(1, hours_old // 24))),
        ("APEC", lambda: apec.fetch(terms)),
        ("Fashion Jobs", lambda: fashionjobs.fetch(known_urls)),
        ("Isarta", lambda: isarta.fetch(known_urls)),
    ]:
        try:
            batch = fn()
        except Exception as e:  # 一個來源整組失敗也不中斷其他來源
            log.error("%s 整體失敗: %s", name, e)
            batch = []
        stats[name] = len(batch)
        raw += batch
    return raw, stats


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
                if j.get("date_posted"):
                    known_urls.update(s.get("url", "") for s in j.get("sources", []))
        except (json.JSONDecodeError, KeyError):
            pass

    raw, stats = demo_jobs() if demo else scrape_all(cfg, known_urls)
    log.info("原始抓到 %d 筆：%s", len(raw), stats)

    # 分類 + 過濾
    kept = [j for j in (enrich.classify(r, cfg) for r in raw) if j]
    log.info("過濾後 %d 筆（排除 Stage/Alternance 與五類皆未命中者）", len(kept))

    # 本次去重
    kept = enrich.dedupe(kept)

    # 與歷史合併（保留 first_seen，讓「最近24小時／7天」視圖有依據）
    today = utcnow_iso()[:10]
    history = {}
    if OUT.exists():
        try:
            history = {j["id"]: j for j in json.loads(OUT.read_text(encoding="utf-8")).get("jobs", [])}
        except (json.JSONDecodeError, KeyError):
            log.warning("既有 jobs.json 無法解析，視為首次執行")

    for j in kept:
        old = history.get(j["id"])
        j["first_seen"] = old.get("first_seen", today) if old else today
        history[j["id"]] = j

    # 30 天過期下架 + 用「目前的」排除規則重新清洗歷史（規則更新即回溯生效）
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    jobs = [
        j for j in history.values()
        if (j.get("first_seen") or today) >= cutoff
        and not enrich.excluded_title(j.get("title", ""), cfg)
    ]

    # 排序：分數高在前，同分者新的在前
    jobs.sort(key=lambda j: (-j["score"], _desc(j.get("first_seen")), _desc(j.get("date_posted"))))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated_at": utcnow_iso(),
                "source_stats": stats,
                "count": len(jobs),
                "new_today": sum(1 for j in jobs if j.get("first_seen") == today),
                "jobs": jobs,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    log.info("輸出 %d 筆 → %s（今日新增 %d）", len(jobs), OUT, sum(1 for j in jobs if j.get("first_seen") == today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
