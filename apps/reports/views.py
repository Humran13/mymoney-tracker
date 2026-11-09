import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from . import services


def _resolve_year_month(request):
    today = timezone.localdate()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))
    return year, month


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
        writer.writerow([key.replace("_", " ").title(), report[key]])
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

    summary_rows = [
        ["Total income", f"{report['total_income']:,.2f}"],
        ["Total expenses", f"{report['total_expenses']:,.2f}"],
        ["Net", f"{report['net']:,.2f}"],
        ["Mobile money fees", f"{report['mobile_money_fees']:,.2f}"],
        ["Opening balance", f"{report['opening_balance']:,.2f}"],
        ["Closing balance", f"{report['closing_balance']:,.2f}"],
        ["Money borrowed", f"{report['money_borrowed']:,.2f}"],
        ["Money lent", f"{report['money_lent']:,.2f}"],
        ["Outstanding debts (closing)", f"{report['closing_outstanding_debts']:,.2f}"],
        ["Outstanding receivables (closing)", f"{report['closing_outstanding_receivables']:,.2f}"],
        ["Savings contributions", f"{report['savings_contributions']:,.2f}"],
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
