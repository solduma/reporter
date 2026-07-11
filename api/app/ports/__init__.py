"""포트 — 응용 계층이 의존하는 인터페이스(Protocol).

육각형 아키텍처의 경계. 여기엔 "무엇을 하는가"(시그니처)만 있고 "어떻게"(SQLAlchemy·외부 API)는
adapters 가 구현한다. app 내부의 db·services·routers 를 import 하지 않는다(리프).
db.models 의 ORM 타입은 현 단계에선 반환 타입 힌트로만 쓰되(TYPE_CHECKING), 향후 도메인
엔티티로 대체할 여지를 둔다.
"""
