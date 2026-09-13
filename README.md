# Jo's Chasse 📡

每天自動抓取法國求職平台，篩出五類行銷職缺（Marketing Operations、Performance
Marketing、CRM、Marketing Analytics、Marketing Produit），發布成可篩選的靜態網站。

網站：https://yenchou.github.io/jobradar/

規劃書（來源分級、關鍵字規格、付費選項）：見專案 Artifact「Jo's Chasse 規劃書」。

## 架構

```
GitHub Actions（每天巴黎 12:00 與 19:00 兩輪）
  │  cron 只吃 UTC，巴黎有日光節約時間，所以排四條 cron，
  │  再由 gate 步驟挑出當季正確的那兩條（另外兩條會跳過）
  └─ python -m scraper.main
       ├─ net.py                       共用 HTTP 層：重試、逾時、時間預算
       ├─ sources/indeed_jobspy.py     Indeed（JobSpy）
       ├─ sources/wttj.py              Welcome to the Jungle（Algolia API）
       ├─ sources/france_travail.py    France Travail 官方 API（需金鑰，無則跳過）
       ├─ sources/apec.py              APEC（站內搜尋 webservice）
       ├─ sources/fashionjobs.py       Fashion Jobs（列表頁＋詳情頁 JSON-LD，見「注意」）
       ├─ sources/isarta.py            Isarta（行銷／傳播職缺板列表頁）
       ├─ enrich.py                    分類、過濾、加權、去重
       └─ docs/data/jobs.json          （30 天滾動資料）
docs/index.html                        GitHub Pages 網站（職缺／封存兩個分頁）
keywords.yml                           所有分類與排序規則（改這裡，不用改程式）
```

每個來源獨立：一個掛掉不影響其他，Actions log 會顯示每個來源抓到幾筆。

## 網站功能

- **篩選**：時間、職類、地區、工作型態、合約、標籤（需中文／影音內容）、來源、投遞狀態。
- **投遞追蹤**：每筆可標記「已投遞／面試中／略過」，存在瀏覽器 localStorage，不用帳號。
- **封存分頁**：按卡片右下角的「封存」把職缺留下來；標記「已投遞」「面試中」會自動封存。
  職缺超過 30 天會從 `jobs.json` 下架，但**封存的職缺會保留**——封存當下就把整筆
  快照存進 localStorage，所以下架後仍查得到（會標示「已下架」）。
  附匯出／匯入 JSON 備份：localStorage 只存在這台瀏覽器，換裝置或清資料就沒了。
- **手動貼職缺**：JobTeaser、TrueUp 的 email alert 看到好職缺，貼網址就能入庫。

## 本地測試

系統 Python 可能太舊（JobSpy 需要 3.10+），用 uv 建虛擬環境：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

.venv/bin/python -m scraper.main --demo          # 不上網，用示範資料跑完整 pipeline
.venv/bin/python -m scraper.main                 # 真實抓取（HOURS_OLD=168 可回抓一週）
.venv/bin/python -m http.server -d docs 8000     # 開 http://localhost:8000 看網站
```

真實抓取約 10 分鐘，會覆寫 `docs/data/jobs.json`，測完記得 `git checkout --` 還原。

## 日常調整

- **漏抓／誤抓**：改 `keywords.yml`（關鍵字、排除詞、城市加權），commit 即生效。
  排除規則會**回溯套用**——既有的 `jobs.json` 下次執行時會用新規則重新過濾。
- **加來源**：在 `scraper/sources/` 加一個模組，回傳同樣欄位的 dict list，
  在 `main.py` 的 `scrape_all()` 註冊一行。**先看對方的 `robots.txt`。**
- **LinkedIn**（Phase 3）：JobSpy 已支援，把 `site_name` 加上 `"linkedin"`；
  穩定抓取需要 residential proxy（約 $5–10/月），設定 `proxies=[...]`。

## 維運

- **France Travail 金鑰**：到 https://francetravail.io 註冊 → 建 application →
  訂閱「API Offres d'emploi v2」→ repo Settings → Secrets and variables → Actions
  設 `FT_CLIENT_ID`、`FT_CLIENT_SECRET`。沒設金鑰時這個來源自動跳過。
- **排程延遲**：GitHub 的排程可能延遲 10–30 分鐘，屬正常現象。
- **時間預算**：每個來源都有上限（合計最壞 55 分鐘），`timeout-minutes: 75`。
  正常約 10 分鐘；會用到預算代表某個站在異常拖慢。
- **重試**：連線失敗（含 DNS）與 429/5xx 會自動重試 2 次。注意每次重試都會
  重新吃掉完整 timeout，不只是退避秒數——單一請求最壞約 93 秒。

## 注意

- 只抓公開頁面、只存摘要＋原站連結，個人使用。抓取節奏刻意放慢（站與站之間、
  每個請求之間都有間隔），對來源網站友善也降低被封風險。

- **Fashion Jobs 是一個已知的例外，使用前請知悉**：

  - 該站的 `robots.txt` 對 `User-agent: *` 明確 `Disallow: /s/?keyword=*` 與
    `/s/?page=*`，而那是這個來源唯一可用的入口（robots.txt 公告的 sitemap
    子項是空的，無法用來發現職缺）。
  - 該站在 Cloudflare 後面，會依 TLS 指紋擋掉 requests／urllib3——連 robots.txt
    允許的路徑也一併擋。所以這個來源改用 `tls_client` 模擬瀏覽器指紋。
  - 也就是說，**這個來源同時違反該站的 robots.txt 並繞過其機器人防護**，與上面
    「對來源網站友善」的原則相牴觸，是專案擁有者在知情下做的取捨。請求足跡已
    刻意壓低（每個搜尋詞 2 頁、詳情頁上限 60 筆），遇到 403 會整組收工不再重試。
  - 不想維持這個例外的話，把 `main.py` 的 `scrape_all()` 裡 Fashion Jobs 那一行
    註冊拿掉即可，其他來源不受影響。
