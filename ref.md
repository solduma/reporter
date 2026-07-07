---
title: "AI가 매일 아침 증권사 리포트를 읽어주는 시스템 만들기 (OpenClaw)"
source: "https://zoey.day/blog?post=4w67rj24g4gd9m5yq8ep"
author:
  - "[[조이]]"
published: 2026-07-03
created: 2026-07-07
description: "투자 에이전트 '돈냥이'가 매일 아침 증권사 리포트를 자동 수집하고, AI가 분석한 인사이트를 슬랙과 텔레그램으로 보내주는 시스템을 만든 과정을 공유합니다. 세 번째 팀원 — 돈냥이 💰🐱 1편에서 OpenClaw를 설치하고 첫 번째 AI 비서 '달밤이'를 만들었어요. 그 다음 콘텐츠 매니저 '슝이'까지, 둘이 자리를 잡아가고 있었죠. 근데 매일 하는 일 중 하나가 아직 자동화가 안 되어 있었습니다. 투자 리서치. 매일 아침 증권사 리포트 훑어보고, 뉴스 체크하고, 포트폴리오 점검하고. 이걸 매일 하면 30분~1시간이 녹아요. 뉴스 하나 읽다 보면 링크 타고 들어가서 한 시간이 지나있죠. 그래서 세 번째 에이전트를 만들기로 했습니다. OpenClaw에 새 에이전트 추가하기 OpenClaw는 에이전트를 여러 개 돌릴 수 있어요. 어제 만든 비서실장 '달밤이'에게 새 직원을 뽑고 싶다고 이야기합니다. 대답을 몇번 하고 무슨 업무를 맡기고 싶은지 정해주면, 달밤이가 새 직원을 만들기 시작합니다. 그렇게 탄생한, 돈냥이💰🐱 역할: 투자 자문위원 — 쉽게 설명해주는 투자 친구 원칙: 좋은 것만 말하지 않고 리스크도 솔직하게. 모르면 절대 아는 척 금지. 채널: 슬랙 #investment-strategy 채널 전담 달밤이(비서실장)가 돈냥이에게 업무를 지시하고, 돈냥이는 자기 채널에서 투자 관련 일을 처리하는 구조예요. 제가 직접 일을 시키기도 하고요. 에이전트끼리는 sessions_send로 소통합니다."
tags:
  - "clippings"
---

AI가 매일 아침 증권사 리포트를 읽어주는 시스템 만들기 (OpenClaw)

투자 에이전트 '돈냥이'가 매일 아침 증권사 리포트를 자동 수집하고, AI가 분석한 인사이트를 슬랙과 텔레그램으로 보내주는 시스템을 만든 과정을 공유합니다. 세 번째 팀원 — 돈냥이 💰🐱 1편에서 OpenClaw를 설치하고 첫 번째 AI 비서 '달밤이'를 만들었어요. 그 다음 콘텐츠 매니저 '슝이'까지, 둘이 자리를 잡아가고 있었죠. 근데 매일 하는 일 중 하나가 아직 자동화가 안 되어 있었습니다. 투자 리서치. 매일 아침 증권사 리포트 훑어보고, 뉴스 체크하고, 포트폴리오 점검하고. 이걸 매일 하면 30분~1시간이 녹아요. 뉴스 하나 읽다 보면 링크 타고 들어가서 한 시간이 지나있죠. 그래서 세 번째 에이전트를 만들기로 했습니다. OpenClaw에 새 에이전트 추가하기 OpenClaw는 에이전트를 여러 개 돌릴 수 있어요. 어제 만든 비서실장 '달밤이'에게 새 직원을 뽑고 싶다고 이야기합니다. 대답을 몇번 하고 무슨 업무를 맡기고 싶은지 정해주면, 달밤이가 새 직원을 만들기 시작합니다. 그렇게 탄생한, 돈냥이💰🐱 역할: 투자 자문위원 — 쉽게 설명해주는 투자 친구 원칙: 좋은 것만 말하지 않고 리스크도 솔직하게. 모르면 절대 아는 척 금지. 채널: 슬랙 #investment-strategy 채널 전담 달밤이(비서실장)가 돈냥이에게 업무를 지시하고, 돈냥이는 자기 채널에서 투자 관련 일을 처리하는 구조예요. 제가 직접 일을 시키기도 하고요. 에이전트끼리는 sessions\_send로 소통합니다.

2

![](https://static.cafenono.com/emoji/14/img-apple-64/1f44d.webp)

6

zoeylog

조이의 연습장 (Blog)

![](https://upload.cafenono.com/image/stockImage/VK7jXtOtEuM?q=90&s=1280x1&t=outside&f=webp)

![](https://upload.cafenono.com/image/slashpagePost/20260404/140626_xhsxMLOjtGK0lMRGKD?q=100&s=1440x1&t=outside&f=webp)

투자 에이전트 '돈냥이'가 매일 아침 증권사 리포트를 자동 수집하고, AI가 분석한 인사이트를 슬랙과 텔레그램으로 보내주는 시스템을 만든 과정을 공유합니다.

## 세 번째 팀원 — 돈냥이 💰🐱

[1편](https://slashpage.com/zoeylog/36nj8v2wq74k325ykq9z) 에서 OpenClaw를 설치하고 첫 번째 AI 비서 '달밤이'를 만들었어요. 그 다음 콘텐츠 매니저 '슝이'까지, 둘이 자리를 잡아가고 있었죠.[![](https://upload.cafenono.com/image/slashpagePost/20260403/142644_oLb4JZUoj2h5WnhX1f?f=jpeg&q=90&s=1280x1&t=outside)](https://slashpage.com/zoeylog/36nj8v2wq74k325ykq9z)

[OpenClaw 설치부터 첫 비서 달밤이 탄생까지 - 조이의 연습장 (Blog) - zoeylog

\[삶을 구조화하는 루틴 빌더\] 조이의 경험 축적, 아카이브 공간

slashpage.com

](https://slashpage.com/zoeylog/36nj8v2wq74k325ykq9z)

근데 매일 하는 일 중 하나가 아직 자동화가 안 되어 있었습니다. 투자 리서치.

매일 아침 증권사 리포트 훑어보고, 뉴스 체크하고, 포트폴리오 점검하고. 이걸 매일 하면 30분~1시간이 녹아요. 뉴스 하나 읽다 보면 링크 타고 들어가서 한 시간이 지나있죠.

그래서 세 번째 에이전트를 만들기로 했습니다.

### OpenClaw에 새 에이전트 추가하기

OpenClaw는 에이전트를 여러 개 돌릴 수 있어요. 어제 만든 비서실장 '달밤이'에게 새 직원을 뽑고 싶다고 이야기합니다.

![](https://upload.cafenono.com/image/slashpagePost/20260404/135713_a39f1a4PnxlIeiySPP?q=100&s=1440x1&t=outside&f=webp)

대답을 몇번 하고 무슨 업무를 맡기고 싶은지 정해주면, 달밤이가 새 직원을 만들기 시작합니다.

그렇게 탄생한, 돈냥이💰🐱

•

역할: 투자 자문위원 — 쉽게 설명해주는 투자 친구

•

원칙: 좋은 것만 말하지 않고 리스크도 솔직하게. 모르면 절대 아는 척 금지.

•

채널: 슬랙 #investment-strategy 채널 전담

달밤이(비서실장)가 돈냥이에게 업무를 지시하고, 돈냥이는 자기 채널에서 투자 관련 일을 처리하는 구조예요. 제가 직접 일을 시키기도 하고요. 에이전트끼리는 sessions\_send로 소통합니다.

### 돈냥이에게 시킨 첫 번째 일

"매일 아침 증권사 리포트를 자동으로 수집해서, AI가 분석한 브리핑을 슬랙이랑 텔레그램으로 보내줘."

단순 요약이 아니라, 여러 리포트를 교차 분석해서 "오늘 시장에서 뭐가 중요한지"를 한눈에 파악할 수 있는 브리핑을 원했어요. 돈냥이가 파이썬 스크립트를 짜고, 크론으로 자동 실행하고, 슬랙·텔레그램 발송까지 — 그 시스템을 만든 과정을 공유합니다.

## 전체 구조 — 어떻게 돌아가나

![](https://upload.cafenono.com/image/slashpagePost/20260404/135955_N7kaXxwdOivFEabGQH?q=90&s=1280x1&t=outside&f=webp)

### 흐름 요약

1.

매일 아침 9:30~11:00, macOS 크론이 파이썬 스크립트를 자동 실행

2.

네이버 금융 리서치 페이지에서 당일 발행된 증권사 리포트 목록을 수집

3.

조회수 + 주요 증권사 기준으로 카테고리별 상위 5개를 선별

4.

선별된 리포트의 PDF를 다운로드하고 텍스트를 추출

5.

1단계 — Haiku(경량 AI): 리포트별 150자 요약 (핵심 수치·종목 포함)

6.

2단계 — Sonnet(고급 AI): 전체 요약을 종합 분석해서 인사이트 생성

7.

슬랙 채널과 텔레그램으로 발송

## 1단계: 리포트 수집 — 네이버 금융 크롤링

데이터 소스는 네이버 금융 리서치 페이지입니다.

```
https://finance.naver.com/research/company_list.naver   ← 종목분석
https://finance.naver.com/research/industry_list.naver  ← 산업분석
https://finance.naver.com/research/market_info_list.naver ← 시황정보
https://finance.naver.com/research/invest_list.naver    ← 투자정보
https://finance.naver.com/research/economy_list.naver   ← 경제분석
https://finance.naver.com/research/debenture_list.naver ← 채권분석
```

6개 카테고리를 돌면서 오늘 날짜 리포트만 필터링합니다. 페이지네이션도 처리해서, 당일 리포트가 여러 페이지에 걸쳐 있어도 전부 수집해요.

### 핵심 리포트 선별 로직

하루에 리포트가 수십~백 개가 올라옵니다. 전부 분석하면 시간도, API 비용도 낭비. 그래서 상위 10개만 골라요.

선별 기준:

•

조회수 정규화 — 해당 카테고리 내 최대 조회수 대비 비율 (0~100점)

•

주요 증권사 보너스 — 삼성·미래에셋·KB·NH·한국투자 등 15개 대형사면 +30점

```
MAJOR_BROKERS = {
    "삼성증권", "미래에셋증권", "KB증권", "NH투자증권", "한국투자증권",
    "신한투자증권", "키움증권", "하나증권", "대신증권", "메리츠증권",
    "IBK투자증권", "교보증권", "유안타증권", "현대차증권", "LS증권",
}

# 점수 = (조회수/최대조회수 × 100) + (주요증권사면 30)
```

이렇게 하면 "많이 본 리포트 + 신뢰도 높은 증권사" 조합이 상위에 올라옵니다.

## 2단계: PDF → 텍스트 추출

선별된 리포트의 PDF를 다운로드해서 텍스트를 뽑습니다.

```
import fitz  # PyMuPDF 라이브러리

doc = fitz.open(pdf_path)
for i, page in enumerate(doc):
    if i >= 3:  # 앞 3페이지만 (핵심은 앞에 있음)
        break
    text += page.get_text()
```

PDF 전체를 읽으면 차트 설명, 면책조항 같은 잡음이 많아요. 앞 3페이지만 추출하면 핵심 내용을 효율적으로 잡을 수 있습니다.

## 3단계: AI 2단계 분석

여기가 핵심입니다. AI 분석을 두 단계로 나눈 이유가 있어요.

![](https://upload.cafenono.com/image/slashpagePost/20260404/140043_hqyl768zqYKu6ndGKd?q=90&s=1280x1&t=outside&f=webp)

### 1차 — Haiku로 개별 요약

Haiku(Claude의 경량 모델)가 리포트 하나하나를 150자 이내로 요약합니다.

```
요약 조건:
- 핵심 주장 1줄
- 구체적 수치나 종목이 있으면 반드시 포함
- 150자 이내
```

빠르고 저렴한 모델로 "정보 압축"을 먼저 해요. 6개 카테고리 × 5개 = 최대 30개 리포트를 한꺼번에 고급 모델한테 던지면 비용도 크고 핵심이 묻히거든요. 그리고 이 작업을 2번 진행합니다.

### 2차 — Sonnet으로 종합 인사이트

Sonnet(고급 모델)이 모든 요약을 모아서 종합 분석합니다. 단순 나열이 아니라 재해석.

출력 형식:

```
🔥 오늘의 핵심 (3줄)
→ 여러 리포트에서 겹치거나 임팩트 큰 시장 메시지

📊 주목 테마
→ 반복 언급된 섹터/테마 2~3개, 주목 이유 포함

💎 주목 종목 (최대 5개)
→ 여러 리포트 언급 + 모멘텀 + 수급 흐름 기준 선별

⚠️ 리스크 요인
→ 리포트들이 경고하는 리스크
```

이렇게 하면 "리포트 30개 읽은 효과"를 1분 안에 얻을 수 있어요.

## 4단계: 슬랙 + 텔레그램 발송

### 슬랙

slack-sdk 파이썬 라이브러리로 발송합니다.

```
from slack_sdk import WebClient
client = WebClient(token=SLACK_BOT_TOKEN)
client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=message)
```

메시지가 4000자를 넘으면 자동으로 분할 발송해요. 슬랙 API 제한이 있거든요.

### 텔레그램

텔레그램 Bot API로 발송합니다.

```
url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
requests.post(url, json={"chat_id": CHAT_ID, "text": message})
```

텔레그램 봇 만드는 법:

1.

텔레그램에서 @BotFather 검색 → /newbot 명령

2.

봇 이름과 username 설정 → API 토큰 발급

3.

봇과 대화 시작 → https://api.telegram.org/bot{TOKEN}/getUpdates로 chat\_id 확인

4.

config.py에 토큰과 chat\_id 저장

이렇게 하면 폰으로도 브리핑을 받아볼 수 있어요.

## 크론 스케줄 — 자동 실행 설정

![](https://upload.cafenono.com/image/slashpagePost/20260404/140129_KvQEd8B3DGqiZGR8NS?q=90&s=1280x1&t=outside&f=webp)

macOS crontab으로 매일 자동 실행합니다. 한 번에 6개 카테고리를 다 돌리지 않고, 시간차를 두고 카테고리별로 실행해요.

```
# 매일 오전 9시 — 전날 로그 초기화
0 9 * * 1-5 > ~/Projects/dongnyangi-research/logs/today_briefing.txt

# 9:30 — 종목분석 + 산업분석 (batch 1)
30 9 * * 1-5 cd ~/Projects/dongnyangi-research && python3 test_quality.py --batch 1

# 10:00 — 시황정보 + 투자정보 (batch 2)
0 10 * * 1-5 cd ~/Projects/dongnyangi-research && python3 test_quality.py --batch 2

# 10:30 — 경제분석 (batch 3)
30 10 * * 1-5 cd ~/Projects/dongnyangi-research && python3 test_quality.py --batch 3

# 11:00 — 채권분석 (batch 4)
0 11 * * 1-5 cd ~/Projects/dongnyangi-research && python3 test_quality.py --batch 4

# 14:00 — 오후 능동 리서치
0 14 * * 1-5 cd ~/Projects/dongnyangi-research && python3 afternoon_insight.py
```

왜 나눴나?

•

증권사 리포트가 9시~11시 사이에 순차적으로 올라옴

•

한 번에 돌리면 아직 안 올라온 카테고리는 0개 수집

•

시간차를 두면 각 카테고리별 최신 리포트를 최대한 잡을 수 있음

•

1-5 = 월~금만 실행 (주말엔 증권사도 쉬니까)

## 👋 오후 능동 리서치 — 하루가 끝나지 않았다

오전 브리핑만으로는 부족합니다. 시장은 하루 종일 움직이니까요.

오후 2시에 자동 실행되는 두 번째 시스템:

1.

오전에 발송했던 브리핑 로그를 읽음

2.

AI가 핵심 키워드 5개를 자동 추출

3.

각 키워드로 Google News RSS + 네이버 뉴스 실시간 검색

4.

오전 내용과 연결해서 "오후에 뭐가 달라졌는지" 분석

5.

키워드별로 짧은 메시지(300자 이내)로 끊어서 발송

6.

```
📌 삼성전기 업데이트
오전에 목표주가 상향 말씀드렸는데, 오후에 보니 외국인이 
3거래일 연속 순매수 중이에요. MLCC 재고 사이클이...

출처: Reuters  네이버경제
```

핵심 규칙: 1차 정보(당연한 사실)는 쓰지 않습니다. "유가 올랐다" 대신 "유가 급등 → 국내 물류비 → 특정 종목 마진 영향"처럼 2차·3차 구조적 연결만 전달해요.

## 설치와 실행 — 직접 만들어보려면

### 필요한 것

```
Python 3.x
pip install requests beautifulsoup4 pymupdf anthropic python-dotenv slack-sdk
```

### 환경변수 (config.py 또는.env)

```
ANTHROPIC_API_KEY=sk-ant-...    # Anthropic API 키
SLACK_BOT_TOKEN=xoxb-...       # Slack Bot 토큰
SLACK_CHANNEL_ID=C0AQ...       # 발송할 Slack 채널 ID
TELEGRAM_BOT_TOKEN=123456:ABC  # 텔레그램 봇 토큰 (선택)
TELEGRAM_CHAT_ID=7920761494    # 텔레그램 채팅 ID (선택)
```

### 슬랙 봇 설정

1.

[api.slack.com/apps](https://api.slack.com/apps) 에서 새 앱 생성

[Slack API: Applications | Slack](https://api.slack.com/apps)

[api.slack.com](https://api.slack.com/apps)

1.

OAuth & Permissions → chat:write 스코프 추가

2.

워크스페이스에 설치 → Bot Token 복사

3.

브리핑 받을 채널에 봇 초대 (/invite @봇이름)

### 수동 테스트

```
cd ~/Projects/dongnyangi-research
python3 research_briefing.py
```

정상 작동하면 크론에 등록.

![](https://upload.cafenono.com/image/slashpagePost/20260404/140444_HeJOaXnQqO4wXsKc9k?q=90&s=1280x1&t=outside&f=webp)

![](https://upload.cafenono.com/image/slashpagePost/20260404/140444_3JT8Ov5I6GkahPPkSc?q=90&s=1280x1&t=outside&f=webp)

### 그렇게 만들었습니다. 돈냥이의 데일리 리포트 ✨[![](https://upload.cafenono.com/image/linkPreviewImage/20260404/140313_kDtO8Twm14MXoZ4bBJ?q=80&s=360x1&t=outside&f=webp)](https://t.me/moneycat_report)

[돈냥이의 데일리 리포트

매일 올라오는 증권 리포트 알려준다냥💰🐱

t.me

](https://t.me/moneycat_report)

베타테스터 들어오세요!

## 만들면서 부딪힌 것들

크론 시간 설정 — 처음에 8시로 돌렸더니 리포트가 0개. 증권사 리포트는 9시 이후에 올라와요. 직접 돌려봐야 알 수 있는 것들.

macOS crontab 권한 — AI 에이전트가 crontab -e를 실행하면 macOS 보안 UI가 뜨는데, AI는 그 화면을 볼 수도 클릭할 수도 없어요. crontab 수정은 사람이 직접 해야 합니다.

환경변수 충돌 —.env 파일에 만료된 API 키가 있었는데, load\_dotenv(override=True) 때문에 정상 인증을 덮어써 버림. override=False로 바꾸고 해결.

주말 크론 — 토요일 아침 "왜 리포트 안 왔어?" → 증권사도 주말엔 쉽니다. 크론을 \* \* 1-5(월~금)로 수정.
