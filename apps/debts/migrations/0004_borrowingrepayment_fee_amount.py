from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive only: existing BorrowingRepayment rows get fee_amount = 0,
    which is exactly the pre-Phase-13 behavior (principal-only repayment).
    No existing amount, date, account or posting is modified."""

    dependencies = [
        ("debts", "0003_borrowing_debt_type_alter_borrowing_lender_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="borrowingrepayment",
            name="fee_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Transaction charge charged by the payment channel (MTN/Airtel/bank). Reduces the account balance, never the debt.",
                max_digits=18,
            ),
        ),
    ]
