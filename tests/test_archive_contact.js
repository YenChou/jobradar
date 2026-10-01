// 封存頁：「LinkedIn 聯絡」追蹤（投遞後要聯絡 HR 或該 team 的人，勾起來就知道
// 哪些還沒做）、依職缺公告日期排序、封存資料的清理與匯入。
//
// 執行：cd tests && npm i jsdom && node test_archive_contact.js
//
// 全部用自己造的資料，不讀每天會變的 docs/data/jobs.json：資料停更或換了內容，
// 測試也不會突然變紅。
const fs = require("fs"), path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");
const html = fs.readFileSync(path.join(__dirname, "..", "docs", "index.html"), "utf8");

// 跟頁面一樣用巴黎當地日期，才會落在預設的「最近 7 天」裡
const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Paris", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const mkJob = (id, posted, extra) => ({
  id, title: "T" + id, company: "C" + id, location: "Paris", city: "Paris", region: "paris",
  work_mode: null, contract: "CDI", categories: ["crm"], skills: [], bonus_tags: [], score: 10,
  date_posted: posted, first_seen: posted, salary: null, description_snippet: "",
  sources: [{ name: "S", url: "https://example.org/" + id }], ...extra });
const data = { generated_at: new Date().toISOString(), count: 3, new_today: 3, source_stats: { S: 3 },
               jobs: [mkJob("j1", today), mkJob("j2", today), mkJob("j3", today)] };

let lastAlert = null;
// 開一個新頁面；seed 是開頁前就在 localStorage 裡的東西。回傳頁面與頁面腳本拋出的錯誤
function boot(seed, url){
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errors.push(e));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: url || "https://example.org/", virtualConsole: vc,
    beforeParse(w){
      for (const [k, v] of Object.entries(seed || {})) w.localStorage.setItem(k, JSON.stringify(v));
      w.fetch = () => Promise.resolve({ ok:true, json: () => Promise.resolve(JSON.parse(JSON.stringify(data))) });
      w.alert = m => { lastAlert = m; };
    },
  });
  return new Promise(r => setTimeout(() => r({ w: dom.window, errors }), 300));
}

// 測試本身拋錯時要明確失敗，不能連摘要都沒印
process.on("unhandledRejection", e => { console.log("❌ 測試拋錯：" + (e && e.stack || e)); process.exit(1); });

(async () => {
  const { w, errors: pageErrors } = await boot();
  const d = w.document, $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
  let fail = 0;
  const ok = (c, m) => { console.log((c?"✅":"❌")+" "+m); if(!c) fail++; };
  const arch = () => JSON.parse(w.localStorage.getItem("jr_archive") || "{}");
  ok($("#cards").children.length === 3, "主列表載入自造資料：" + $("#cards").children.length + " 筆");

  // 你的流程：投遞 → 自動封存
  const c0 = $("#cards").children[0], c1 = $("#cards").children[1];
  const t0 = c0.querySelector(".title").textContent.trim();
  [...c0.querySelectorAll(".track button")].find(b=>b.textContent==="已投遞").click();
  [...$("#cards").children[1].querySelectorAll(".track button")].find(b=>b.textContent==="已投遞").click();
  ok(Object.keys(arch()).length === 2, "兩筆投遞自動進封存");

  $("#tab-archive").click();
  ok(!$("#view-archive").hidden, "切到封存分頁");

  // 勾選項存在且預設未勾
  const cards = () => [...$("#arch-cards").children];
  ok(cards().length === 2, `封存頁 ${cards().length} 筆`);
  ok(cards().every(c => !c.querySelector(".b.gone")), "載入資料時就建好上架索引：剛封存的不會被標成已下架");
  ok(cards().every(c => c.querySelector(".contact input[type=checkbox]")), "每張卡片都有勾選項");
  ok(cards().every(c => !c.querySelector(".contact input").checked), "預設未勾選");
  ok(cards().every(c => c.querySelector(".b.todo")), "未聯絡的標「待聯絡」");
  ok(/2 筆待聯絡/.test($("#arch-count").textContent), "計數：" + $("#arch-count").textContent);
  ok(/只看待聯絡（2）/.test($("#arch-todo").textContent), "按鈕顯示待辦數：" + $("#arch-todo").textContent);

  // 聯絡完打勾
  cards()[0].querySelector(".contact input").click();
  const done = Object.values(arch()).filter(a => a.contacted);
  ok(done.length === 1, "打勾後寫入 localStorage");
  ok(/^\d{4}-\d{2}-\d{2}$/.test(done[0].contacted_at || ""), "記錄聯絡日期 " + done[0].contacted_at);
  ok(/1 筆待聯絡/.test($("#arch-count").textContent), "計數更新：" + $("#arch-count").textContent);

  // 已聯絡的排後面、標籤消失、文字改為已聯絡
  const first = cards()[0];
  ok(!first.querySelector(".contact input").checked, "待聯絡的排在最前面");
  const doneCard = cards().find(c => c.querySelector(".contact input").checked);
  ok(!doneCard.querySelector(".b.todo"), "已聯絡的不再標「待聯絡」");
  ok(/已聯絡 \d{4}-\d{2}-\d{2}/.test(doneCard.querySelector(".contact").textContent), "顯示聯絡日期");
  ok(doneCard.querySelector(".contact").classList.contains("done"), "已聯絡樣式");

  // 只看待聯絡
  $("#arch-todo").click();
  ok(cards().length === 1, "「只看待聯絡」只留 1 筆");
  ok($("#arch-todo").getAttribute("aria-pressed") === "true", "按鈕呈現按下狀態");
  $("#arch-todo").click();
  ok(cards().length === 2, "再按一次恢復全部");

  // 取消勾選
  doneCard2 = cards().find(c => c.querySelector(".contact input").checked);
  doneCard2.querySelector(".contact input").click();
  ok(Object.values(arch()).every(a => !a.contacted), "可取消勾選");
  ok(Object.values(arch()).every(a => !a.contacted_at), "取消時清掉日期");
  ok(/2 筆待聯絡/.test($("#arch-count").textContent), "計數回復");

  // 全部聯絡完。每次點擊都會重繪，所以要重新查詢，不能拿舊的節點陣列連點
  while (cards().some(c => !c.querySelector(".contact input").checked)) {
    cards().find(c => !c.querySelector(".contact input").checked)
           .querySelector(".contact input").click();
  }
  ok(/全部已聯絡/.test($("#arch-count").textContent), "全部完成時的提示：" + $("#arch-count").textContent);

  // 舊資料相容：沒有 contacted 欄位的封存項
  const a = arch(); const k = Object.keys(a)[0];
  delete a[k].contacted; delete a[k].contacted_at;
  w.localStorage.setItem("jr_archive", JSON.stringify(a));
  w.eval("archive = asPlainObject(store.get('jr_archive', {})); renderArchive();");
  ok(cards().some(c => c.querySelector(".b.todo")), "舊封存資料（無 contacted 欄位）視為待聯絡，不會炸");

  // localStorage 寫入失敗時要還原，否則畫面說「已聯絡」但重載後消失
  console.log();
  w.eval("archive = asPlainObject(store.get('jr_archive', {})); archTodoOnly = false; renderArchive();");
  const target = cards()[0];
  const before = JSON.parse(w.localStorage.getItem("jr_archive"));
  const beforeCount = $("#arch-count").textContent;
  // 覆寫實例屬性對 jsdom 的 Storage 無效，要改原型
  const realSet = w.Storage.prototype.setItem;
  w.Storage.prototype.setItem = () => { throw new Error("QuotaExceededError"); };
  lastAlert = null;
  target.querySelector(".contact input").click();
  w.Storage.prototype.setItem = realSet;
  ok(JSON.stringify(JSON.parse(w.localStorage.getItem("jr_archive"))) === JSON.stringify(before),
     "寫入失敗時 localStorage 未被改動");
  w.eval("renderArchive();");
  ok($("#arch-count").textContent === beforeCount,
     "寫入失敗時畫面還原，不會顯示成已聯絡：" + $("#arch-count").textContent);
  ok(/沒有存起來/.test(lastAlert || ""), "有提示使用者：" + lastAlert);

  // ---- 排序：待聯絡優先，組內依公告日期新到舊（推定日期一起排），同日看封存日 ----
  console.log();
  const titles = () => cards().map(c => c.querySelector(".title").textContent.trim());
  const cardOf = t => cards().find(c => c.querySelector(".title").textContent.trim() === t);
  const dateOf = t => cardOf(t).querySelector(".date").textContent;
  const fake = (id, posted, at, extra) => ({ job: mkJob(id, posted, extra), at, why: "manual" });
  // 模擬「重新載入頁面」：從 localStorage 讀封存（會清理）、換上架資料、跑載入時的同步
  const loadArch = (obj, liveJobs) => {
    w.localStorage.setItem("jr_archive", JSON.stringify(obj));
    w.__live = liveJobs || [];
    $("#aq").value = "";
    w.eval("archive = asPlainObject(store.get('jr_archive', {})); archTodoOnly = false;" +
           "DATA = { ...DATA, jobs: window.__live }; syncLive(); renderArchive();");
  };

  loadArch({
    x1: fake("x1", "2026-09-01", "2026-09-30"),
    x2: fake("x2", "2026-09-20", "2026-09-02"),
    x3: fake("x3", "2026-09-10", "2026-09-15"),
    x4: { ...fake("x4", "2026-09-25", "2026-09-26"), contacted: true, contacted_at: "2026-09-27" },
    x6: fake("x6", "2026-09-10", "2026-09-20"),                          // 與 x3 同日：封存日較新的在前
    x8: fake("x8", "2026-09-29", "2026-09-29", { date_assumed: true }),  // 推定日期：照日期一起排
    x5: fake("x5", null, "2026-09-01", { first_seen: "2026-09-12" }),    // 只有 first_seen：也是推定
    // 不合理的日期一律忽略，沒有日期的排最後（彼此再看封存日）
    x7: fake("x7", 20260920, "2026-09-03", { first_seen: 20260920 }),
    x9: fake("x9", "9/25/2026", "2026-09-04", { first_seen: "9/25/2026" }),
    x10: fake("x10", "2026-13-45", "2026-09-05", { first_seen: "2026-02-30" }),
    x11: fake("x11", "9999-01-01", "2026-09-06", { first_seen: "9999-01-01" }),
  });
  ok(JSON.stringify(titles()) === JSON.stringify(
       ["Tx8", "Tx2", "Tx5", "Tx6", "Tx3", "Tx1", "Tx11", "Tx10", "Tx9", "Tx7", "Tx4"]),
     "排序：" + titles().join(","));
  ok(dateOf("Tx8") === "約 2026-09-29", "推定日期標「約」");
  ok(dateOf("Tx5") === "約 2026-09-12", "只有 first_seen 也標「約」");
  ok(dateOf("Tx2") === "2026-09-20", "真日期不標「約」");
  for (const t of ["Tx7", "Tx9", "Tx10", "Tx11"])
    ok(dateOf(t) === "", t + " 的不合理日期不顯示：「" + dateOf(t) + "」");

  // ---- 顯示時用架上的真日期代替快照日期（只影響顯示，不改存的資料） ----
  console.log();
  const liveCase = {
    // 推定 9/30、架上真日期 9/04（不晚於推定）→ 用真日期
    L1: fake("L1", "2026-09-30", "2026-09-30", { date_assumed: true }),
    // 已經是真日期：一律不換。同公司同職稱重新刊登會拿到同一個 ID，不能蓋掉你投遞那筆的日期
    L2: { ...fake("L2", "2026-08-01", "2026-08-02"), why: "applied" },
    // 推定 8/01、架上真日期 9/20 晚於推定 → 那是重新刊登的另一筆，不換
    L3: fake("L3", "2026-08-01", "2026-08-01", { date_assumed: true }),
    // 已經是真日期，架上的日期就算更早也不換
    L5: fake("L5", "2026-09-10", "2026-09-11"),
    // 架上也只是推定值 → 不換
    L4: fake("L4", "2026-09-05", "2026-09-05", { date_assumed: true }),
    // 快照完全沒有可用的日期 → 用架上的真日期
    L6: fake("L6", null, "2026-09-12", { first_seen: "not a date" }),
    x1: fake("x1", "2026-09-10", "2026-09-30"),
  };
  loadArch(liveCase,
    [mkJob("L1", "2026-09-04"), mkJob("L2", "2026-09-20"), mkJob("L3", "2026-09-20"),
     mkJob("L4", "2026-09-01", { date_assumed: true }), mkJob("L5", "2026-09-05"),
     mkJob("L6", "2026-09-07"), mkJob("x1", "2026-09-10")]);
  ok(dateOf("TL1") === "2026-09-04", "推定日期顯示成架上的真日期、不再標「約」：" + dateOf("TL1"));
  ok(dateOf("TL2") === "2026-08-01", "已記下的真日期不被重新刊登換掉：" + dateOf("TL2"));
  ok(dateOf("TL3") === "約 2026-08-01", "架上日期晚於推定值（重新刊登）不換：" + dateOf("TL3"));
  ok(dateOf("TL5") === "2026-09-10", "已記下的真日期即使架上較早也不換：" + dateOf("TL5"));
  ok(dateOf("TL4") === "約 2026-09-05", "架上也是推定值時不換：" + dateOf("TL4"));
  ok(dateOf("TL6") === "2026-09-07", "快照沒有可用日期時用架上的真日期：" + dateOf("TL6"));
  ok(JSON.stringify(titles()) === JSON.stringify(["Tx1", "TL5", "TL6", "TL4", "TL1", "TL2", "TL3"]), "排序：" + titles().join(","));
  ok(JSON.stringify(arch()) === JSON.stringify(liveCase), "載入與顯示都沒有改動存的資料");

  // 補日期只在載入時做：搜尋框打字重繪不寫 localStorage、不重建上架索引
  const realSet2 = w.Storage.prototype.setItem;
  let writes = 0;
  w.Storage.prototype.setItem = function(){ writes++; return realSet2.apply(this, arguments); };
  const idxBefore = w.eval("liveById");
  for (const ch of ["T", "TL"]) { $("#aq").value = ch; $("#aq").dispatchEvent(new w.Event("input")); }
  w.Storage.prototype.setItem = realSet2;
  ok(writes === 0, "搜尋重繪不寫 localStorage（寫了 " + writes + " 次）");
  ok(w.eval("liveById") === idxBefore, "搜尋重繪不重建上架索引");
  $("#aq").value = "";

  // ---- 手動加入的職缺不會被標成已下架 ----
  console.log();
  loadArch({}, data.jobs);
  $("#tab-jobs").click();
  $("#add-title").value = "Manual role"; $("#add-company").value = "M Co"; $("#add-url").value = "https://example.org/m";
  $("#add-btn").click();
  const mCard = [...$("#cards").children].find(c => c.querySelector(".title").textContent.trim() === "Manual role");
  [...mCard.querySelectorAll(".track button")].find(b => b.textContent === "已投遞").click();
  $("#tab-archive").click();
  ok(cardOf("Manual role") && !cardOf("Manual role").querySelector(".b.gone"), "手動加入後投遞：封存頁不標「已下架」");

  // ---- 匯入 ----
  console.log();
  const importFile = async obj => {
    lastAlert = null;
    w.archiveImport(new w.File([JSON.stringify({ archive: obj })], "backup.json", { type: "application/json" }));
    await new Promise(r => setTimeout(r, 100));
  };
  loadArch({
    x1: fake("x1", "2026-09-10", "2026-09-30"),
    // 本機已有真日期、已聯絡；備份是比較舊的版本
    k1: { ...fake("k1", "2026-09-04", "2026-09-05"), contacted: true, contacted_at: "2026-09-06", why: "applied" },
    // 本機只有推定日期、還沒聯絡；備份有真日期、已聯絡
    k2: { ...fake("k2", "2026-09-28", "2026-09-28", { date_assumed: true }), contacted: false },
    // 本機日期都不合格；備份有可用的日期
    k3: fake("k3", "bad", "2026-09-28", { first_seen: "bad" }),
  }, [mkJob("m2", "2026-09-02")]);
  await importFile({
    k1: { ...fake("k1", "2026-09-30", "2026-09-01", { date_assumed: true }), contacted: false, why: "manual" },
    k2: { ...fake("k2", "2026-09-20", "2026-09-20"), contacted: true, contacted_at: "2026-09-21" },
    k3: fake("k3", "2026-09-15", "2026-09-15", { date_assumed: true }),
    m1: fake("m1", "25/09/2026", "2026-09-25", { first_seen: "2026-09-24" }),  // date_posted 壞了，first_seen 還能用
    m2: fake("m2", "2026-13-01", "2026-09-26", { first_seen: "2026-02-30" }),  // 都壞了，但架上有真日期
    m4: fake("m4", "9/9/2026", "2026-09-26", { first_seen: "9/9/2026" }),      // 都壞了，也不在架上
    m3: { job: null, at: "2026-09-26" },                                        // 結構壞掉：略過
  });
  ok(/新增 3 筆，目前共 7 筆/.test(lastAlert || ""), "匯入計數：" + (lastAlert || "").split("\n")[0]);
  ok(/略過 1 筆/.test(lastAlert || ""), "提示略過的壞項目");
  ok(/其中 1 筆的公告日期無法辨識/.test(lastAlert || ""),
     "日期警告只算最後真的沒有日期的那筆：" + (lastAlert || "").replace(/\n/g, " / "));
  const ka = arch();
  ok(ka.k1.contacted === true && ka.k1.contacted_at === "2026-09-06" && ka.k1.why === "applied",
     "舊備份不會蓋掉本機的「已聯絡」與封存原因");
  ok(ka.k1.job.date_posted === "2026-09-04" && !ka.k1.job.date_assumed, "舊備份不會蓋掉本機的真日期：" + ka.k1.job.date_posted);
  ok(ka.k2.contacted === true && ka.k2.contacted_at === "2026-09-21", "備份的「已聯絡」補進本機");
  ok(ka.k2.job.date_posted === "2026-09-20" && !ka.k2.job.date_assumed, "本機只有推定日期時採用備份的真日期：" + ka.k2.job.date_posted);
  ok(dateOf("Tk3") === "約 2026-09-15", "本機沒有可用日期時整組採用備份的日期：" + dateOf("Tk3"));
  ok(ka.m1.job.date_posted === "25/09/2026", "匯入的原始內容原樣保留，不因日期不合格被刪");
  ok(dateOf("Tm1") === "約 2026-09-24", "壞掉的 date_posted 略過、退回 first_seen（標「約」）：" + dateOf("Tm1"));
  ok(dateOf("Tm2") === "2026-09-02", "日期都壞了但還在架上：顯示架上的真日期");
  ok(dateOf("Tm4") === "", "日期都壞了也不在架上：不顯示日期");

  // 匯入別人的備份：內容不能在頁面上執行
  w.__xss = 0;
  await importFile({
    e1: { job: mkJob("e1", "2026-09-01", {
            title: "<img src=x onerror=window.__xss=1>",
            sources: [null, "str", { name: "<b>s</b>", url: "javascript:window.__xss=2" }],
            categories: null, bonus_tags: 5 }),
          at: "2026-09-01", why: "<img src=x onerror=window.__xss=3>" },
  });
  await new Promise(r => setTimeout(r, 50));
  const e1 = cards().find(c => c.querySelector(".why") && /onerror/.test(c.querySelector(".why").textContent));
  ok(!!e1, "惡意內容的封存照樣顯示（以純文字呈現）");
  ok(e1 && !e1.querySelector("img") && !e1.querySelector("b"), "封存原因、職稱、來源名稱都被跳脫，沒有變成 HTML");
  ok(e1 && [...e1.querySelectorAll("a")].every(a => !/^javascript:/i.test(a.getAttribute("href"))), "javascript: 連結被換掉");
  ok(w.__xss === 0, "沒有執行任何匯入的程式碼");

  // 儲存空間滿：整批不匯入，也不能說「匯入完成」
  const before2 = w.localStorage.getItem("jr_archive");
  const realSet3 = w.Storage.prototype.setItem;
  w.Storage.prototype.setItem = () => { throw new Error("QuotaExceededError"); };
  await importFile({ n1: fake("n1", "2026-09-01", "2026-09-01") });
  w.Storage.prototype.setItem = realSet3;
  ok(/匯入失敗/.test(lastAlert || "") && !/匯入完成/.test(lastAlert || ""), "存不進去時提示匯入失敗：" + lastAlert);
  ok(w.localStorage.getItem("jr_archive") === before2 && !w.eval("archive.n1") && !cardOf("Tn1"),
     "存不進去時記憶體、畫面、localStorage 都沒有半套資料");

  // 裝置時鐘調慢：jobs.json 的產生時間比裝置時間晚時，最近的日期仍算有效
  const ahead = new Date(Date.now() + 10 * 864e5).toISOString();
  const soon = ahead.slice(0, 10), far = new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10);
  w.__gen = ahead;
  loadArch({ c1: fake("c1", soon, "2026-09-01"), c2: fake("c2", far, "2026-09-01", { first_seen: far }) });
  w.eval("DATA = { ...DATA, generated_at: window.__gen }; renderArchive();");
  ok(dateOf("Tc1") === soon, "裝置時鐘慢於資料產生時間時，最近的日期照常顯示：" + dateOf("Tc1"));
  ok(dateOf("Tc2") === "", "比裝置時鐘與資料時間都晚的日期才當成不合理");
  w.eval("DATA = { ...DATA, generated_at: null };");

  ok(pageErrors.length === 0, "頁面腳本沒有拋錯" + (pageErrors.length ? "：" + pageErrors[0].message : ""));

  // 整個封存根本不是物件（例如被別的程式寫壞）：當成空的，照樣可以封存
  const g = await boot({ jr_archive: "garbage" }, "https://garbage.example/");
  const gd = g.w.document;
  [...gd.querySelector("#cards").children[0].querySelectorAll(".track button")].find(b => b.textContent === "已投遞").click();
  gd.querySelector("#tab-archive").click();
  ok(gd.querySelector("#arch-cards").children.length === 1 && g.errors.length === 0,
     "封存資料整個壞掉時仍能封存、不拋錯" + (g.errors.length ? "：" + g.errors[0].message : ""));

  // ---- 壞掉的封存資料：頁面照常、原始資料不刪、有提示 ----
  console.log();
  const seedArch = {
    b1: null, b2: { job: null }, b4: "garbage",
    b3: { job: { title: "Tno-id", company: "C", date_posted: "2026-9-1" }, at: "2026-09-01", why: "manual" },  // 沒有 id、日期格式不對
    ok1: { job: { id: "ok1", title: "Tok1", company: "C", sources: [null], bonus_tags: [null] }, at: "2026-09-02", why: "manual" },
  };
  const broken = await boot({ jr_archive: seedArch }, "https://broken.example/");
  const bd = broken.w.document, bq = s => bd.querySelector(s);
  ok(bq("#cards").children.length === 3, "主列表照常顯示：" + bq("#cards").children.length + " 筆");
  bq("#tab-archive").click();
  const btitles = () => [...bq("#arch-cards").children].map(c => c.querySelector(".title").textContent.trim());
  ok(JSON.stringify(btitles().sort()) === JSON.stringify(["Tno-id", "Tok1"]), "能顯示的都顯示（少了 id 用 key 補）：" + btitles().join(","));
  ok(/3 筆資料損壞無法顯示（仍保留/.test(bq("#arch-count").textContent), "提示有幾筆畫不出來：" + bq("#arch-count").textContent);
  // 對其中一筆打勾（會寫入 localStorage），其餘原始資料都要原樣保留
  [...bq("#arch-cards").children].find(c => /Tok1/.test(c.textContent)).querySelector(".contact input").click();
  const after = JSON.parse(broken.w.localStorage.getItem("jr_archive"));
  const expect = JSON.parse(JSON.stringify(seedArch));
  ok(after.ok1.contacted === true, "打勾有存起來");
  delete after.ok1.contacted; delete after.ok1.contacted_at;
  ok(JSON.stringify(after) === JSON.stringify(expect), "之後寫入時，壞掉與日期不合格的原始資料都原樣保留");
  ok(broken.w.eval("archiveView('b3', archive.b3).job.id") === "b3", "少了 job.id 的用封存的 key 補");
  ok(broken.errors.length === 0, "頁面腳本沒有拋錯" + (broken.errors.length ? "：" + broken.errors[0].message : ""));

  console.log("\n失敗: " + fail);
  process.exit(fail ? 1 : 0);
})();
