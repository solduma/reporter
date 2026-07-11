"""도메인 코어 — 순수 비즈니스 규칙(스코어링·기술지표 등).

육각형 아키텍처의 안쪽. app 내부의 어떤 계층(services/routers/db/config/schemas)도
import 하지 않는다(외부 라이브러리·표준 라이브러리만). 입력은 원시 타입·도메인 dataclass 로
받고, 영속화·외부 IO·프레임워크를 모른다. import-linter 계약으로 이 리프 규칙을 강제한다.
"""
