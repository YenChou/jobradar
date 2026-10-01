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

setTimeout(() => {
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

  // ---- 排序：待聯絡優先，同組內依公告日期新到舊，同一天再依封存日 ----
  console.log();
  const titles = () => cards().map(c => c.querySelector(".title").textContent.trim());
  const fake = (id, posted, at, extra) => ({
    job: { ...data.jobs[0], id, title: "T" + id, date_posted: posted, first_seen: posted, ...extra }, at, why: "manual" });
  const real = data.jobs.find(j => j.date_posted && j.date_posted < "2026-09-05" && !j.date_assumed);
  const loadArch = obj => {
    w.localStorage.setItem("jr_archive", JSON.stringify(obj));
    $("#aq").value = "";
    w.eval("archive = store.get('jr_archive', {}); archTodoOnly = false; renderArchive();");
  };
  loadArch({
    x1: fake("x1", "2026-09-01", "2026-09-30"),
    x2: fake("x2", "2026-09-20", "2026-09-02"),
    x3: fake("x3", "2026-09-10", "2026-09-15"),
    x4: { ...fake("x4", "2026-09-25", "2026-09-26"), contacted: true, contacted_at: "2026-09-27" },
    // 舊資料沒有 date_posted，要退回 first_seen
    x5: fake("x5", null, "2026-09-01", { first_seen: "2026-09-12" }),
    // 與 x3 同一天公告：封存日較新的排前面
    x6: fake("x6", "2026-09-10", "2026-09-20"),
    // 匯入的備份被手改成數字日期：不能讓整個分頁炸掉，當成沒有日期排最後
    x7: fake("x7", 20260920, "2026-09-03", { first_seen: 20260920 }),
  });
  ok(JSON.stringify(titles()) === JSON.stringify(["Tx2", "Tx5", "Tx6", "Tx3", "Tx1", "Tx7", "Tx4"]),
     "待聯絡優先、組內依公告日期新到舊、first_seen 後備、同日看封存日、非字串日期不炸：" + titles().join(","));
  const x7 = cards().find(c => c.querySelector(".title").textContent.trim() === "Tx7");
  ok(x7.querySelector(".date").textContent === "", "非字串日期不當成日期顯示：「" + x7.querySelector(".date").textContent + "」");

  // 快照裡的推定日期（偏新）：職缺還在架上時改用 jobs.json 的真日期，並寫回封存
  ok(!!real, "測試資料裡有一筆真實公告日早於 9/5 的職缺");
  loadArch({
    x1: fake("x1", "2026-09-10", "2026-09-30"),
    [real.id]: { job: { ...real, date_posted: "2026-09-30", date_assumed: true }, at: "2026-09-30", why: "applied" },
  });
  ok(JSON.stringify(titles()) === JSON.stringify(["Tx1", real.title.trim()]),
     "推定日期被架上的真日期取代後排到正確位置：" + titles().join(" | "));
  const saved = arch()[real.id].job;
  ok(saved.date_posted === real.date_posted && !saved.date_assumed, "真日期寫回封存快照 " + saved.date_posted);
  const realCard = cards().find(c => c.querySelector(".title").textContent.trim() === real.title.trim());
  ok(realCard.querySelector(".date").textContent === real.date_posted, "卡片顯示真日期、不再標「約」：" + realCard.querySelector(".date").textContent);

  console.log("\n失敗: " + fail);
  process.exit(fail ? 1 : 0);
}, 300);
