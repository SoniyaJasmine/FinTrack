from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .forms import CategoryForm, GSTReportFilterForm, TransactionForm
from .models import Category, Gst, Transaction
from .views import _get_gst_report_totals


class TransactionCategoryTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Food')

    def test_add_transaction_saves_selected_category(self):
        response = self.client.post(
            reverse('add_transaction'),
            {
                'date': '2026-11-16',
                'description': 'Lunch',
                'amount': '25.00',
                'type': 'Expense',
                'category': self.category.pk,
            }
        )

        self.assertRedirects(response, reverse('transaction_list'))
        self.assertEqual(
            Transaction.objects.get(description='Lunch').category,
            self.category
        )

    def test_add_transaction_requires_a_category(self):
        response = self.client.post(
            reverse('add_transaction'),
            {
                'date': '2026-11-16',
                'description': 'Lunch',
                'amount': '25.00',
                'type': 'Expense',
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transaction.objects.exists())

    def test_categorize_transactions_updates_existing_transaction(self):
        transaction = Transaction.objects.create(
            date='2026-11-16',
            description='Coffee',
            amount='5.00',
            type='Expense'
        )
        page = self.client.get(reverse('categorize_transactions'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Coffee')
        self.assertContains(page, 'form-0-category')

        response = self.client.post(
            reverse('categorize_transactions'),
            {
                'form-TOTAL_FORMS': '1',
                'form-INITIAL_FORMS': '1',
                'form-MIN_NUM_FORMS': '0',
                'form-MAX_NUM_FORMS': '1000',
                'form-0-id': str(transaction.pk),
                'form-0-category': str(self.category.pk),
            }
        )

        self.assertRedirects(
            response,
            reverse('categorize_transactions')
        )
        transaction.refresh_from_db()
        self.assertEqual(transaction.category, self.category)

    def test_add_category_creates_category(self):
        response = self.client.post(
            reverse('add_category'),
            {'name': 'Travel'}
        )

        self.assertRedirects(response, reverse('add_transaction'))
        self.assertTrue(Category.objects.filter(name='Travel').exists())

    def test_gst_report_filters_by_date_and_category_and_returns_pdf(self):
        included = Transaction.objects.create(
            date='2026-11-16',
            description='Included transaction',
            amount='100.00',
            type='Income',
            category=self.category
        )
        Gst.objects.create(
            transaction=included,
            gst_percentage='18.00',
            gst_amount='18.00'
        )
        other_category = Category.objects.create(name='Travel')
        excluded = Transaction.objects.create(
            date='2026-11-15',
            description='Excluded transaction',
            amount='50.00',
            type='Expense',
            category=other_category
        )
        Gst.objects.create(
            transaction=excluded,
            gst_percentage='18.00',
            gst_amount='9.00'
        )
        category_totals, grand_totals = _get_gst_report_totals(
            date(2026, 11, 16),
            date(2026, 11, 16),
            self.category
        )
        self.assertEqual(category_totals['Food']['count'], 1)
        self.assertEqual(
            category_totals['Food']['gst_collected'],
            Decimal('18.00')
        )
        self.assertEqual(grand_totals['income'], Decimal('100.00'))
        self.assertNotIn('Travel', category_totals)

        response = self.client.get(
            reverse('generate_gst_report'),
            {
                'start_date': '2026-11-16',
                'end_date': '2026-11-16',
                'category': self.category.pk,
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        pdf_content = b''.join(response.streaming_content)
        self.assertTrue(pdf_content.startswith(b'%PDF'))

    def test_gst_report_page_and_invalid_date_range(self):
        page = self.client.get(reverse('generate_gst_report'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Download GST Report')

        response = self.client.get(
            reverse('generate_gst_report'),
            {
                'start_date': '2026-11-17',
                'end_date': '2026-11-16',
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Start date must be on or before end date.')

    def test_gst_bill_returns_pdf_for_transaction(self):
        transaction = Transaction.objects.create(
            date='2026-11-16',
            description='Consulting services',
            amount='100.00',
            type='Income',
            category=self.category
        )
        Gst.objects.create(
            transaction=transaction,
            gst_percentage='18.00',
            gst_amount='18.00'
        )

        response = self.client.get(
            reverse('generate_gst_bill', args=[transaction.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('gst_bill_', response['Content-Disposition'])
        self.assertTrue(
            b''.join(response.streaming_content).startswith(b'%PDF')
        )

    @patch('accounting.views.timezone.localdate', return_value=date(2026, 11, 16))
    def test_predictive_analytics_uses_local_monthly_history(self, _today):
        for transaction_date, amount, category in (
            ('2026-09-10', '300.00', self.category),
            ('2026-10-10', '600.00', self.category),
            ('2026-11-10', '900.00', self.category),
        ):
            Transaction.objects.create(
                date=transaction_date,
                description='Monthly expense',
                amount=amount,
                type='Expense',
                category=category
            )

        response = self.client.get(reverse('predictive_analytics'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your transaction data stays in this application.')
        self.assertEqual(len(response.context['historical_data']), 24)
        self.assertEqual(len(response.context['predictions']), 3)
        self.assertEqual(
            response.context['predictions'][0]['amount'],
            Decimal('300.00')
        )
        self.assertIn('Food', ' '.join(response.context['insights']))
        self.assertIn('300.00', response.content.decode())

    @patch('accounting.views.timezone.localdate', return_value=date(2026, 11, 16))
    def test_predictive_analytics_handles_empty_history(self, _today):
        response = self.client.get(reverse('predictive_analytics'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_history'])
        self.assertEqual(response.context['predictions'], [])
        self.assertContains(response, 'There are no transactions')


class AccountingFormTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Office')

    def test_transaction_form_accepts_valid_transaction(self):
        form = TransactionForm(data={
            'date': '2025-06-15',
            'description': 'Office supplies',
            'amount': '75.25',
            'type': 'Expense',
            'category': self.category.pk,
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_transaction_form_rejects_nonpositive_amount_and_missing_category(self):
        form = TransactionForm(data={
            'date': '2025-06-15',
            'description': 'Office supplies',
            'amount': '0.00',
            'type': 'Expense',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)
        self.assertIn('category', form.errors)

    def test_category_form_rejects_blank_name(self):
        form = CategoryForm(data={'name': ' '})

        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)

    def test_gst_report_form_rejects_reversed_date_range(self):
        form = GSTReportFilterForm(data={
            'start_date': '2025-12-31',
            'end_date': '2025-01-01',
            'category': self.category.pk,
        })

        self.assertFalse(form.is_valid())
        self.assertIn(
            'Start date must be on or before end date.',
            form.non_field_errors()
        )


class TransactionFixtureTests(TestCase):
    fixtures = ['sample_transactions.json']

    def test_fixture_has_twenty_transactions_across_a_year(self):
        self.assertEqual(Transaction.objects.count(), 20)
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(
            Transaction.objects.order_by('date').first().date.isoformat(),
            '2025-01-05'
        )
        self.assertEqual(
            Transaction.objects.order_by('-date').first().date.isoformat(),
            '2025-12-20'
        )

    def test_report_view_generates_pdf_from_fixture_data(self):
        transaction = Transaction.objects.order_by('date').first()
        Gst.objects.create(
            transaction=transaction,
            gst_percentage='18.00',
            gst_amount='9.00'
        )

        response = self.client.get(
            reverse('generate_gst_report'),
            {
                'start_date': '2025-01-01',
                'end_date': '2025-12-31',
                'category': transaction.category_id,
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertTrue(
            b''.join(response.streaming_content).startswith(b'%PDF')
        )
