# 손절선 알림 설계 검토 (초안)

> 상태: **설계 검토용 초안** — 코드 변경 없음. 착수 여부·방식을 합의하기 위한 문서.
> 근거: 2026-07-12 코드 실측(발송 경로·계약·스케줄러·기존 알림 패턴).

## 1. 문제 정의

보유종목의 현재가가 **손절선에 도달(hit)/근접(near)**했을 때 사용자에게 알린다.

**이미 된 것 (표시)**: `/portfolio`·종목 헤더 배지에 `stop_status`(hit/near) 표시 완료(#235·#237). 즉 **사용자가 화면을 보면** 안다.

**안 된 것 (능동 알림)**: 사용자가 화면을 안 볼 때 **밀어서 전달**(push). 이것이 이 문서의 대상.

## 2. 실측 제약 (설계를 가르는 사실)

| 사실 | 함의 |
|---|---|
| `app`/worker config 에 **telegram bot_token·chat_id 없음**, worker 컨테이너 env 에 **TELEGRAM 미전달** | 지금 코드로는 worker 가 텔레그램을 **못 보낸다**(자격증명 부재) |
| import-linter 계약 `app-no-cli-only` 이 **app→reporter.telegram 금지** | app 이 `TelegramSender` 를 직접 부르면 계약 위반 |
| `TelegramSender(bot_token, chat_id)` 는 단순 봇 API 래퍼(공유 커널 아님, CLI 전용 분류) | 어댑터로 감싸면 재사용 가능하나 계약·자격증명 선결 |
| `FallbackSink = Callable` 레지스트리 선례: app 이 포트(Callable) 정의 → startup 에서 sink 등록 | **알림 포트의 검증된 패턴이 이미 존재** |
| 단일 사용자(계정 없음), 발송 대상 chat_id 는 CLI 가 쓰는 그 채팅 1개 | "누구에게" 는 명확 — 다중 사용자 라우팅 불요 |
| 스케줄러: candle_batch(평일 19:30, 종가 갱신) · nightly(18:00 유니버스 스냅샷=close_price) · ingest_cycle(장중 30분) | 손절 체크를 얹을 자연스러운 훅 존재 |
| KR 종가 소스 = UniverseSnapshot.close_price (일 단위). 실시간 체결가는 KIS WS(조회 중 종목만) | **일 단위 알림**은 배치로, **장중 실시간 알림**은 별도 난이도 |

## 3. 설계 축 두 가지

### 축 A — 전달 채널 (어디로 알리나)
1. **인앱만 (표시 강화)**: 이미 배지 있음. + 홈/네비에 "손절 경보 N건" 뱃지, 재방문 시 노출. **발송 경로 불요 → 계약·자격증명 문제 회피.**
2. **텔레그램 push**: 화면 밖에도 전달. 계약·자격증명 선결 필요.
3. **웹 푸시(Web Push/브라우저 알림)**: 화면 밖 전달 + 텔레그램 불요. 단 Service Worker·VAPID·구독 관리 신규 인프라(중~대).

### 축 B — 감지 타이밍 (언제 체크하나)
1. **일 1회 배치**(마감 후, candle_batch 19:30 직후): close_price 로 hit/near 판정. 단순·저비용. "장중 실시간"은 아님.
2. **장중 주기**(ingest_cycle 30분): 준실시간이나 30분 지연.
3. **실시간**(KIS WS): 즉시성 최고이나 조회 중 종목만 구독·상시연결 부담. 보유 전 종목 상시 구독은 40구독 한도·비용 문제.

## 4. 텔레그램 push 를 택할 경우의 구조 (계약 준수안)

FallbackSink 패턴을 그대로 따른다 — **app 이 알림 포트를 정의, 어댑터가 telegram 구현**:

```
app/ports/notifier.py       NotifierPort (Protocol): notify(title, body) -> None
app/adapters/notify/telegram.py  TelegramNotifier — reporter.telegram.TelegramSender 위임
                             (이 파일만 reporter.telegram 참조 → llm-behind-port 처럼
                              app-no-cli-only 계약에서 adapters.notify 예외 허용)
app/services/stop_alert.py   보유 stop_status 계산 → hit/near 를 NotifierPort 로 발송
                             (중복 발송 방지: 이미 알린 것 상태 저장, hit 재진입만 재알림)
scheduler                    candle_batch 뒤 run_stop_alert 잡 추가
config/worker env            TELEGRAM_BOT_TOKEN·CHAT_ID 를 worker 에 전달(현재 없음)
```

**계약 처리**: 현행 `app-no-cli-only` 는 app 전체가 reporter.telegram 금지. LLM 때와 동일하게 **adapters/notify 한 곳만 예외**로 두는 계약 재구성 필요(P2 `llm-behind-port` 선례). 위반 주입으로 실효 검증.

**중복 발송 방지(필수)**: 매 배치마다 hit 을 다시 쏘면 스팸. `stop_alert_state`(종목·마지막알림상태·시각) 저장 → 상태 전이(ok→near, near→hit)에서만 1회 발송, 회복 시 리셋.

## 5. 비용·리스크

| 방식 | 인프라 | 계약/자격증명 | 즉시성 | 리스크 |
|---|---|---|---|---|
| A1 인앱 표시강화 | 최소(뱃지) | 불요 | 재방문 시 | 낮음. "능동 알림"은 아님 |
| A2 텔레그램+B1 배치 | 포트+어댑터+상태+계약+worker env | 필요(계약 재구성·토큰 전달) | 일 1회(마감후) | 중. 단일 사용자라 라우팅 단순 |
| A2 텔레그램+B2 장중 | 위 + 30분 주기 | 필요 | 30분 | 중. 준실시간 |
| A3 웹푸시 | SW·VAPID·구독 신규 | 불요 | 화면 밖 | 중~대. 신규 스택 |
| B3 실시간(KIS WS) | 상시 구독 확장 | — | 즉시 | 대. 40구독 한도·상시연결 |

## 6. 권고

- **가장 실용적: A2(텔레그램) + B1(마감 후 배치)**. 이유 — (1) 발송 인프라(bot·chat_id·TelegramSender)가 이미 있고 단일 사용자라 chat_id 명확, (2) FallbackSink 검증된 포트 패턴 재사용, (3) 손절은 통상 **종가 기준** 판단이 자연스러워 일 1회로 충분, (4) 장중 실시간(B3)은 한도·비용 대비 실익 낮음.
- **선결 3가지**: ① `adapters/notify` 예외를 담은 계약 재구성 ② worker 에 TELEGRAM 토큰·chat_id 전달(현재 미전달) ③ 중복 발송 방지 상태 저장.
- **저비용 대안**: 능동 push 가 지금 급하지 않다면 **A1(인앱 표시 강화)** — 홈/네비 "손절 경보 N" 뱃지만으로 큰 인프라 없이 인지도 개선. push 는 트리거(실제 손절 놓침 경험) 생기면 착수.

## 7. 미결 질문 (합의 필요)
- 능동 push 가 필요한가, 아니면 인앱 표시 강화(A1)로 충분한가?
- push 라면 채널은 텔레그램(A2, 기존 인프라 재사용) vs 웹푸시(A3, telegram 불요)?
- 타이밍은 마감 후 일 1회(B1)면 충분한가, 장중 30분(B2)이 필요한가?
- worker 에 TELEGRAM 자격증명을 넣는 것(운영·보안)에 동의하는가?
