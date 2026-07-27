import sys
from pathlib import Path

# 설치 없이 패키지를 import 가능하게 python/ 를 sys.path 에 추가.
sys.path.insert(0, str(Path(__file__).resolve().parent))
