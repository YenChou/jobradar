"""Indeed France — 透過 JobSpy (https://github.com/speedyapply/JobSpy)。

JobSpy 走 Indeed 內部 API，目前不需 proxy，是最穩的來源。
之後要加 LinkedIn 時，把 site_name 加上 "linkedin" 並配置 proxies 即可。
"""
from __future__ import annotations

import logging
import time

from scraper.net import retry_call
from scraper.util import to_date_str

log = logging.getLogger("chasse.indeed")


def fetch(search_terms: list[str], hours_old: int = 72, results_per_term: int = 50) -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.error("python-jobspy 未安裝：pip install python-jobspy")
        return []

    jobs: list[dict] = []
    for term in search_terms:
        # JobSpy 內部自己發請求，用不到 net.session 那層重試，所以包在外面。
        try:
            df = retry_call(
                lambda: scrape_jobs(
                    site_name=["indeed"],
                    search_term=term,
                    location="France",
                    country_indeed="France",
                    results_wanted=results_per_term,
                    hours_old=hours_old,
                    description_format="markdown",
                ),
                what=f"Indeed {term!r}",
            )
        except Exception as e:  # 重試到底仍失敗；單一搜尋詞失敗不影響其他
            log.warning("Indeed 搜尋 %r 失敗: %s", term, e)
            continue

        for _, row in df.iterrows():
            jobs.append(
                {
                    "title": row.get("title") or "",
                    "company": row.get("company") or "",
                    "location": row.get("location") or "",
                    "description": row.get("description") or "",
                    "url": row.get("job_url") or "",
                    "source": "Indeed",
                    "date_posted": to_date_str(row.get("date_posted")),
                    "contract": row.get("job_type") or None,
                    "work_mode": "remote" if row.get("is_remote") is True else None,
                    "salary": _salary(row),
                }
            )
        log.info("Indeed %r → %d 筆", term, len(df))
        time.sleep(3)  # 對來源友善，降低被限流風險
    return jobs


def _salary(row) -> str | None:
    lo, hi = row.get("min_amount"), row.get("max_amount")
    if lo and hi:
        try:
            return f"{int(lo):,}–{int(hi):,} {row.get('currency') or 'EUR'}/{row.get('interval') or 'an'}"
        except (TypeError, ValueError):
            return None
    return None
