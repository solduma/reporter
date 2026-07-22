"""테스트가 패키지를 import 가능하게 경로 보정(uv install 없이도 python -m pytest 동작)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
