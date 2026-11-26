"""CheckUp_MasterBot — 제안 기능

점검(checkup.py)이 '지금 고장 난 것'을 찾는다면, 여기서는 아래 두 가지를 해요.

[1] 문제 알림에 '이렇게 해보세요' 한 줄 붙이기 (tip_for)
[2] 아직 고장은 아니지만 손봐야 할 것 찾기 (하루 한 번, find_advice)
    a. 불안정   : 최근 실행 중 실패가 자주 섞여 있음 (마지막은 성공이어도)
    b. 느려짐   : 최근 실행 시간이 평소보다 2배 넘게 걸림
    c. 학습 멈춤 : 점검·학습 기록장(CHECKS.md 등)이 오래 안 바뀜 → 주간 점검이 빠졌을 수 있음
    d. 검증 제외 반복 : 다른 사이트와 비교(crosscheck.json)에서 같은 종목이 며칠째 빠짐

⚠️ 이 파일의 어떤 부분이 실패해도 기존 점검·알림은 그대로 돌아가요.
   (checkup.py 가 이 기능을 try 로 감싸서 부르고, 실패하면 건너뛰어요)
"""
import base64
import datetime as dt
import json
import statistics

# ───────────────────────── 설정 ─────────────────────────
FLAKY_MIN_FAILS = 3          # 최근 실행 15번 중 실패가 이만큼 넘으면 '불안정'
SLOW_RATIO = 2.0             # 평소(중간값)보다 이 배수 넘게 오래 걸리면 '느려짐'
SLOW_MIN_SECONDS = 180       # 단, 3분도 안 걸리는 작업은 느려져도 무시
LEARNING_STALE_DAYS = 9      # 기록장이 이만큼 안 바뀌면 '학습 멈춤' (주간 점검이 토요일마다라서 9일)
XCHECK_REPEAT_DAYS = 3       # 같은 종목이 이만큼 연속으로 빠지면 '원인 찾기' 제안
REMIND_DAYS = 7              # 같은 제안은 해결될 때까지 7일마다 다시 알려줘요
DEFAULT_LEARNING_FILES = ["CHECKS.md", "LESSONS.md", "WEEKLY_REVIEW.md", "reports"]
DEFAULT_XCHECK_FILE = "docs/crosscheck.json"

# ───────────────────────── [1] 문제별 해결 방법 ─────────────────────────
TIPS = {
    "실행 실패": "'실행 기록 열기'에서 빨간 ✗ 단계를 눌러 마지막 몇 줄을 Claude에게 보여주세요. 한 번뿐이면 다음 실행에서 저절로 낫는 경우가 많아요.",
    "실행 누락": "GitHub 예약 실행은 가끔 늦거나 빠져요. 다음 시간에도 안 돌면 Actions 화면에서 'Run workflow'로 한 번 직접 돌려 보세요.",
    "자동 실행 꺼짐": "저장소 → Actions → 해당 작업 → 'Enable workflow' 버튼을 누르면 다시 켜져요.",
    "곧 자동 실행 꺼짐": "저장소에 아무 변화라도 한 번 올리면(예: README 한 글자 수정) 60일이 다시 시작돼요. Claude에게 '빈 커밋 올려줘'라고 해도 돼요.",
    "숨은 문제": "실행은 성공이지만 알림이 안 갔을 수 있어요. 텔레그램·ntfy에 그 시간 알림이 왔는지 먼저 확인하고, 안 왔으면 실행 기록을 Claude에게 보여주세요.",
    "0건 지속": "사이트 주소나 화면 구조가 바뀌었을 가능성이 커요. 그 사이트를 직접 열어 공고가 정말 없는지 보고, 있으면 Claude에게 '읽는 방법 고쳐줘'라고 해주세요.",
    "사이트 오류 지속": "사이트가 봇 접속을 막았거나 점검 중일 수 있어요. 하루 더 지켜보고 계속되면 Claude에게 오류 내용을 보여주세요.",
    "점검 불가": "CHECKUP_TOKEN 이 만료됐거나 이 저장소 권한이 빠졌을 수 있어요. GitHub → Settings → Developer settings → 토큰에서 기간과 저장소 목록을 확인해 주세요.",
}


def tip_for(kind):
    tip = TIPS.get(kind)
    return f"\n\n💡 이렇게 해보세요: {tip}" if tip else ""


# ───────────────────────── [2] 미리 찾기 ─────────────────────────
def _parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _duration(run):
    try:
        a = _parse(run.get("run_started_at") or run["created_at"])
        b = _parse(run["updated_at"])
        return max(0.0, (b - a).total_seconds())
    except (KeyError, ValueError, TypeError):
        return None


def _mins(sec):
    return f"{sec / 60:.0f}분" if sec >= 90 else f"{sec:.0f}초"


def advise_runs(runs_cache, out):
    """a·b) 점검 때 이미 읽어 둔 실행 기록으로 불안정·느려짐을 찾아요. (GitHub에 추가로 묻지 않아요)"""
    for (repo, name, wname, link), runs in runs_cache.items():
        done = [r for r in runs if r.get("status") == "completed"
                and r.get("conclusion") not in ("skipped", "cancelled")]
        if len(done) < 5:
            continue
        fails = [r for r in done if r.get("conclusion") in ("failure", "timed_out", "startup_failure")]
        if done[0].get("conclusion") == "success" and len(fails) >= FLAKY_MIN_FAILS:
            out.append((f"flaky:{repo}/{wname}", name, "불안정",
                        f"'{wname}' 작업이 최근 {len(done)}번 중 {len(fails)}번 실패했어요. 지금은 성공했지만 자주 넘어지는 중이에요.",
                        "Claude에게 '최근 실패 원인 공통점 찾아줘'라고 하면 실패한 실행 기록들을 비교해서 고칠 곳을 찾아요.",
                        link))
        ok = [d for d in (_duration(r) for r in done if r.get("conclusion") == "success") if d]
        if len(ok) >= 5:
            latest, usual = ok[0], statistics.median(ok[1:])
            if latest >= SLOW_MIN_SECONDS and usual > 0 and latest > usual * SLOW_RATIO:
                out.append((f"slow:{repo}/{wname}", name, "느려짐",
                            f"'{wname}' 작업이 평소 {_mins(usual)}쯤 걸렸는데 최근엔 {_mins(latest)} 걸렸어요.",
                            "목록이 늘었거나 사이트 응답이 느려졌을 수 있어요. 계속 늘면 시간 제한에 걸려 실패할 수 있으니 Claude에게 '어느 단계가 느려졌는지 봐줘'라고 해주세요.",
                            link))


def _last_commit(api, owner, repo, path):
    data = api(f"/repos/{owner}/{repo}/commits?path={path}&per_page=1")
    if not data:
        return None
    return _parse(data[0]["commit"]["committer"]["date"])


def advise_learning(api, owner, bot, now, out):
    """c) 점검·학습 기록장이 오래 안 바뀌었는지 봐요. (기록장이 없는 봇은 건너뛰어요)
    기록장이 여러 개면 '가장 최근에 바뀐 것' 기준이에요.
    (LESSONS.md 처럼 실수를 찾았을 때만 쓰는 기록장은 오래 그대로여도 정상이라서)"""
    repo, name = bot["repo"], bot["name"]
    newest, newest_path = None, None
    for path in bot.get("learning", DEFAULT_LEARNING_FILES):
        try:
            when = _last_commit(api, owner, repo, path)
        except Exception as e:  # 기록장 하나 못 읽어도 다른 건 계속
            print(f"  (제안) {repo}/{path} 기록 확인 실패: {e}")
            continue
        if when and (not newest or when > newest):
            newest, newest_path = when, path
    if not newest:
        return
    days = (now - newest).days
    if days >= LEARNING_STALE_DAYS:
        out.append((f"learn:{repo}", name, "학습 기록 멈춤",
                    f"점검·학습 기록장이 {days}일째 그대로예요 (가장 최근: '{newest_path}'). 매주 하던 점검·학습이 빠졌을 수 있어요.",
                    f"Claude에게 '{name} 주간 점검해줘'라고 하면 밀린 점검을 하고 기록장을 채워요.",
                    f"https://github.com/{owner}/{repo}/blob/main/{newest_path}"))


def advise_xcheck(api, owner, bot, today, memory, out):
    """d) 다른 사이트와 비교했을 때 빠진 종목이 며칠째 계속 빠지는지 봐요."""
    repo, name = bot["repo"], bot["name"]
    path = bot.get("crosscheck", DEFAULT_XCHECK_FILE)
    try:
        meta = api(f"/repos/{owner}/{repo}/contents/{path}")
        data = json.loads(base64.b64decode(meta.get("content", "")).decode("utf-8"))
    except Exception:
        return  # 이 파일이 없는 봇이 대부분이라 조용히 넘어가요
    items = [f"비교에서 뺌: {x}" for x in data.get("excluded") or []]
    items += [f"최신 가격 못 받음: {x}" for x in data.get("stale_why") or []]
    seen = memory.setdefault(repo, {})
    for x in items:
        rec = seen.setdefault(x, {"first": today, "days": 0, "last": ""})
        if rec["last"] != today:
            rec["days"] += 1
            rec["last"] = today
    for x in list(seen):
        if x not in items:
            del seen[x]  # 오늘 안 보이면 해결된 거라 지워요
    long = [(x, r["days"]) for x, r in seen.items() if r["days"] >= XCHECK_REPEAT_DAYS]
    if long:
        lines = "\n".join(f"- {x} ({d}일째)" for x, d in sorted(long, key=lambda t: -t[1])[:6])
        more = f"\n- 그 밖에 {len(long) - 6}건" if len(long) > 6 else ""
        out.append((f"xcheck:{repo}", name, "검증 제외 반복",
                    f"다른 사이트와 숫자가 안 맞아 빠진 종목이 며칠째 그대로예요:\n{lines}{more}",
                    f"하루 이틀은 정상이지만 오래가면 자료 오류(액면분할 등)일 수 있어요. Claude에게 '{name} 검증에서 빠진 종목 원인 찾아줘'라고 해주세요.",
                    f"https://github.com/{owner}/{repo}/blob/main/{path}"))


def find_advice(api, config, runs_cache, state, now, kst):
    """하루 한 번 불러요. 새로 생긴 제안과 계속되는 제안 개수를 돌려줘요."""
    out = []
    owner = config["owner"]
    today = now.astimezone(kst).date().isoformat()
    xmem = state.setdefault("xcheck_seen", {})

    steps = [("실행 기록", lambda: advise_runs(runs_cache, out))]
    for bot in config["bots"]:
        steps.append((f"{bot['name']} 기록장", lambda b=bot: advise_learning(api, owner, b, now, out)))
        steps.append((f"{bot['name']} 검증", lambda b=bot: advise_xcheck(api, owner, b, today, xmem, out)))
    for label, fn in steps:
        try:
            fn()
        except Exception as e:  # 하나가 실패해도 나머지 제안은 계속 찾아요
            print(f"  (제안) {label} 확인 중 오류: {e}")

    # 같은 제안 반복 방지: 처음 찾았을 때 + 해결 안 되면 7일마다
    old = state.get("advice", {})
    new_state, to_send = {}, []
    for key, name, kind, msg, tip, link in out:
        prev = old.get(key)
        sent = prev.get("sent") if prev else None
        if not sent or (now - _parse(sent)).days >= REMIND_DAYS:
            to_send.append((name, kind, msg, tip, link, bool(prev)))
            sent = now.isoformat(timespec="minutes")
        new_state[key] = {"name": name, "kind": kind, "sent": sent}
    resolved = [v for k, v in old.items() if k not in new_state]
    state["advice"] = new_state
    return to_send, resolved, len(new_state)


def format_advice(to_send, resolved):
    parts = []
    for name, kind, msg, tip, link, again in to_send:
        head = f"• {name} — {kind}" + (" (아직 그대로예요)" if again else "")
        parts.append(f"{head}\n{msg}\n💡 {tip}\n🔗 {link}")
    if resolved:
        parts.append("✅ 해결된 제안: " + ", ".join(f"{v['name']} {v['kind']}" for v in resolved))
    return "\n\n".join(parts)
