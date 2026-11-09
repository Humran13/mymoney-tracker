from django import template

from apps.core.currency import format_money

register = template.Library()


@register.filter(name="money")
def money_filter(amount, currency_code="UGX"):
    if amount is None:
        amount = 0
    return format_money(amount, currency_code)
