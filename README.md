# CheckUp_MasterBot 🩺

내 봇들을 1시간마다 대신 점검·검증하고, 문제가 있으면 **ntfy + 텔레그램**으로 알려주는 봇이에요.

## 무엇을 확인하나요

| 구분 | 내용 |
|---|---|
| 실행 실패 | 마지막 실행이 에러로 끝났는지 |
| 실행 누락 | 예정된 시간(각 봇의 cron 시간표를 자동으로 읽음)이 2시간 넘게 지났는데 실행이 안 됐는지 |
| 자동 실행 꺼짐 | GitHub가 예약 실행을 꺼버렸는지, 곧 꺼질 위험(활동 50일 이상 없음)이 있는지 |
| 숨은 실패 (검증) | 실행은 '성공'인데 기록 안에 알림 전송 실패, 프로그램 오류, 알림 채널 없음, 실패한 단계가 있는지 |

- 문제가 새로 생기면 바로 알림 → 고쳐지면 '복구됨' 알림
- 같은 문제는 반복해서 보내지 않아요
- **매일 아침 8시쯤** 전체 상태 요약 (이 요약이 안 오면 점검 봇 자체가 멈춘 거예요)
- 알림 보내기에 실패하면 이 작업이 '실패'로 표시돼서 GitHub가 메일로 알려줘요

## 💡 개선 제안 (advisor.py)

문제 알림마다 **'💡 이렇게 해보세요'** 한 줄이 붙어요. 그리고 **하루 한 번(아침 요약 때)** 아직 고장은 아니지만 손봐야 할 것을 찾아 **'💡 봇 개선 제안'** 알림을 보내요.

| 제안 | 언제 |
|---|---|
| 불안정 | 마지막은 성공했지만 최근 실행 중 3번 넘게 실패가 섞여 있을 때 |
| 느려짐 | 최근 실행이 평소보다 2배 넘게(3분 이상) 걸렸을 때 |
| 학습 기록 멈춤 | 점검·학습 기록장(CHECKS.md · LESSONS.md · WEEKLY_REVIEW.md · reports)이 9일 넘게 안 바뀌었을 때 → 주간 점검이 빠졌을 수 있음 |
| 검증 제외 반복 | `docs/crosscheck.json` 에서 같은 종목이 3일 넘게 계속 빠질 때 |

- 같은 제안은 처음 한 번, 해결 안 되면 7일마다 다시 알려줘요. 해결되면 '해결된 제안'으로 알려줘요.
- 기록장이나 crosscheck.json 이 없는 봇은 그 확인을 건너뛰어요.
- 봇마다 다른 파일을 보게 하려면 `bots.json` 에 `"learning": ["파일1", "파일2"]`, `"crosscheck": "경로"` 를 적으면 돼요.
- **제안 기능이 고장 나도 기존 점검·알림은 그대로 돌아가요.** (요약에 '제안 기능에 오류' 한 줄만 붙어요)

## 처음 설정 (Settings → Secrets and variables → Actions → New repository secret)

| 이름 | 넣을 값 |
|---|---|
| `CHECKUP_TOKEN` | 다른 봇들의 실행 기록을 읽는 GitHub 토큰 (아래 참고) |
| `NTFY_TOPIC` | 알림 받을 ntfy 채널 이름 |
| `TELEGRAM_BOT_TOKEN` | 다른 봇들과 같은 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 다른 봇들과 같은 텔레그램 채팅 ID |

### CHECKUP_TOKEN 만들기
GitHub 오른쪽 위 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token

- Expiration: 원하는 기간 (만료되면 '점검 불가' 알림이 와요)
- Repository access: **All repositories** (봇을 새로 만들어도 따로 설정할 필요 없음)
- Permissions → Repository permissions: **Actions: Read-only**, **Contents: Read-only**
- 읽기 전용이라 이 토큰으로는 아무것도 바꿀 수 없어요

## 봇 추가하기
`bots.json` 에 한 줄 추가하면 끝이에요.

## 바로 확인하기
Actions → **봇 점검·검증** → Run workflow → 요약이 휴대폰으로 와요.
