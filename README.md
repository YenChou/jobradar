# JobRadar France 📡

每天自動抓取法國求職平台，篩出五類行銷職缺（Marketing Operations、Performance
Marketing、CRM、Marketing Analytics、Marketing Produit），發布成可篩選的靜態網站。

規劃書（來源分級、關鍵字規格、付費選項）：見專案 Artifact「JobRadar France 規劃書」。

## 架構

```
GitHub Actions（每天 04:30 UTC ≈ 巴黎 06:30）
  └─ python -m scraper.main
       ├─ sources/indeed_jobspy.py     Indeed（JobSpy）
       ├─ sources/wttj.py              Welcome to the Jungle（Algolia API）
       ├─ sources/france_travail.py    France Travail 官方 API（需金鑰，無則跳過）
       ├─ enrich.py                    分類、過濾、加權、去重
       └─ docs/data/jobs.json          （30 天滾動資料）
docs/index.html                        GitHub Pages 網站
keywords.yml                           所有分類與排序規則（改這裡，不用改程式）
```

## 上線步驟（一次性，約 15 分鐘）

0. **把 workflow 檔案放到定位**（安全機制不允許遠端工具直接寫 `.github/`，所以它在 `_setup/`）
   ```bash
   cd "這個資料夾"
   mkdir -p .github/workflows && mv _setup/daily.yml .github/workflows/ && rmdir _setup
   ```

1. **建 GitHub repo 並推上這些檔案**
   ```bash
   git init && git add -A && git commit -m "init JobRadar"
   # 在 GitHub 建一個 repo（private 也可以），然後：
   git remote add origin git@github.com:<你的帳號>/jobradar.git
   git branch -M main && git push -u origin main
   ```

2. **開啟 GitHub Pages**
   repo → Settings → Pages → Source 選 `Deploy from a branch`，
   branch 選 `main`、資料夾選 `/docs` → Save。
   幾分鐘後網站在 `https://<你的帳號>.github.io/jobradar/`。
   ⚠️ private repo 的 Pages 需要付費方案；免費帳號把 repo 設 public 即可
   （網站上只有職缺摘要與公開連結，沒有個人資料）。

3. **第一次手動跑爬蟲**
   repo → Actions → 「Daily job scrape」→ Run workflow。
   跑完會自動 commit `docs/data/jobs.json`，網站就有真實資料了。
   之後每天早上自動跑，不用管它。

4. **（建議）France Travail 金鑰**（免費，5 分鐘）
   1. 到 https://francetravail.io 註冊
   2. 建立 application，訂閱「API Offres d'emploi v2」
   3. repo → Settings → Secrets and variables → Actions → 新增兩個 secret：
      `FT_CLIENT_ID`、`FT_CLIENT_SECRET`
   沒設金鑰前，Indeed 和 WTTJ 照常運作。

## 本地測試

```bash
pip install -r requirements.txt
python -m scraper.main --demo        # 不上網，用示範資料跑完整 pipeline
python -m scraper.main               # 真實抓取（HOURS_OLD=168 可回抓一週）
python -m http.server -d docs 8000   # 開 http://localhost:8000 看網站
```

## 日常調整

- **漏抓／誤抓**：改 `keywords.yml`（關鍵字、排除詞、城市加權），commit 即生效。
- **加來源**：在 `scraper/sources/` 加一個模組，回傳同樣欄位的 dict list，
  在 `main.py` 的 `scrape_all()` 註冊一行。Phase 2 預計加 APEC、Fashion Jobs、Isarta。
- **LinkedIn**（Phase 3）：JobSpy 已支援，把 `site_name` 加上 `"linkedin"`；
  穩定抓取需要 residential proxy（約 $5–10/月），設定 `proxies=[...]`。

## 注意

- 只抓公開頁面、只存摘要＋原站連結，個人使用。
- GitHub Actions 排程可能延遲 10–30 分鐘，屬正常現象。
- 單一來源掛掉不影響其他來源；Actions log 會顯示每個來源抓到幾筆。
