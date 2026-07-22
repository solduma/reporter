"""안전한 산술식 평가기(eval 대체).

ast 로 파싱해 허용 노드(Expression/BinOp/UnaryOp/Constant/Name/abs 호출)만 평가한다.
Name 은 주어진 namespace 에서 조회한다. 임의 코드 실행 불가(eval 금지, 속성 접근/호출 제한).
"""

from __future__ import annotations

import ast
from decimal import Decimal

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}
_ALLOWED_FUNCS = {"abs": abs, "round": round}


class SafeEvalError(ValueError):
    """지원하지 않는 노드가 식에 포함된 경우."""


def safe_eval(
    expr: str, namespace: dict[str, Decimal | int | float]
) -> tuple[Decimal | None, list[str]]:
    """expr 을 평가해 (값, missing_keys) 반환.

    - namespace 에 없는 Name 은 missing 에 기록하고 평가를 중단한다(값 None).
    - 0으로 나누기 시 (None, []) — 호출자가 warning 으로 처리.
    - 지원 불가 노드(속성·임의 호출·부울 연산 등)면 SafeEvalError.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - 정제된 식만 들어옴
        raise SafeEvalError(f"구문 오류: {exc.msg}") from exc

    missing: list[str] = []

    def _name(node: ast.Name):
        if node.id in namespace:
            return Decimal(str(namespace[node.id]))
        missing.append(node.id)
        raise _Missing()

    def _const(node: ast.Constant):
        return Decimal(str(node.value))

    def _call(node: ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise SafeEvalError(f"지원 않는 호출: {ast.dump(node.func)}")
        if node.keywords:
            raise SafeEvalError("키워드 인수 미지원")
        args = [_eval(a) for a in node.args]
        if any(a is None for a in args):
            return None
        fn = _ALLOWED_FUNCS[node.func.id]
        return Decimal(str(fn(*[Decimal(a) for a in args])))

    def _binop(node: ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise SafeEvalError(f"지원 않는 연산자: {type(node.op).__name__}")
        left, right = _eval(node.left), _eval(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Div) and right == 0:
            raise _DivZero()
        return op(left, right)

    def _unaryop(node: ast.UnaryOp):
        op = _ALLOWED_UNARYOPS.get(type(node.op))
        if op is None:
            raise SafeEvalError(f"지원 않는 단항 연산자: {type(node.op).__name__}")
        operand = _eval(node.operand)
        if operand is None:
            return None
        return op(operand)

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Name):
            return _name(node)
        if isinstance(node, ast.Constant):
            return _const(node)
        if isinstance(node, ast.BinOp):
            return _binop(node)
        if isinstance(node, ast.UnaryOp):
            return _unaryop(node)
        if isinstance(node, ast.Call):
            return _call(node)
        raise SafeEvalError(f"지원 않는 노드: {type(node).__name__}")

    class _Missing(Exception):
        pass

    class _DivZero(Exception):
        pass

    try:
        value = _eval(tree)
    except _Missing:
        return None, missing
    except _DivZero:
        return None, []
    return value, missing
