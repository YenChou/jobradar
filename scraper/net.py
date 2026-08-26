"""共用的 HTTP 層：帶重試的 requests session。

各來源原本直接用 requests，暫時性的網路問題（DNS 解析失敗、連線逾時、
站方 429/5xx）會讓整個來源當次歸零——實際發生過一次本機 DNS 中斷，
Fashion Jobs、Isarta、APEC 三個來源同時掛零。

重試涵蓋連線層（含 DNS）與伺服器暫時性錯誤，退避間隔遞增。
搜尋類請求都是唯讀的，POST 一併重試沒有副作用。
"""
from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("chasse.net")

RETRY_TOTAL = 3          # 首次之外再試 3 次
BACKOFF_FACTOR = 1       # 間隔 0s → 2s → 4s（urllib3 是 backoff * 2^(n-1)）
                         # 站方整個掛掉時，每個請求最多多花 6 秒；暫時性抖動幾秒內就會過
RETRY_STATUS = (429, 500, 502, 503, 504)


def session() -> requests.Session:
    """建立帶重試的 session。每個來源自己開一個，連線池不互相干擾。"""
    s = requests.Session()
    retry = Retry(
        total=RETRY_TOTAL,
        connect=RETRY_TOTAL,   # 連線失敗（含 DNS 解析不到）
        read=RETRY_TOTAL,
        status=RETRY_TOTAL,
        status_forcelist=RETRY_STATUS,
        backoff_factor=BACKOFF_FACTOR,
        allowed_methods=frozenset({"GET", "POST"}),  # 搜尋是唯讀的，POST 重試安全
        raise_on_status=False,   # 狀態碼交給呼叫端的 raise_for_status 處理
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def retry_call(fn, *, what: str, attempts: int = RETRY_TOTAL):
    """重試「不經過本模組 session」的呼叫，例如 JobSpy 內部自己發請求。

    ⚠️ 不要拿來包 session() 發出的請求——那層已經有 Retry，兩層疊起來
    會變成 retries × attempts 次，一個掛掉的主機要等好幾十秒才放棄。

    失敗到底就往外拋，由呼叫端決定要不要讓這個來源整組跳過。
    """
    import time

    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if i == attempts:
                raise
            wait = BACKOFF_FACTOR * (2 ** (i - 1))
            log.warning("%s 第 %d 次失敗（%s），%d 秒後重試", what, i, e, wait)
            time.sleep(wait)
