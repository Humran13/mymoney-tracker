from django.core.management.base import BaseCommand

from apps.transactions.models import Category

INCOME_CATEGORIES = ["Salary", "Business Receipts", "Freelance Payments", "Gifts", "Allowances", "Other Income"]

EXPENSE_CATEGORIES = [
    "Breakfast", "Lunch", "Dinner", "Transport", "Fuel", "Rent", "Internet",
    "Mobile Data", "Airtime", "Electricity", "Water", "School Fees", "Shopping",
    "Medical Expenses", "Family Support", "Entertainment", "Mobile Money Fees",
    "Other Expenses",
]


class Command(BaseCommand):
    help = "Seeds the default (global) income/expense categories. Safe to run repeatedly."

    def handle(self, *args, **options):
        created = 0
        for name in INCOME_CATEGORIES:
            _, was_created = Category.objects.get_or_create(user=None, name=name, kind=Category.Kind.INCOME)
            created += int(was_created)
        for name in EXPENSE_CATEGORIES:
            _, was_created = Category.objects.get_or_create(user=None, name=name, kind=Category.Kind.EXPENSE)
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f"Seeded categories ({created} new)."))
