"""本機抓 APEC（scraper/local_apec.py）與雲端合併（main.local_apec）。

APEC 本身用假的 fetch 代替；git 推送用暫存的本機 repo＋bare remote 實際跑一次。

執行（兩種都可以）：
    .venv/bin/python tests/test_local_apec.py
    .venv/bin/python -m pytest tests/test_local_apec.py
"""
import contextlib
import json
import logging
import pathlib
import subprocess
import sys
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.CRITICAL)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper import local_apec, main, net
from scraper.sources import apec


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


def fake_job(i, title="Chargé de CRM (H/F)"):
    return {"title": title, "company": f"Acme {i}", "location": "Nantes", "description": "CRM, emailing",
            "url": f"https://www.apec.fr/x/{i}", "source": "APEC", "date_posted": "2026-10-07",
            "contract": "CDI", "work_mode": None, "salary": None}


@contextlib.contextmanager
def sandbox(fetch):
    """把 local_apec 的檔案位置導到暫存資料夾，APEC 換成假的 fetch。"""
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        with patched(local_apec, OUT=t / "apec.json", LOCAL=t / "local", RUNS=t / "local" / "runs.json",
                     LATEST=t / "local" / "latest.json"), patched(apec, fetch=fetch), \
                contextlib.redirect_stdout(None):
            yield t


def runs(t):
    return json.loads((t / "local" / "runs.json").read_text(encoding="utf-8"))


# ── 抓取與紀錄 ────────────────────────────────────────────

def test_successful_run_writes_file_and_record():
    """抓到職缺：寫 apec.json、記一筆「成功」，預覽只列會上網站的"""
    jobs = [fake_job(1), fake_job(2), fake_job(3, title="Comptable")]   # Comptable 不屬任何職類
    with sandbox(lambda *a, **k: jobs) as t:
        rec = local_apec.run(push=False)
        data = json.loads((t / "apec.json").read_text(encoding="utf-8"))
        latest = json.loads((t / "local" / "latest.json").read_text(encoding="utf-8"))
        assert rec["status"] == "成功" and rec["fetched"] == 3 and rec["kept"] == 2, rec
        assert data["count"] == 3 and len(data["jobs"]) == 3
        assert len(latest["jobs"]) == 2 and latest["jobs"][0]["url"].startswith("https://www.apec.fr/")
        assert runs(t)[-1]["status"] == "成功"


def test_blocked_run_keeps_previous_file():
    """家裡也被擋：記「被擋」，上一次的 apec.json 不覆寫"""
    def blocked(*a, **k):
        net.report_blocked("APEC", "HTTP 403（DataDome）")
        return []
    with sandbox(blocked) as t:
        (t / "apec.json").write_text('{"generated_at": "old", "jobs": [1]}', encoding="utf-8")
        rec = local_apec.run(push=False)
        assert rec["status"] == "被擋" and "DataDome" in rec["message"], rec
        assert json.loads((t / "apec.json").read_text())["generated_at"] == "old"


def test_crash_is_recorded_not_raised():
    """排程裡沒人看終端機：例外要寫進紀錄，不能讓程式直接死掉"""
    def boom(*a, **k):
        raise RuntimeError("網路斷了")
    with sandbox(boom) as t:
        rec = local_apec.run(push=False)
        assert rec["status"] == "失敗" and "網路斷了" in rec["message"], rec
        assert runs(t)[-1]["status"] == "失敗"


def test_run_history_is_capped():
    """執行紀錄只留最近 MAX_RUNS 筆"""
    with sandbox(lambda *a, **k: [fake_job(1)]) as t, patched(local_apec, MAX_RUNS=3):
        for _ in range(5):
            local_apec.run(push=False)
        assert len(runs(t)) == 3


# ── git 推送 ─────────────────────────────────────────────

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@contextlib.contextmanager
def git_repo(branch="main"):
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        _git(t, "init", "-q", "--bare", "-b", "main", "remote.git")
        _git(t, "clone", "-q", str(t / "remote.git"), "work")
        w = t / "work"
        _git(w, "config", "user.email", "t@example.com")
        _git(w, "config", "user.name", "t")
        _git(w, "checkout", "-q", "-b", "main")
        (w / "docs" / "data").mkdir(parents=True)
        (w / "README.md").write_text("x")
        _git(w, "add", ".")
        _git(w, "commit", "-q", "-m", "init")
        _git(w, "push", "-q", "origin", "main")
        if branch != "main":
            _git(w, "checkout", "-q", "-b", branch)
        with patched(local_apec, ROOT=w, OUT=w / "docs" / "data" / "apec.json"):
            yield t, w


def test_git_push_commits_and_pushes_to_main():
    """在 main 上：commit apec.json 並推到 remote 的 main；內容沒變就不推"""
    with git_repo() as (t, w):
        (w / "docs" / "data" / "apec.json").write_text('{"jobs": []}')
        ok, msg = local_apec.git_push()
        assert ok, msg
        assert "apec.json" in _git(t / "remote.git", "show", "--stat", "--format=%s", "main")
        ok2, msg2 = local_apec.git_push()
        assert ok2 and "不需推送" in msg2, msg2


def test_git_push_refuses_other_branches():
    """不在 main 上就不推：推 HEAD:main 會把其他沒合併的 commit 一起帶上去"""
    with git_repo(branch="feature") as (t, w):
        (w / "docs" / "data" / "apec.json").write_text('{"jobs": []}')
        ok, msg = local_apec.git_push()
        assert not ok and "main" in msg, msg


# ── 排程 ─────────────────────────────────────────────────

def test_schedule_plans_for_each_os():
    """三個平台的排程內容：時間、執行的指令、工作目錄都要對"""
    mac = local_apec.schedule_plan("install", "08:30", system="Darwin")
    plist = next(iter(mac["files"].values()))
    assert "<integer>8</integer>" in plist and "<integer>30</integer>" in plist
    assert "scraper.local_apec" in plist and str(local_apec.ROOT) in plist
    assert ["launchctl", "load", "-w"] == mac["commands"][-1][:3]

    win = local_apec.schedule_plan("install", "08:30", system="Windows")
    cmd = next(iter(win["files"].values()))
    assert f'cd /d "{local_apec.ROOT}"' in cmd and "scraper.local_apec run" in cmd
    assert "/ST" in win["commands"][0] and "08:30" in win["commands"][0]

    lin = local_apec.schedule_plan("install", "08:30", system="Linux")
    assert lin["files"]["crontab"].startswith("30 8 * * * ") and local_apec.CRON_TAG in lin["files"]["crontab"]

    assert local_apec.schedule_plan("uninstall", system="Linux")["files"]["crontab"] == ""
    assert local_apec.schedule_plan("uninstall", system="Windows")["commands"][0][:2] == ["schtasks", "/Delete"]


def test_bad_time_rejected():
    """時間格式錯就報錯，不要裝一個永遠不會跑的排程"""
    for bad in ("25:00", "8h30", "12:60"):
        try:
            local_apec.schedule_plan("install", bad, system="Linux")
        except ValueError:
            continue
        raise AssertionError(bad)


# ── 本地介面 ─────────────────────────────────────────────

def test_ui_server_serves_page_and_state_and_runs():
    """介面：首頁、/api/state、POST /api/run 會在背景執行一次"""
    done = threading.Event()
    with sandbox(lambda *a, **k: [fake_job(1)]) as t, \
            patched(local_apec, run=lambda push=True: done.set(), schedule_status=lambda: "尚未設定"):
        srv = local_apec.make_server(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            page = urllib.request.urlopen(base + "/").read().decode()
            assert "APEC 本機抓取" in page
            state = json.loads(urllib.request.urlopen(base + "/api/state").read())
            assert state["runs"] == [] and state["schedule"] == "尚未設定"
            r = urllib.request.urlopen(urllib.request.Request(base + "/api/run", method="POST"))
            assert r.status == 202
            assert done.wait(5), "背景執行沒有被觸發"
        finally:
            srv.shutdown()
            srv.server_close()
    assert srv.server_address[0] == "127.0.0.1", "只能聽本機"


# ── 雲端合併 ─────────────────────────────────────────────

def _local_file(t, hours_ago, n=2):
    gen = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = t / "apec.json"
    p.write_text(json.dumps({"generated_at": gen, "count": n, "jobs": [fake_job(i) for i in range(n)]}))
    return p


def test_cloud_merges_fresh_local_file():
    """雲端：48 小時內的本機資料併進來，太舊的略過，沒有檔案就當沒有"""
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        with patched(main, LOCAL_APEC=_local_file(t, 3)):
            got = main.local_apec()
            assert got and len(got["jobs"]) == 2
        with patched(main, LOCAL_APEC=_local_file(t, 72)):
            assert main.local_apec() is None
        with patched(main, LOCAL_APEC=t / "nope.json"):
            assert main.local_apec() is None


def test_scrape_all_adds_local_jobs_to_apec():
    """scrape_all：本機資料算進 APEC 的筆數，並登記在 LOCAL_SOURCES（寫進 jobs.json）"""
    import types
    from scraper.sources import france_travail, indeed_jobspy, isarta, wttj
    stub = lambda *a, **k: []
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        with patched(main, LOCAL_APEC=_local_file(t, 1, n=4)), \
                patched(indeed_jobspy, fetch=stub), patched(wttj, fetch=stub), \
                patched(france_travail, fetch=stub), patched(isarta, fetch=stub), patched(apec, fetch=stub):
            raw, stats = main.scrape_all(main.load_cfg(), set())
    assert stats["APEC"] == 4 and len(raw) == 4
    assert main.LOCAL_SOURCES["APEC"]["count"] == 4


if __name__ == "__main__":
    fail = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("✅", fn.__doc__)
            except AssertionError as e:
                fail += 1
                print("❌", fn.__doc__, "→", e)
    print(f"\n失敗: {fail}")
    sys.exit(1 if fail else 0)
