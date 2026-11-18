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
