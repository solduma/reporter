"""어댑터 — 포트의 구체 구현(영속화·외부 IO).

driven adapters(응용 계층이 포트를 통해 호출). persistence 는 SQLAlchemy 로 Repository 포트를
구현한다. 향후 외부 IO(kis·dart·naver·ollama) 어댑터도 여기 아래로 모은다.
"""
