from decimal import Decimal

from django.db import migrations


CHARITY = "Charity"
SHIPPING_FEES = "Shipping Fees"


def add_default_categories(apps, schema_editor):
    """Idempotent: adds two *global* expense categories (user=None).

    Global categories are shared by every account, so nothing has to be
    written per user and no user's own custom categories are touched. In
    Postgres a UNIQUE constraint doesn't consider two NULL owners equal, so
    `get_or_create` (not a bare `create`) is what actually makes re-running
    this a no-op.
    """
    Category = apps.get_model("transactions", "Category")
    for name in (CHARITY, SHIPPING_FEES):
        Category.objects.get_or_create(user=None, name=name, kind="expense")


def noop_reverse(apps, schema_editor):
    """Deliberately does nothing on reverse.

    Dropping the categories on a rollback would orphan every expense already
    filed under them (Category is SET_NULL, so the label would silently
    become "Uncategorized"). Leaving two unused global categories behind is
    harmless; destroying a user's categorization is not.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("transactions", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(add_default_categories, noop_reverse),
    ]
