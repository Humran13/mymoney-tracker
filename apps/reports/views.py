import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from . import cash_flow, services


def _resolve_year_month(request):
    today = timezone.localdate()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))
    return year, month


def _analysis_period(request):
    start = parse_date(request.GET.get("start") or "")
    end = parse_date(request.GET.get("end") or "")
    on = parse_date(request.GET.get("date") or "")
    return cash_flow.resolve_period(
        request.GET.get("preset", cash_flow.DEFAULT_PRESET), on=on, start=start, end=end
    )


@login_required
def income_vs_expenses(request):
    """Dedicated "Income vs Expenses Analysis" page (Phase 13, improvement 1).

    Defaults to today; every figure comes from the same ledger the dashboard
    balance is built from, so the two can never disagree.
    """
    period = _analysis_period(request)
    context = cash_flow.analysis(request.user, period)
    return render(request, "reports/income_vs_expenses.html", context)


@login_required
def income_vs_expenses_day(request, date):
    """Drill-down for a single day: every source object once, grouped by
    category, account, merchant/description and fees."""
    the_date = parse_date(date)
    if the_date is None:
        return render(request, "reports/income_vs_expenses_day.html", {"detail": None, "date": date}, status=404)
    return render(
        request,
        "reports/income_vs_expenses_day.html",
        {"detail": cash_flow.day_detail(request.user, the_date), "date": the_date},
    )


@login_required
def monthly_report(request):
    year, month = _resolve_year_month(request)
    report = services.compute_monthly_report(request.user, year, month)
    return render(request, "reports/monthly.html", {"report": report})


@login_required
def monthly_report_csv(request):
    year, month = _resolve_year_month(request)
    report = services.compute_monthly_report(request.user, year, month)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="mymoney_report_{year}_{month:02d}.csv"'
    writer = csv.writer(response)
    writer.writerow(["MyMoney Tracker Monthly Report", f"{year}-{month:02d}"])
    writer.writerow([])
    writer.writerow(["Metric", "Amount (UGX)"])
    for key in [
        "total_income", "total_expenses", "net", "mobile_money_fees", "opening_balance", "closing_balance",
        "money_borrowed", "money_lent", "loan_repayments_paid", "loan_repayments_received",
        "opening_outstanding_debts", "closing_outstanding_debts",
        "opening_outstanding_receivables", "closing_outstanding_receivables", "savings_contributions",
    ]:
        value = report[key]
        # A balance before an account's starting date is genuinely unknown
        # (None), not zero — never let it print as "None" in an export.
        writer.writerow([key.replace("_", " ").title(), "" if value is None else value])
    writer.writerow([])
    writer.writerow(["Spending by category", "Amount (UGX)"])
    for row in report["spending_by_category"]:
        writer.writerow([row["category__name"] or "Uncategorized", row["total"]])
    return response


@login_required
def monthly_report_pdf(request):
    year, month = _resolve_year_month(request)
    report = services.compute_monthly_report(request.user, year, month)

    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("MyMoney Tracker — Monthly Report", styles["Title"]),
        Paragraph(f"{report['start']:%B %Y}", styles["Normal"]),
        Spacer(1, 16),
    ]

    def money(value):
        # None means "before this account's starting date, so unknown" — say so
        # instead of crashing or pretending it was 0.
        return "not available" if value is None else f"{value:,.2f}"

    summary_rows = [
        ["Total income", money(report["total_income"])],
        ["Total expenses", money(report["total_expenses"])],
        ["Net", money(report["net"])],
        ["Mobile money fees", money(report["mobile_money_fees"])],
        ["Opening balance", money(report["opening_balance"])],
        ["Closing balance", money(report["closing_balance"])],
        ["Money borrowed", money(report["money_borrowed"])],
        ["Money lent", money(report["money_lent"])],
        ["Outstanding debts (closing)", money(report["closing_outstanding_debts"])],
        ["Outstanding receivables (closing)", money(report["closing_outstanding_receivables"])],
        ["Savings contributions", money(report["savings_contributions"])],
    ]
    table = Table([["Metric", "Amount (UGX)"]] + summary_rows, colWidths=[260, 160])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("Spending by category", styles["Heading3"]))
    cat_rows = [[row["category__name"] or "Uncategorized", f"{row['total']:,.2f}"] for row in report["spending_by_category"]]
    if cat_rows:
        cat_table = Table([["Category", "Amount (UGX)"]] + cat_rows, colWidths=[260, 160])
        cat_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
        elements.append(cat_table)

    doc.build(elements)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="mymoney_report_{year}_{month:02d}.pdf"'
    return response
