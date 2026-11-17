
from django import forms
from .models import Category, Transaction


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name']

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }


class TransactionForm(forms.ModelForm):

    class Meta:
        model = Transaction

        fields = [
            'date',
            'description',
            'amount',
            'type',
            'category',
        ]

        widgets = {
            'date': forms.DateInput(
                attrs={'type': 'date', 'class': 'form-control'}
            ),

            'description': forms.Textarea(
                attrs={'class': 'form-control', 'rows': 3}
            ),

            'amount': forms.NumberInput(
                attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}
            ),

            'type': forms.Select(
                attrs={'class': 'form-control'}
            ),

            'category': forms.Select(
                attrs={'class': 'form-control'}
            ),
        }

    def clean_amount(self):
        amount = self.cleaned_data['amount']

        if amount <= 0:
            raise forms.ValidationError(
                "Amount must be greater than zero."
            )

        return amount


class TransactionCategoryForm(forms.ModelForm):
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Transaction
        fields = ['category']


class GSTReportFilterForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        empty_label='All categories',
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError(
                "Start date must be on or before end date."
            )

        return cleaned_data
