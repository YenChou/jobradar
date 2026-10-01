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
  w.eval("archive = cleanArchive(store.get('jr_archive', {})); renderArchive();");
  ok(cards().some(c => c.querySelector(".b.todo")), "舊封存資料（無 contacted 欄位）視為待聯絡，不會炸");

  // localStorage 寫入失敗時要還原，否則畫面說「已聯絡」但重載後消失
  console.log();
  w.eval("archive = cleanArchive(store.get('jr_archive', {})); archTodoOnly = false; renderArchive();");
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
    w.eval("archive = cleanArchive(store.get('jr_archive', {})); archTodoOnly = false;" +
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

  // ---- 載入時把架上的真日期接回封存 ----
  console.log();
  loadArch({
    // 推定 9/30、架上真日期 9/04（不晚於推定）→ 接上
    L1: fake("L1", "2026-09-30", "2026-09-30", { date_assumed: true }),
    // 已經是真日期：一律不動。同公司同職稱重新刊登會拿到同一個 ID，不能蓋掉你投遞那筆的日期
    L2: { ...fake("L2", "2026-08-01", "2026-08-02"), why: "applied" },
    // 推定 8/01、架上真日期 9/20 晚於推定 → 那是重新刊登的另一筆，不接
    L3: fake("L3", "2026-08-01", "2026-08-01", { date_assumed: true }),
    // 已經是真日期，架上的日期就算更早也不動
    L5: fake("L5", "2026-09-10", "2026-09-11"),
    // 架上也只是推定值 → 不接
    L4: fake("L4", "2026-09-05", "2026-09-05", { date_assumed: true }),
    x1: fake("x1", "2026-09-10", "2026-09-30"),
  }, [mkJob("L1", "2026-09-04"), mkJob("L2", "2026-09-20"), mkJob("L3", "2026-09-20"),
      mkJob("L4", "2026-09-01", { date_assumed: true }), mkJob("L5", "2026-09-05"),
      mkJob("x1", "2026-09-10")]);
  const saved = arch();
  ok(saved.L1.job.date_posted === "2026-09-04" && !saved.L1.job.date_assumed, "推定日期換成架上的真日期並存起來：" + saved.L1.job.date_posted);
  ok(saved.L2.job.date_posted === "2026-08-01", "已記下的真日期不被重新刊登蓋掉：" + saved.L2.job.date_posted);
  ok(saved.L3.job.date_posted === "2026-08-01" && saved.L3.job.date_assumed, "架上日期晚於推定值（重新刊登）不接：" + saved.L3.job.date_posted);
  ok(saved.L5.job.date_posted === "2026-09-10", "已記下的真日期即使架上較早也不改：" + saved.L5.job.date_posted);
  ok(saved.L4.job.date_posted === "2026-09-05" && saved.L4.job.date_assumed, "架上也是推定值時不接");
  ok(JSON.stringify(titles()) === JSON.stringify(["Tx1", "TL5", "TL4", "TL1", "TL2", "TL3"]), "接上後的排序：" + titles().join(","));
  ok(dateOf("TL1") === "2026-09-04", "卡片顯示接上的真日期、不再標「約」");

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
  loadArch({ x1: fake("x1", "2026-09-10", "2026-09-30") }, [mkJob("m2", "2026-09-02")]);
  await importFile({
    m1: fake("m1", "25/09/2026", "2026-09-25", { first_seen: "2026-09-24" }),  // date_posted 壞了，first_seen 還能用
    m2: fake("m2", "2026-13-01", "2026-09-26", { first_seen: "2026-09-30" }),  // 壞了，但架上有真日期接得上
    m3: { job: null, at: "2026-09-26" },                                        // 結構壞掉：略過
  });
  ok(/新增 2 筆，目前共 3 筆/.test(lastAlert || ""), "匯入計數：" + (lastAlert || "").split("\n")[0]);
  ok(/略過 1 筆/.test(lastAlert || ""), "提示略過的壞項目");
  ok(/其中 1 筆有日期不是合理的 YYYY-MM-DD，那些日期欄位已忽略/.test(lastAlert || ""),
     "日期警告只算真的還有問題的那筆，用語不說成「沒有日期」：" + (lastAlert || "").replace(/\n/g, " / "));
  ok(arch().m2.job.date_posted === "2026-09-02", "匯入的職缺若還在架上會接上真日期");
  ok(dateOf("Tm1") === "約 2026-09-24", "壞掉的 date_posted 忽略後退回 first_seen（標「約」）：" + dateOf("Tm1"));

  // 儲存空間滿：整批不匯入，也不能說「匯入完成」
  const before2 = w.localStorage.getItem("jr_archive");
  const realSet3 = w.Storage.prototype.setItem;
  w.Storage.prototype.setItem = () => { throw new Error("QuotaExceededError"); };
  await importFile({ n1: fake("n1", "2026-09-01", "2026-09-01") });
  w.Storage.prototype.setItem = realSet3;
  ok(/匯入失敗/.test(lastAlert || "") && !/匯入完成/.test(lastAlert || ""), "存不進去時提示匯入失敗：" + lastAlert);
  ok(w.localStorage.getItem("jr_archive") === before2 && !w.eval("archive.n1") && !cardOf("Tn1"),
     "存不進去時記憶體、畫面、localStorage 都沒有半套資料");

  ok(pageErrors.length === 0, "頁面腳本沒有拋錯" + (pageErrors.length ? "：" + pageErrors[0].message : ""));

  // ---- 壞掉的封存資料不能讓頁面空白 ----
  console.log();
  const broken = await boot({ jr_archive: {
    b1: null, b2: { job: null }, b3: { job: { title: "no id" } }, b4: "garbage",
    ok1: { job: { id: "ok1", title: "Tok1", company: "C" }, at: "2026-09-01", why: "manual" },  // 舊格式：沒有 sources/categories
  } }, "https://broken.example/");
  const bd = broken.w.document;
  ok(bd.querySelector("#cards").children.length === 3, "主列表照常顯示：" + bd.querySelector("#cards").children.length + " 筆");
  bd.querySelector("#tab-archive").click();
  const bcards = [...bd.querySelector("#arch-cards").children];
  ok(bcards.length === 1 && bcards[0].querySelector(".title").textContent.trim() === "Tok1", "壞掉的項目被丟掉，正常的照常顯示");
  ok(broken.errors.length === 0, "頁面腳本沒有拋錯" + (broken.errors.length ? "：" + broken.errors[0].message : ""));

  console.log("\n失敗: " + fail);
  process.exit(fail ? 1 : 0);
})();
