"""共用的 HTTP 層：帶重試與時間預算的 requests session。

各來源原本直接用 requests，暫時性的網路問題（DNS 解析失敗、連線逾時、
站方 429/5xx）會讓整個來源當次歸零——實際發生過一次本機 DNS 中斷，
Fashion Jobs、Isarta、APEC 三個來源同時掛零。

重試的成本要算清楚（這裡踩過坑）：
每次重試都會重新吃掉完整的 timeout，不只是退避秒數。單一請求最壞情況是
  (1 + RETRY_TOTAL) × read_timeout + 退避總和
實測 read timeout 3 秒、RETRY_TOTAL=3 時，單一請求要 18 秒——6 倍。
所以 RETRY_TOTAL 壓到 2，並且提供 Budget 讓呼叫端限制整個來源的用時。
"""
from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("chasse.net")

RETRY_TOTAL = 2          # 重試次數（不含首次）→ 最多 3 次嘗試
BACKOFF_FACTOR = 1       # 退避 0s → 2s，合計 2s
RETRY_AFTER_MAX = 30     # 站方的 Retry-After 上限（見 _BoundedRetry）
RETRY_STATUS = (429, 500, 502, 503, 504)

CONNECT_TIMEOUT = 10     # 連線（含 DNS）失敗要快，卡住的多半不會好
READ_TIMEOUT = 30
TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

# 單一請求最壞用時，給 Budget 算安全邊際用
WORST_CASE_S = (1 + RETRY_TOTAL) * READ_TIMEOUT + BACKOFF_FACTOR * (2 ** RETRY_TOTAL - 1)


class _BoundedRetry(Retry):
    """限制 Retry-After 的上限，並讓重試留下 log。

    urllib3 的 backoff_max 只管指數退避，不管 Retry-After——站方回一個
    `Retry-After: 600` 就會讓單一請求靜靜卡住 30 分鐘（實測）。
    """

    def get_retry_after(self, response):
        after = super().get_retry_after(response)
        if after is None:
            return None
        if after > RETRY_AFTER_MAX:
            log.warning("站方要求等 %.0f 秒，壓到 %d 秒", after, RETRY_AFTER_MAX)
            return RETRY_AFTER_MAX
        return after

    def increment(self, method=None, url=None, *args, **kwargs):
        log.info("重試 %s %s（剩 %s 次）", method, url, self.total)
        return super().increment(method, url, *args, **kwargs)


def session() -> requests.Session:
    """建立帶重試的 session。每個來源自己開一個，連線池不互相干擾。

    呼叫端請帶 timeout=net.TIMEOUT，讓連線與讀取的上限分開。
    """
    s = requests.Session()
    retry = _BoundedRetry(
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


def retry_call(fn, *, what: str, retries: int = RETRY_TOTAL):
    """重試「不經過本模組 session」的呼叫，例如 JobSpy 內部自己發請求。

    retries 的語意與 session() 的 RETRY_TOTAL 一致：不含首次，退避公式
    也一樣（BACKOFF_FACTOR * 2^(n-1)，第一次重試不等待）。

    ⚠️ 不要拿來包 session() 發出的請求——那層已經有 Retry，兩層疊起來
    會變成 retries × attempts 次，一個掛掉的主機要等好幾十秒才放棄。

    失敗到底就往外拋，由呼叫端決定要不要讓這個來源整組跳過。
    """
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            if i == retries:
                raise
            wait = 0 if i == 0 else BACKOFF_FACTOR * (2 ** (i - 1))
            log.warning("%s 第 %d 次失敗（%s），%d 秒後重試", what, i + 1, e, wait)
            if wait:
                time.sleep(wait)


class Budget:
    """來源層級的時間預算。

    只在請求「之前」檢查的話，超出量等於一次請求的長度——而重試讓那個
    長度變成 WORST_CASE_S（約 92 秒），預算就形同虛設。所以 expired()
    預留一次最壞請求的邊際：時間不夠做完下一個請求就直接收工。
    """

    def __init__(self, seconds: float, *, margin: float = WORST_CASE_S):
        self.deadline = time.monotonic() + seconds
        self.margin = margin

    def expired(self) -> bool:
        return time.monotonic() + self.margin >= self.deadline

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())
