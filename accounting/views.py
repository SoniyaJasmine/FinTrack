
from datetime import date
from decimal import Decimal
from html import escape
from io import BytesIO

from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.forms import modelformset_factory
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Category, Transaction, Gst, Ledger
from .forms import (
    CategoryForm,
    GSTReportFilterForm,
    TransactionCategoryForm,
    TransactionForm,
)


GST_RATE = Decimal('18.00')
TransactionCategoryFormSet = modelformset_factory(
    Transaction,
    form=TransactionCategoryForm,
    extra=0
)


def add_transaction(request):

    if request.method == 'POST':

        form = TransactionForm(request.POST)

        if form.is_valid():

            with db_transaction.atomic():

                new_transaction = form.save()

                # Calculate GST
                gst_amount = (
                    new_transaction.amount * GST_RATE / Decimal('100')
                ).quantize(Decimal('0.01'))

                # Save GST
                Gst.objects.create(
                    transaction=new_transaction,
                    gst_percentage=GST_RATE,
                    gst_amount=gst_amount
                )

                # Get previous ledger balance
                last_ledger = Ledger.objects.order_by('-id').first()

                if last_ledger:
                    previous_balance = last_ledger.current_balance
                else:
                    previous_balance = Decimal('0.00')

                # Calculate new balance
                if new_transaction.type == 'Income':
                    new_balance = (
                        previous_balance + new_transaction.amount
                    )
                else:
                    new_balance = (
                        previous_balance - new_transaction.amount
                    )

                # Save ledger
                Ledger.objects.create(
                    date=new_transaction.date,
                    transaction=new_transaction,
                    current_balance=new_balance
                )

            messages.success(
                request,
                "Transaction added successfully!"
            )

            return redirect('transaction_list')

        else:
            messages.error(request, "Please correct the errors below.")

    else:
        form = TransactionForm()

    return render(
        request,
        'accounting/add_transaction.html',
        {'form': form}
    )


def add_category(request):
    if request.method == 'POST':
        form = CategoryForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Category added successfully!")
            return redirect('add_transaction')

        messages.error(request, "Please correct the errors below.")
    else:
        form = CategoryForm()

    return render(
        request,
        'accounting/add_category.html',
        {'form': form}
    )


def categorize_transactions(request):
    queryset = Transaction.objects.order_by('-date', '-id')
    formset = TransactionCategoryFormSet(
        request.POST or None,
        queryset=queryset
    )

    if request.method == 'POST':
        if formset.is_valid():
            formset.save()
            messages.success(request, "Transaction categories updated.")
            return redirect('categorize_transactions')

        messages.error(request, "Please correct the errors below.")

    return render(
        request,
        'accounting/categorize_transactions.html',
        {'formset': formset}
    )


def transaction_list(request):

    transactions = Transaction.objects.all().order_by('-date', '-id')

    total_income = (
        Transaction.objects.filter(type='Income')
        .aggregate(total=Sum('amount'))['total']
        or Decimal('0.00')
    )

    total_expense = (
        Transaction.objects.filter(type='Expense')
        .aggregate(total=Sum('amount'))['total']
        or Decimal('0.00')
    )

    latest_ledger = Ledger.objects.order_by('-id').first()

    current_balance = (
        latest_ledger.current_balance
        if latest_ledger
        else Decimal('0.00')
    )

    context = {
        'transactions': transactions,
        'total_income': total_income,
        'total_expense': total_expense,
        'current_balance': current_balance,
    }

    return render(
        request,
        'accounting/transaction_list.html',
        context
    )


def _get_gst_report_totals(start_date, end_date, category):
    filters = {'gst_records__isnull': False}
    if start_date:
        filters['date__gte'] = start_date
    if end_date:
        filters['date__lte'] = end_date
    if category:
        filters['category'] = category

    transactions = (
        Transaction.objects.filter(**filters)
        .select_related('category')
        .prefetch_related('gst_records')
        .distinct()
        .order_by('category__name', 'date', 'id')
    )
    totals_by_category = {}

    for item in transactions:
        category_name = item.category.name if item.category else 'Uncategorized'
        totals = totals_by_category.setdefault(
            category_name,
            {
                'count': 0,
                'income': Decimal('0.00'),
                'expenses': Decimal('0.00'),
                'gst_collected': Decimal('0.00'),
                'gst_paid': Decimal('0.00'),
            }
        )
        gst_amount = sum(
            (record.gst_amount for record in item.gst_records.all()),
            Decimal('0.00')
        )
        totals['count'] += 1

        if item.type == 'Income':
            totals['income'] += item.amount
            totals['gst_collected'] += gst_amount
        else:
            totals['expenses'] += item.amount
            totals['gst_paid'] += gst_amount

    grand_totals = {
        'count': 0,
        'income': Decimal('0.00'),
        'expenses': Decimal('0.00'),
        'gst_collected': Decimal('0.00'),
        'gst_paid': Decimal('0.00'),
    }
    for totals in totals_by_category.values():
        for key in grand_totals:
            grand_totals[key] += totals[key]

    return totals_by_category, grand_totals


def _shift_month(month, offset):
    month_index = month.year * 12 + month.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def predictive_analytics(request):
    today = timezone.localdate()
    current_month = today.replace(day=1)
    first_month = _shift_month(current_month, -23)
    transactions = (
        Transaction.objects.filter(date__gte=first_month, date__lte=today)
        .select_related('category')
        .order_by('date', 'id')
    )
    monthly_totals = {}
    category_expenses = {}

    month = first_month
    while month <= current_month:
        monthly_totals[month] = {
            'income': Decimal('0.00'),
            'expenses': Decimal('0.00'),
        }
        month = _shift_month(month, 1)

    for item in transactions:
        month = item.date.replace(day=1)
        if item.type == 'Income':
            monthly_totals[month]['income'] += item.amount
        else:
            monthly_totals[month]['expenses'] += item.amount
            category_name = (
                item.category.name if item.category else 'Uncategorized'
            )
            category_expenses[category_name] = (
                category_expenses.get(category_name, Decimal('0.00'))
                + item.amount
            )

    historical_data = [
        {
            'month': month.strftime('%b %Y'),
            'income': totals['income'],
            'expenses': totals['expenses'],
        }
        for month, totals in monthly_totals.items()
    ]
    insights = []
    recommendations = []
    predictions = []
    has_history = transactions.exists()

    monthly_expenses = [
        totals['expenses'] for totals in monthly_totals.values()
    ]
    completed_month_expenses = monthly_expenses[:-1]
    recent_expenses = completed_month_expenses[-3:]
    recent_average = (
        sum(recent_expenses, Decimal('0.00')) / Decimal(len(recent_expenses))
    )

    if has_history:
        previous_expenses = completed_month_expenses[-6:-3]
        previous_average = (
            sum(previous_expenses, Decimal('0.00'))
            / Decimal(len(previous_expenses))
        )
        if previous_average:
            change_percent = (
                (recent_average - previous_average)
                / previous_average * Decimal('100')
            )
            if change_percent > Decimal('10'):
                insights.append(
                    f"Average monthly spending rose by "
                    f"{change_percent:.1f}% over the last three months."
                )
            elif change_percent < Decimal('-10'):
                insights.append(
                    f"Average monthly spending fell by "
                    f"{abs(change_percent):.1f}% over the last three months."
                )
            else:
                insights.append(
                    "Average monthly spending has been broadly stable "
                    "over the last six months."
                )
        else:
            insights.append(
                "Spending in the last three months averaged "
                f"INR {recent_average:.2f} per month."
            )

        recent_income = [
            monthly_totals[month]['income']
            for month in list(monthly_totals)[:-1][-3:]
        ]
        average_income = (
            sum(recent_income, Decimal('0.00'))
            / Decimal(len(recent_income))
        )
        if recent_average > average_income:
            recommendations.append(
                "Recent average spending is higher than income. Review "
                "recurring and discretionary expenses to improve cash flow."
            )
        else:
            recommendations.append(
                "Keep comparing monthly expenses with income and set aside "
                "part of any monthly surplus for savings."
            )

        if category_expenses:
            top_category, top_amount = max(
                category_expenses.items(),
                key=lambda entry: entry[1]
            )
            insights.append(
                f"{top_category} was the largest expense category across "
                f"the period, totaling INR {top_amount:.2f}."
            )
            recommendations.append(
                f"Review spending in {top_category} for opportunities to "
                "set a practical monthly limit."
            )

        for offset in range(1, 4):
            forecast_month = _shift_month(current_month, offset)
            predictions.append({
                'month': forecast_month.strftime('%b %Y'),
                'amount': recent_average,
            })
    else:
        insights.append(
            "There are no transactions in the last 24 months to analyze."
        )
        recommendations.append(
            "Add transaction history to see spending trends and a local "
            "three-month forecast."
        )

    months = list(monthly_totals)
    chart_data = {
        'labels': [
            month.strftime('%b %Y')
            for month in months
        ] + [prediction['month'] for prediction in predictions],
        'historical_expenses': [
            float(monthly_totals[month]['expenses'])
            for month in months
        ] + [None] * len(predictions),
        'predicted_expenses': (
            [None] * (len(months) - 1)
            + [float(monthly_totals[months[-1]]['expenses'])]
            + [float(prediction['amount']) for prediction in predictions]
            if has_history
            else [None] * (len(months) + len(predictions))
        ),
    }

    return render(
        request,
        'accounting/analytics_report.html',
        {
            'historical_data': historical_data,
            'predictions': predictions,
            'insights': insights,
            'recommendations': recommendations,
            'chart_data': chart_data,
            'has_history': has_history,
            'period_start': first_month,
            'period_end': today,
        }
    )


def generate_gst_report(request):
    form = GSTReportFilterForm(request.GET or None)

    if request.GET:
        if not form.is_valid():
            return render(
                request,
                'accounting/gst_report.html',
                {'form': form}
            )

        start_date = form.cleaned_data['start_date']
        end_date = form.cleaned_data['end_date']
        category = form.cleaned_data['category']
        totals_by_category, grand_totals = _get_gst_report_totals(
            start_date,
            end_date,
            category
        )

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=15 * mm,
            leftMargin=15 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
            title='GST Report'
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ReportTitle',
            parent=styles['Title'],
            alignment=TA_CENTER
        )
        story = [
            Paragraph('GST Report', title_style),
            Spacer(1, 5 * mm)
        ]
        date_range = 'All dates'
        if start_date or end_date:
            date_range = (
                f"{start_date.strftime('%d %b %Y') if start_date else 'Beginning'}"
                f" to {end_date.strftime('%d %b %Y') if end_date else 'Present'}"
            )
        story.extend([
            Paragraph(f"Date range: {date_range}", styles['Normal']),
            Paragraph(
                f"Category: {escape(category.name) if category else 'All categories'}",
                styles['Normal']
            ),
            Spacer(1, 5 * mm)
        ])

        table_data = [[
            'Category',
            'Transactions',
            'Taxable Income (INR)',
            'Taxable Expenses (INR)',
            'Total Taxable (INR)',
            'GST Collected (INR)',
            'GST Paid (INR)',
        ]]
        for category_name, totals in totals_by_category.items():
            taxable_total = totals['income'] + totals['expenses']
            table_data.append([
                Paragraph(escape(category_name), styles['BodyText']),
                str(totals['count']),
                f"{totals['income']:.2f}",
                f"{totals['expenses']:.2f}",
                f"{taxable_total:.2f}",
                f"{totals['gst_collected']:.2f}",
                f"{totals['gst_paid']:.2f}",
            ])
        grand_taxable_total = grand_totals['income'] + grand_totals['expenses']
        table_data.append([
            'Total',
            str(grand_totals['count']),
            f"{grand_totals['income']:.2f}",
            f"{grand_totals['expenses']:.2f}",
            f"{grand_taxable_total:.2f}",
            f"{grand_totals['gst_collected']:.2f}",
            f"{grand_totals['gst_paid']:.2f}",
        ])
        report_table = Table(
            table_data,
            repeatRows=1,
            hAlign='LEFT'
        )
        report_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#172554')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        story.append(report_table)
        document.build(story)
        buffer.seek(0)
        return FileResponse(
            buffer,
            as_attachment=True,
            filename='gst_report.pdf'
        )

    return render(
        request,
        'accounting/gst_report.html',
        {'form': form}
    )


def generate_gst_bill(request, transaction_id):
    item = get_object_or_404(
        Transaction.objects.select_related('category').prefetch_related(
            'gst_records'
        ),
        pk=transaction_id
    )
    gst_records = list(item.gst_records.all())
    gst_amount = sum(
        (record.gst_amount for record in gst_records),
        Decimal('0.00')
    )
    gst_rates = ', '.join(
        f'{record.gst_percentage:.2f}%' for record in gst_records
    ) or '0.00%'
    final_total = item.amount + gst_amount

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f'GST Bill {item.pk}'
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'BillTitle',
        parent=styles['Title'],
        alignment=TA_CENTER,
        textColor=colors.HexColor('#172554')
    )
    details = [
        ['Bill number', f'FT-{item.pk:06d}'],
        ['Date', item.date.strftime('%d %b %Y')],
        ['Transaction type', item.type],
        ['Category', item.category.name if item.category else 'Uncategorized'],
    ]
    details_table = Table(details, colWidths=[45 * mm, 115 * mm])
    details_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e2e8f0')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    line_item = Table(
        [[
            'Description',
            'Taxable Amount (INR)',
            'GST Rate',
            'GST Amount (INR)',
            'Total (INR)',
        ], [
            Paragraph(escape(item.description), styles['BodyText']),
            f'{item.amount:.2f}',
            gst_rates,
            f'{gst_amount:.2f}',
            f'{final_total:.2f}',
        ]],
        colWidths=[65 * mm, 30 * mm, 20 * mm, 30 * mm, 25 * mm],
        repeatRows=1
    )
    line_item.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#172554')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    story = [
        Paragraph('GST BILL', title_style),
        Spacer(1, 8 * mm),
        details_table,
        Spacer(1, 10 * mm),
        line_item,
        Spacer(1, 8 * mm),
        Paragraph(
            f'<b>Invoice total: INR {final_total:.2f}</b>',
            styles['Heading2']
        ),
    ]
    document.build(story)
    buffer.seek(0)
    return FileResponse(
        buffer,
        as_attachment=True,
        filename=f'gst_bill_{item.pk}.pdf'
    )
