from decimal import Decimal, InvalidOperation


NUMERIC_KEYS = frozenset(
    (
        "YES価格",
        "NO価格",
        "出来高",
        "流動性",
        "締切までの日数",
    )
)


def canonical_json_number(raw_value: str, *, field: str, row_number: int) -> str:
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{row_number}行目の{field}が不正です") from None

    if not value.is_finite():
        raise ValueError(f"{row_number}行目の{field}が不正です")

    if value.is_zero():
        return "0"

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
