"""在你自己的電腦上抓 APEC（本機排程＋本地介面）。

APEC 自 2026-10-01 起用 DataDome 擋掉 GitHub Actions 的機房 IP，雲端抓不到。
家裡的網路是一般使用者的 IP，用同樣低頻率的查詢可以正常抓。這個模組在本機
跑 APEC，把結果存成 docs/data/apec.json 推上 GitHub；雲端每天的抓取（main.py）
再把它併進網站，照常分類、去重。

用法（在 repo 根目錄、main 分支上）：
    python -m scraper.local_apec run              # 抓一次 APEC 並推上 GitHub
    python -m scraper.local_apec run --no-push    # 只抓、不推（測試用）
    python -m scraper.local_apec ui               # 開本地介面：每次執行的結果、立即執行
    python -m scraper.local_apec install          # 設定每天自動執行（預設 11:00）
    python -m scraper.local_apec install --time 08:30
    python -m scraper.local_apec uninstall        # 取消排程

排程：Mac 用 launchd、Windows 用工作排程器、Linux 用 cron。執行紀錄只存在本機的
local/ 資料夾（不進 git）。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "apec.json"     # 推上 GitHub、給雲端合併用
LOCAL = ROOT / "local"                         # 只在本機：執行紀錄與最近一次的預覽
RUNS = LOCAL / "runs.json"
LATEST = LOCAL / "latest.json"
MAX_RUNS = 200
RESULTS_PER_TERM = 100   # 與雲端（daily.yml 的 RESULTS_PER_TERM）一致
DEFAULT_TIME = "11:00"   # 雲端在巴黎時間 12:00 跑，本機在那之前推上去，中午那輪就會併進網站
TASK_NAME = "JosChasseAPEC"
LAUNCHD_LABEL = "fr.joschasse.apec"
CRON_TAG = "# jos-chasse-apec"

log = logging.getLogger("chasse.local")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)   # 先寫暫存檔再換名：介面同時在讀也不會讀到寫一半的檔案


# ── 抓取 ─────────────────────────────────────────────────

def run(push: bool = True) -> dict:
    """抓一次 APEC；回傳這次的執行紀錄（也會寫進 local/runs.json）。"""
    from scraper import enrich, main, net
    from scraper.sources import apec

    started = _now()
    record = {"started_at": _iso(started), "status": "失敗", "fetched": 0, "kept": 0,
              "pushed": False, "message": ""}
    try:
        cfg = main.load_cfg()
        net.BLOCKED.clear()
        jobs = apec.fetch(cfg["search_terms"], results_per_term=RESULTS_PER_TERM)
        blocked = net.BLOCKED.get("APEC")
        record["fetched"] = len(jobs)

        if not jobs:
            # 沒抓到就不覆寫：上一次的結果比空檔案有用，雲端會依時間判斷新不新鮮
            record["status"] = "被擋" if blocked else "沒有結果"
            record["message"] = blocked or "APEC 沒有回傳任何職缺，保留上一次的結果"
        else:
            kept = [j for j in (enrich.classify(r, cfg) for r in jobs) if j]
            record["kept"] = len(kept)
            record["status"] = "部分被擋" if blocked else "成功"
            if blocked:
                record["message"] = blocked
            _write_json(OUT, {"generated_at": _iso(_now()), "count": len(jobs), "jobs": jobs})
            # 介面用的預覽：會上網站的那些（分數高的在前）
            kept.sort(key=lambda j: -j["score"])
            _write_json(LATEST, {"started_at": record["started_at"], "jobs": [
                {k: j.get(k) for k in ("title", "company", "city", "location", "categories",
                                       "bonus_tags", "score", "date_posted")}
                | {"url": j["sources"][0]["url"]} for j in kept]})
            if push:
                ok, msg = git_push()
                record["pushed"] = ok
                record["message"] = "；".join(m for m in (record["message"], msg) if m)
    except Exception as e:   # 排程裡沒人看終端機：任何錯誤都要進紀錄，介面上才看得到
        log.exception("本機抓取 APEC 失敗")
        record["message"] = f"{type(e).__name__}: {e}"
    record["duration_s"] = round((_now() - started).total_seconds(), 1)

    runs = _read_json(RUNS, [])
    runs.append(record)
    _write_json(RUNS, runs[-MAX_RUNS:])
    log.info("APEC 本機抓取：%s（抓到 %d 筆、會上網站 %d 筆）%s",
             record["status"], record["fetched"], record["kept"], record["message"])
    return record


def git_push() -> tuple[bool, str]:
    """把 docs/data/apec.json commit 並推到 main。回傳 (成功與否, 說明)。"""
    rel = OUT.relative_to(ROOT).as_posix()

    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=180)

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        # 推 HEAD:main 會把這個分支上其他還沒合併的 commit 一起推上去
        return False, f"目前在 {branch!r} 分支，只有在 main 上才會推送（檔案已更新，未推送）"
    if not git("status", "--porcelain", "--", rel).stdout.strip():
        return True, "內容與上次相同，不需推送"
    for args in (("add", "--", rel),
                 ("commit", "-m", f"chore: 本機抓取 APEC {datetime.now():%Y-%m-%d}", "--", rel)):
        r = git(*args)
        if r.returncode:
            return False, f"git {args[0]} 失敗：{(r.stderr or r.stdout).strip()[:300]}"
    # 雲端每天會推 jobs.json，先把它拉下來再推；兩邊改的是不同檔案，不會衝突
    r = git("pull", "--rebase", "--autostash", "origin", "main")
    if r.returncode:
        git("rebase", "--abort")
        return False, f"git pull 失敗：{(r.stderr or r.stdout).strip()[:300]}"
    r = git("push", "origin", "HEAD:main")
    if r.returncode:
        return False, f"git push 失敗：{(r.stderr or r.stdout).strip()[:300]}"
    return True, "已推上 GitHub"


# ── 排程 ─────────────────────────────────────────────────

def _parse_time(hhmm: str) -> tuple[int, int]:
    h, m = (int(x) for x in hhmm.split(":"))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(hhmm)
    return h, m


def schedule_plan(action: str, hhmm: str = DEFAULT_TIME, system: str | None = None) -> dict:
    """排程要做的事（寫哪些檔案、跑哪些指令）。install/uninstall 照這份計畫執行，
    --dry-run 只印出來——測試也用它驗證三個平台。"""
    system = system or platform.system()
    h, m = _parse_time(hhmm)
    py = sys.executable
    files: dict[str, str] = {}
    commands: list[list[str]] = []

    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        commands.append(["launchctl", "unload", str(plist)])   # 已安裝過就先卸載，失敗無妨
        if action == "install":
            # StartCalendarInterval：Mac 睡眠時錯過的那次，醒來後會補跑
            files[str(plist)] = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>-m</string><string>scraper.local_apec</string><string>run</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>
  <key>StandardOutPath</key><string>{LOCAL / "schedule.log"}</string>
  <key>StandardErrorPath</key><string>{LOCAL / "schedule.log"}</string>
</dict>
</plist>
"""
            commands.append(["launchctl", "load", "-w", str(plist)])
        else:
            commands.append(["rm", "-f", str(plist)])
    elif system == "Windows":
        wrapper = LOCAL / "run_apec.cmd"
        if action == "install":
            # 工作排程器不能指定工作目錄，所以包一層 .cmd 先 cd 到 repo
            files[str(wrapper)] = (f'@echo off\r\ncd /d "{ROOT}"\r\n'
                                   f'"{py}" -m scraper.local_apec run >> "{LOCAL / "schedule.log"}" 2>&1\r\n')
            commands.append(["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", TASK_NAME,
                             "/ST", f"{h:02d}:{m:02d}", "/TR", f'"{wrapper}"'])
        else:
            commands.append(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    else:   # Linux 等：cron
        line = (f"{m} {h} * * * cd {shlex.quote(str(ROOT))} && {shlex.quote(py)} -m scraper.local_apec run "
                f">> {shlex.quote(str(LOCAL / 'schedule.log'))} 2>&1 {CRON_TAG}")
        files["crontab"] = line if action == "install" else ""
    return {"system": system, "files": files, "commands": commands}


def apply_schedule(plan: dict) -> list[str]:
    """照計畫寫檔、跑指令；回傳給使用者看的結果。"""
    out = []
    for path, content in plan["files"].items():
        if path == "crontab":
            cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
            lines = [l for l in cur.splitlines() if CRON_TAG not in l] + ([content] if content else [])
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
            out.append("已更新 crontab")
            continue
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        out.append(f"已寫入 {path}")
    for cmd in plan["commands"]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        ok = r.returncode == 0 or cmd[:2] == ["launchctl", "unload"]
        out.append(("✓ " if ok else "✗ ") + " ".join(cmd) + ("" if ok else f"\n  {r.stderr.strip()}"))
    return out


def schedule_status() -> str:
    """給介面顯示：目前有沒有排程。"""
    system = platform.system()
    try:
        if system == "Darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
            return f"已設定（launchd：{plist.name}）" if plist.exists() else "尚未設定"
        if system == "Windows":
            r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True)
            return "已設定（工作排程器）" if r.returncode == 0 else "尚未設定"
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        line = next((l for l in cur.splitlines() if CRON_TAG in l), None)
        if not line:
            return "尚未設定"
        m, h = line.split()[:2]
        return f"已設定（cron，每天 {int(h):02d}:{int(m):02d}）"
    except (OSError, ValueError):
        return "無法判斷"


# ── 本地介面 ─────────────────────────────────────────────

_RUNNING = threading.Lock()


def ui_state() -> dict:
    cloud = _read_json(OUT, {})
    return {
        "runs": list(reversed(_read_json(RUNS, []))),
        "latest": _read_json(LATEST, {"jobs": []}),
        "file": {"generated_at": cloud.get("generated_at"), "count": cloud.get("count", 0)},
        "running": _RUNNING.locked(),
        "schedule": schedule_status(),
    }


def _run_in_background() -> bool:
    if not _RUNNING.acquire(blocking=False):
        return False

    def work():
        try:
            run()
        finally:
            _RUNNING.release()
    threading.Thread(target=work, daemon=True).start()
    return True


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, (Path(__file__).parent / "local_apec_ui.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, json.dumps(ui_state(), ensure_ascii=False, default=str).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/run":
            started = _run_in_background()
            self._send(202 if started else 409,
                       json.dumps({"started": started}).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *args):   # 不要每次輪詢都印一行
        pass


def make_server(port: int) -> ThreadingHTTPServer:
    # 只聽 127.0.0.1：這個介面能觸發抓取與 git push，不該讓區網其他裝置連進來
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    srv = make_server(port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"本地介面：{url}（Ctrl+C 結束）")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ── 指令列 ───────────────────────────────────────────────

def cli(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="python -m scraper.local_apec", description="在本機抓 APEC")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="抓一次 APEC 並推上 GitHub")
    r.add_argument("--no-push", action="store_true", help="只抓、不推")
    u = sub.add_parser("ui", help="開本地介面")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true")
    for name in ("install", "uninstall"):
        s = sub.add_parser(name, help=("設定" if name == "install" else "取消") + "每天自動執行")
        s.add_argument("--time", default=DEFAULT_TIME, help="每天幾點跑（本機時間，HH:MM）")
        s.add_argument("--dry-run", action="store_true", help="只印出會做的事")
    args = p.parse_args(argv)

    if args.cmd == "run":
        rec = run(push=not args.no_push)
        return 0 if rec["status"] in ("成功", "部分被擋") else 1
    if args.cmd == "ui":
        serve(args.port, open_browser=not args.no_browser)
        return 0
    plan = schedule_plan(args.cmd, args.time)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0
    for line in apply_schedule(plan):
        print(line)
    if args.cmd == "install":
        print(f"每天 {args.time}（本機時間）會自動抓 APEC。執行紀錄：python -m scraper.local_apec ui")
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(cli())
