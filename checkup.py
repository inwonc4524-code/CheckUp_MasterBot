"""CheckUp_MasterBot — 내 봇들을 대신 지켜보는 점검·검증 봇

1시간마다 GitHub에서 각 봇의 실행 기록을 읽어서 아래를 확인해요.

[점검]
  1. 실행 실패   : 마지막 실행이 에러로 끝났는지
  2. 실행 누락   : 예정된 시간에 실행이 안 됐는지 (각 봇의 cron 시간표를 자동으로 읽어서 비교)
  3. 자동 실행 꺼짐 : GitHub가 예약 실행을 꺼버렸는지 (오래 활동이 없으면 GitHub가 끔)
[검증]
  4. 숨은 실패   : 실행은 '성공'인데 실행 기록(로그) 안에 알림 전송 실패·프로그램 오류가 있는지
  5. 숨은 단계 실패 : 실패해도 넘어가게 해둔 단계가 실패했는지

문제가 생기면 ntfy + 텔레그램으로 바로 알려주고, 고쳐지면 '복구' 알림을 보내요.
같은 문제로 알림이 계속 반복되지 않게 state.json 에 기억해요.
매일 아침 8시쯤 전체 봇 상태 요약을 한 번 보내요. (이 요약이 안 오면 점검 봇이 멈춘 거예요)

실행:  python checkup.py            (평소)
       python checkup.py --report   (지금 바로 요약 보내기)
"""
import base64
import datetime as dt
import html as htmllib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
CONFIG = json.loads((HERE / "bots.json").read_text(encoding="utf-8"))

GH_TOKEN = (os.environ.get("CHECKUP_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHATS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]

GRACE = dt.timedelta(hours=2)        # GitHub 예약 실행은 자주 늦어서, 예정 시간 2시간 뒤까지는 기다려요
SUMMARY_HOUR = 8                     # 매일 요약 보내는 시각 (한국 시간)
STALE_REPO_DAYS = 50                 # 저장소 활동이 이만큼 없으면 경고 (GitHub는 60일이면 예약 실행을 끔)

NOW = dt.datetime.now(UTC)
NOTIFY_ERRORS = []

# 로그에서 찾을 '숨은 실패' 표시들
SERIOUS_PATTERNS = [
    ("프로그램 오류", re.compile(r"Traceback \(most recent call last\)")),
    ("알림 채널 없음", re.compile(r"알림 채널이 하나도 없어서")),
    ("텔레그램 전송 실패", re.compile(r"텔레그램 (전송|보내기) 실패")),
    ("ntfy 전송 실패", re.compile(r"ntfy (전송|보내기) 실패")),
    ("알림 전송 실패", re.compile(r"알림 전송 실패")),
]
# 위 줄이라도 이런 말이 붙어 있으면 '다시 시도 중'이거나 대체 방법으로 보낸 거라 무시해요
IGNORE_IN_LINE = re.compile(r"\(\d+/\d+\)|글자만 보내요|다시 시도")


# ───────────────────────── GitHub 읽기 ─────────────────────────
class ApiError(Exception):
    pass


def api(path, raw=False):
    url = path if path.startswith("http") else "https://api.github.com" + path
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code >= 500:
            last = f"{r.status_code}"
            time.sleep(3 * (attempt + 1))
            continue
        if not r.ok:
            raise ApiError(f"{r.status_code} {path}")
        return r.text if raw else r.json()
    raise ApiError(f"응답 없음 {path}: {last}")


# ───────────────────────── cron 시간표 읽기 ─────────────────────────
def _parse_field(text, lo, hi):
    vals = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = int(part)
            b = hi if step > 1 else a
        vals.update(range(a, b + 1, step))
    return vals


class Cron:
    def __init__(self, expr):
        f = expr.split()
        if len(f) != 5:
            raise ValueError(expr)
        self.expr = expr
        self.minute = _parse_field(f[0], 0, 59)
        self.hour = _parse_field(f[1], 0, 23)
        self.dom = _parse_field(f[2], 1, 31)
        self.month = _parse_field(f[3], 1, 12)
        self.dow = {d % 7 for d in _parse_field(f[4], 0, 7)}
        self.dom_any = f[2] == "*"
        self.dow_any = f[4] == "*"

    def _day_ok(self, t):
        if t.month not in self.month:
            return False
        dow = (t.weekday() + 1) % 7  # cron: 일=0
        d_ok, w_ok = t.day in self.dom, dow in self.dow
        if self.dom_any or self.dow_any:
            return d_ok and w_ok
        return d_ok or w_ok  # 둘 다 정해져 있으면 cron 규칙상 '둘 중 하나'

    def last_before(self, t, max_days=35):
        """t(UTC) 이전 가장 최근 예정 시각."""
        t = t.replace(second=0, microsecond=0)
        day = t.replace(hour=0, minute=0)
        for _ in range(max_days + 1):
            if self._day_ok(day):
                for h in sorted(self.hour, reverse=True):
                    for m in sorted(self.minute, reverse=True):
                        c = day.replace(hour=h, minute=m)
                        if c <= t:
                            return c
            day -= dt.timedelta(days=1)
        return None


CRON_RE = re.compile(r"""^\s*-\s*cron:\s*['"]([^'"]+)['"]""", re.M)


def workflow_crons(owner, repo, path):
    try:
        text = api(f"/repos/{owner}/{repo}/contents/{path}", raw=False)
    except ApiError:
        return []
    body = base64.b64decode(text.get("content", "")).decode("utf-8", "replace")
    if not re.search(r"^\s*schedule\s*:", body, re.M):
        return []
    out = []
    for expr in CRON_RE.findall(body):
        try:
            out.append(Cron(expr))
        except ValueError:
            pass
    return out


# ───────────────────────── 로그 검증 ─────────────────────────
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s?")


def scan_logs(owner, repo, run_id):
    """실행 기록(로그)과 단계 결과를 읽어서 숨은 실패를 찾아요."""
    problems = []
    jobs = api(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=50").get("jobs", [])
    for job in jobs:
        for st in job.get("steps") or []:
            if st.get("conclusion") == "failure" and job.get("conclusion") == "success":
                problems.append(f"'{st.get('name')}' 단계가 실패했지만 넘어갔어요")
        try:
            log = api(f"/repos/{owner}/{repo}/actions/jobs/{job['id']}/logs", raw=True)
        except ApiError as e:
            print(f"  로그 읽기 실패 ({repo} {job['id']}): {e}")
            continue
        found = {}
        for line in log.splitlines():
            line = TS_RE.sub("", line).strip()
            if IGNORE_IN_LINE.search(line):
                continue
            for label, pat in SERIOUS_PATTERNS:
                if pat.search(line):
                    found.setdefault(label, line[:120])
        for label, sample in found.items():
            problems.append(f"{label}: {sample}" if label != "프로그램 오류" else "프로그램 오류(Traceback)가 기록돼 있어요")
    return problems


# ───────────────────────── 알림 (ntfy + 텔레그램) ─────────────────────────
def send_ntfy(title, message, click=None, priority=3, tags=None):
    body = {"topic": TOPIC, "title": title, "message": message, "priority": priority}
    if click:
        body["click"] = click
    if tags:
        body["tags"] = tags
    try:
        r = requests.post("https://ntfy.sh", json=body, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        print("ntfy 보내기 실패:", e)
        NOTIFY_ERRORS.append("ntfy")


def send_telegram(title, message, click=None, priority=3):
    text = f"<b>{htmllib.escape(title)}</b>\n\n{htmllib.escape(message)}"
    if len(text) > 4000:
        text = text[:4000] + "…"
    for chat_id in TG_CHATS:
        body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": True, "disable_notification": priority <= 2}
        if click:
            body["reply_markup"] = {"inline_keyboard": [[{"text": "👉 실행 기록 열기", "url": click}]]}
        for attempt in range(3):
            try:
                r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=body, timeout=(10, 60))
                if r.status_code == 429:
                    time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)) + 1)
                    continue
                if not r.ok:
                    raise requests.RequestException(f"{r.status_code} {r.text[:150]}")
                break
            except requests.RequestException as e:
                if attempt == 2:
                    print("텔레그램 보내기 실패:", str(e).replace(TG_TOKEN, "***"))
                    NOTIFY_ERRORS.append("telegram")
                else:
                    time.sleep(5 * (attempt + 1))
        time.sleep(0.5)


def notify(title, message, click=None, priority=3, tags=None):
    print("\n=== 알림 ===\n" + title + "\n" + message + (f"\n{click}" if click else ""))
    if not TOPIC and not (TG_TOKEN and TG_CHATS):
        print("(알림 채널이 하나도 없어서 실제로 보내지는 않았어요)")
        NOTIFY_ERRORS.append("no-channel")
        return
    if TOPIC:
        send_ntfy(title, message, click, priority, tags)
    if TG_TOKEN and TG_CHATS:
        send_telegram(title, message, click, priority)


# ───────────────────────── 점검 본체 ─────────────────────────
def kst(t):
    return t.astimezone(KST).strftime("%m/%d %H:%M")


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def check_bot(owner, bot, state, issues):
    """봇 하나를 점검해서 '지금 있는 문제'를 issues 에 넣고, 상태 요약 한 줄을 돌려줘요."""
    repo, name = bot["repo"], bot["name"]
    try:
        info = api(f"/repos/{owner}/{repo}")
        wfs = api(f"/repos/{owner}/{repo}/actions/workflows?per_page=100").get("workflows", [])
    except ApiError as e:
        issues.append((f"access:{repo}", name, "점검 불가",
                       f"GitHub에서 이 봇을 읽을 수 없어요 ({e}). CHECKUP_TOKEN 권한에 이 저장소가 들어 있는지 확인해 주세요.",
                       f"https://github.com/{owner}/{repo}"))
        return f"❓ {name}: 읽기 실패"

    pushed = parse_time(info["pushed_at"])
    if NOW - pushed > dt.timedelta(days=STALE_REPO_DAYS):
        issues.append((f"idle:{repo}", name, "곧 자동 실행 꺼짐",
                       f"마지막 활동이 {(NOW - pushed).days}일 전이에요. 60일이 넘으면 GitHub가 예약 실행을 꺼요.",
                       f"https://github.com/{owner}/{repo}/actions"))

    last_ok, n_sched, bad = None, 0, 0
    for wf in wfs:
        crons = workflow_crons(owner, repo, wf["path"])
        if not crons:
            continue  # 버튼으로만 실행하는 작업은 점검하지 않아요
        n_sched += 1
        wname = wf["name"]
        wkey = f"{repo}/{wf['path'].split('/')[-1]}"
        link = f"https://github.com/{owner}/{repo}/actions/workflows/{wf['path'].split('/')[-1]}"

        # 3) 자동 실행 꺼짐
        if wf.get("state") != "active":
            issues.append((f"disabled:{wkey}", name, "자동 실행 꺼짐",
                           f"'{wname}' 작업이 꺼져 있어요 (상태: {wf.get('state')}). Actions 화면에서 'Enable workflow'를 눌러 주세요.", link))
            bad += 1
            continue

        runs = api(f"/repos/{owner}/{repo}/actions/workflows/{wf['id']}/runs?per_page=15").get("workflow_runs", [])
        completed = [r for r in runs if r["status"] == "completed" and r["conclusion"] not in ("skipped", "cancelled")]
        latest = completed[0] if completed else None

        # 1) 실행 실패
        if latest and latest["conclusion"] in ("failure", "timed_out", "startup_failure"):
            fails = 0
            for r in completed:
                if r["conclusion"] in ("failure", "timed_out", "startup_failure"):
                    fails += 1
                else:
                    break
            issues.append((f"fail:{wkey}", name, "실행 실패",
                           f"'{wname}' 작업이 {kst(parse_time(latest['created_at']))} 실행에서 실패했어요"
                           + (f" (연속 {fails}번째)" if fails > 1 else "") + ".", latest["html_url"]))
            bad += 1
        elif latest:
            t = parse_time(latest["created_at"])
            last_ok = t if not last_ok or t > last_ok else last_ok

        # 2) 실행 누락 — 예정 시간(2시간 여유)이 지났는데 그 뒤로 실행이 하나도 없으면
        #    (짝으로 넣어둔 '예비 실행'을 GitHub가 건너뛰는 경우가 있어서, 예정 45분 전부터 시작된 실행도 인정해요)
        dues = [d for d in (c.last_before(NOW - GRACE) for c in crons) if d]
        due = max(dues) if dues else None
        if due:
            started_after = [r for r in runs if parse_time(r["created_at"]) >= due - dt.timedelta(minutes=45)]
            if not started_after:
                issues.append((f"miss:{wkey}", name, "실행 누락",
                               f"'{wname}' 작업이 {kst(due)} 예정이었는데 지금까지 실행되지 않았어요.", link))
                bad += 1

        # 4·5) 숨은 실패 — 성공한 실행의 로그 확인 (같은 실행은 한 번만 읽어요)
        if latest and latest["conclusion"] == "success":
            scanned = state.setdefault("scanned", {})
            if scanned.get(wkey, {}).get("run") != latest["id"]:
                try:
                    probs = scan_logs(owner, repo, latest["id"])
                except ApiError as e:
                    print(f"  로그 확인 실패 {wkey}: {e}")
                    probs = None
                if probs is not None:
                    scanned[wkey] = {"run": latest["id"], "problems": probs}
            probs = scanned.get(wkey, {}).get("problems") or []
            if probs and scanned[wkey]["run"] == latest["id"]:
                issues.append((f"hidden:{wkey}", name, "숨은 문제",
                               f"'{wname}' 작업은 성공으로 끝났지만 기록 안에 문제가 있어요:\n- " + "\n- ".join(probs[:4]),
                               latest["html_url"]))
                bad += 1

    if n_sched == 0:
        return f"➖ {name}: 예약 실행 작업 없음"
    when = f"마지막 정상 {kst(last_ok)}" if last_ok else "정상 실행 기록 없음"
    return (f"✅ {name}: {when}" if bad == 0 else f"⚠️ {name}: 문제 {bad}건 · {when}")


def main():
    force_report = "--report" in sys.argv
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    active = state.setdefault("active", {})  # 지금 알려둔 문제들 {키: 내용}

    owner = CONFIG["owner"]
    issues, lines = [], []
    for bot in CONFIG["bots"]:
        print(f"\n▶ {bot['name']} ({bot['repo']}) 점검")
        try:
            lines.append(check_bot(owner, bot, state, issues))
        except ApiError as e:
            lines.append(f"❓ {bot['name']}: 점검 중 오류")
            print("  점검 중 오류:", e)

    # 새로 생긴 문제 → 바로 알림 (같은 문제가 계속되는 동안은 다시 안 보내요. 아침 요약에서 다시 알려줘요)
    current = {}
    for key, name, kind, msg, link in issues:
        current[key] = {"name": name, "kind": kind, "msg": msg, "link": link}
        if key in active:
            continue
        notify(f"🚨 {name} — {kind}", msg, link, priority=4, tags=["rotating_light"])

    # 사라진 문제 → 복구 알림
    for key, prev in active.items():
        if key not in current and not key.startswith("access:"):
            notify(f"✅ {prev['name']} — 복구됨", f"'{prev['kind']}' 문제가 해결됐어요.", priority=2, tags=["white_check_mark"])
    state["active"] = current

    # 매일 아침 요약
    today = NOW.astimezone(KST).date().isoformat()
    if force_report or (NOW.astimezone(KST).hour >= SUMMARY_HOUR and state.get("summary_date") != today):
        head = "모든 봇이 정상이에요 👍" if not current else f"지금 확인이 필요한 문제 {len(current)}건이 있어요."
        body = head + "\n\n" + "\n".join(lines)
        if current:
            body += "\n\n[진행 중인 문제]\n" + "\n".join(f"- {v['name']}: {v['kind']}" for v in current.values())
        notify(f"🩺 봇 점검 요약 ({NOW.astimezone(KST).strftime('%m/%d')})", body, priority=3, tags=["stethoscope"])
        if not force_report:
            state["summary_date"] = today

    state["last_run"] = NOW.astimezone(KST).isoformat(timespec="minutes")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + "\n".join(lines))
    if NOTIFY_ERRORS:
        print("알림 보내기 문제:", NOTIFY_ERRORS)
        sys.exit(1)  # 알림이 안 가면 이 작업 자체를 '실패'로 표시 → GitHub가 메일로 알려줘요


if __name__ == "__main__":
    main()
