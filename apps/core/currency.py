"""
Currency registry.

v1 only supports UGX, but financial calculations must look up formatting
and precision here rather than hardcoding Uganda-specific assumptions, so
additional currencies can be added later without touching the engine.
"""
from decimal import ROUND_HALF_UP, Decimal

CURRENCIES = {
    "UGX": {"name": "Ugandan Shilling", "symbol": "UGX", "decimals": 0},
    "USD": {"name": "US Dollar", "symbol": "$", "decimals": 2},
    "KES": {"name": "Kenyan Shilling", "symbol": "KES", "decimals": 2},
}

DEFAULT_CURRENCY_CODE = "UGX"


def get_currency(code: str) -> dict:
    return CURRENCIES.get(code, {"name": code, "symbol": code, "decimals": 2})


def quantize_amount(amount: Decimal, currency_code: str = DEFAULT_CURRENCY_CODE) -> Decimal:
    """Round a Decimal amount to the currency's minor-unit precision using
    exact decimal (never float) arithmetic."""
    decimals = get_currency(currency_code)["decimals"]
    exponent = Decimal(1).scaleb(-decimals)
    return Decimal(amount).quantize(exponent, rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, currency_code: str = DEFAULT_CURRENCY_CODE) -> str:
    info = get_currency(currency_code)
    quantized = quantize_amount(amount, currency_code)
    decimals = info["decimals"]
    if decimals == 0:
        formatted = f"{int(quantized):,}"
    else:
        formatted = f"{quantized:,.{decimals}f}"
    return f"{info['symbol']} {formatted}"
