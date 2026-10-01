// 封存頁的「LinkedIn 聯絡」追蹤：投遞後要聯絡 HR 或該 team 的人，
// 勾起來就知道哪些還沒做。
//
// 執行：cd tests && npm i jsdom && node test_archive_contact.js
const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");
const ROOT = path.join(__dirname, "..", "docs");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const data = JSON.parse(fs.readFileSync(path.join(ROOT, "data/jobs.json"), "utf8"));

let lastAlert = null;
const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "https://example.org/",
  beforeParse(w){
    w.fetch = () => Promise.resolve({ ok:true, json: () => Promise.resolve(data) });
    w.alert = m => { lastAlert = m; };
  },
});
const w = dom.window;

// 測試本身拋錯（例如資料不如預期）時要明確失敗，不能連摘要都沒印
process.on("unhandledRejection", e => { console.log("❌ 測試拋錯：" + (e && e.stack || e)); process.exit(1); });

setTimeout(async () => {
  const d = w.document, $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
  let fail = 0;
  const ok = (c, m) => { console.log((c?"✅":"❌")+" "+m); if(!c) fail++; };
  const arch = () => JSON.parse(w.localStorage.getItem("jr_archive") || "{}");

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
  w.eval("archive = store.get('jr_archive', {}); renderArchive();");
  ok(cards().some(c => c.querySelector(".b.todo")), "舊封存資料（無 contacted 欄位）視為待聯絡，不會炸");

  // localStorage 寫入失敗時要還原，否則畫面說「已聯絡」但重載後消失
  console.log();
  w.eval("archive = store.get('jr_archive', {}); archTodoOnly = false; renderArchive();");
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

  // ---- 排序與日期 ----
  // 這段全部用自己造的資料，不讀每天會變的 jobs.json：哪天資料換了也不會突然變紅。
  console.log();
  const titles = () => cards().map(c => c.querySelector(".title").textContent.trim());
  const cardOf = t => cards().find(c => c.querySelector(".title").textContent.trim() === t);
  const mkJob = (id, posted, extra) => ({
    id, title: "T" + id, company: "C", location: "", city: "", region: "other",
    categories: [], bonus_tags: [], sources: [{ name: "S", url: "https://example.org/" + id }],
    date_posted: posted, first_seen: posted, ...extra });
  const fake = (id, posted, at, extra) => ({ job: mkJob(id, posted, extra), at, why: "manual" });
  const loadArch = (obj, liveJobs) => {
    w.localStorage.setItem("jr_archive", JSON.stringify(obj));
    w.__live = liveJobs || [];
    $("#aq").value = "";
    w.eval("archive = store.get('jr_archive', {}); archTodoOnly = false;" +
           "DATA = { ...DATA, jobs: window.__live }; syncLive(); renderArchive();");
  };

  loadArch({
    x1: fake("x1", "2026-09-01", "2026-09-30"),
    x2: fake("x2", "2026-09-20", "2026-09-02"),
    x3: fake("x3", "2026-09-10", "2026-09-15"),
    x4: { ...fake("x4", "2026-09-25", "2026-09-26"), contacted: true, contacted_at: "2026-09-27" },
    // 與 x3 同一天公告：封存日較新的排前面
    x6: fake("x6", "2026-09-10", "2026-09-20"),
    // 推定日期（已下架、沒機會補真日期）：一定偏新，排在真日期之後
    x8: fake("x8", "2026-09-29", "2026-09-29", { date_assumed: true }),
    // 舊資料沒有 date_posted、只有 first_seen：也是推定值
    x5: fake("x5", null, "2026-09-01", { first_seen: "2026-09-12" }),
    // 匯入的備份被手改：數字、其他日期格式都當成沒有日期，排最後、不顯示
    x7: fake("x7", 20260920, "2026-09-03", { first_seen: 20260920 }),
    x9: fake("x9", "9/25/2026", "2026-09-04", { first_seen: "9/25/2026" }),
  });
  ok(JSON.stringify(titles()) === JSON.stringify(["Tx2", "Tx6", "Tx3", "Tx1", "Tx8", "Tx5", "Tx9", "Tx7", "Tx4"]),
     "待聯絡優先；組內真日期新到舊、同日看封存日；推定日期其次；無效日期最後：" + titles().join(","));
  ok(cardOf("Tx8").querySelector(".date").textContent === "約 2026-09-29", "推定日期標「約」");
  ok(cardOf("Tx5").querySelector(".date").textContent === "約 2026-09-12", "只有 first_seen 也標「約」");
  ok(cardOf("Tx2").querySelector(".date").textContent === "2026-09-20", "真日期不標「約」");
  for (const t of ["Tx7", "Tx9"])
    ok(cardOf(t).querySelector(".date").textContent === "", t + " 的無效日期不顯示：「" + cardOf(t).querySelector(".date").textContent + "」");

  // 職缺還在架上：用 jobs.json 的真日期取代快照裡推定的或過時的日期，並寫回封存
  loadArch({
    x1: fake("x1", "2026-09-10", "2026-09-30"),
    L1: fake("L1", "2026-09-30", "2026-09-30", { date_assumed: true }),   // 推定 → 真日期
    L2: fake("L2", "2026-09-12", "2026-09-12"),                          // 真日期被 scraper 修正
    L3: fake("L3", "2026-09-05", "2026-09-05"),                          // 架上仍是推定：不動
  }, [mkJob("L1", "2026-09-04"), mkJob("L2", "2026-09-08"), mkJob("L3", "2026-09-28", { date_assumed: true })]);
  const saved = arch();
  ok(saved.L1.job.date_posted === "2026-09-04" && !saved.L1.job.date_assumed, "推定日期換成真日期並寫回：" + saved.L1.job.date_posted);
  ok(saved.L2.job.date_posted === "2026-09-08", "被修正的真日期也跟著更新：" + saved.L2.job.date_posted);
  ok(saved.L3.job.date_posted === "2026-09-05", "架上只有推定日期時不覆寫：" + saved.L3.job.date_posted);
  ok(JSON.stringify(titles()) === JSON.stringify(["Tx1", "TL2", "TL3", "TL1"]),
     "補完日期後排到正確位置：" + titles().join(","));
  ok(cardOf("TL1").querySelector(".date").textContent === "2026-09-04", "卡片顯示真日期、不再標「約」");

  // 補日期只在載入資料時做，搜尋框打字重繪不會寫 localStorage
  const realSet2 = w.Storage.prototype.setItem;
  let writes = 0;
  w.Storage.prototype.setItem = function(){ writes++; return realSet2.apply(this, arguments); };
  const idxBefore = w.eval("liveById");
  for (const ch of ["T", "TL"]) { $("#aq").value = ch; $("#aq").dispatchEvent(new w.Event("input")); }
  w.Storage.prototype.setItem = realSet2;
  ok(writes === 0, "搜尋重繪不寫 localStorage（寫了 " + writes + " 次）");
  ok(w.eval("liveById") === idxBefore, "搜尋重繪不重建上架索引");
  $("#aq").value = "";

  // 匯入：日期格式不對要提示，不能靜靜排錯；還在架上的一樣補真日期
  lastAlert = null;
  w.archiveImport(new w.File([JSON.stringify({ archive: {
    m1: fake("m1", "25/09/2026", "2026-09-25"),
    m2: fake("m2", "2026-09-26", "2026-09-26", { date_assumed: true }),
  } })], "backup.json", { type: "application/json" }));
  await new Promise(r => setTimeout(r, 100));
  ok(/新增 2 筆/.test(lastAlert || "") && /1 筆的日期不是 YYYY-MM-DD/.test(lastAlert || ""),
     "匯入時提示日期格式不對：" + (lastAlert || "").replace(/\n/g, " / "));
  w.eval("DATA = { ...DATA, jobs: window.__live.concat([" + JSON.stringify(mkJob("m2", "2026-09-02")) + "]) }; syncLive();");
  ok(arch().m2.job.date_posted === "2026-09-02", "匯入的職缺若還在架上會補真日期");

  console.log("\n失敗: " + fail);
  process.exit(fail ? 1 : 0);
}, 300);
