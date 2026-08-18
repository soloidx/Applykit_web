from django import forms


class CoverLetterForm(forms.Form):
    body_html = forms.CharField(
        required=False,
        label="Cover Letter",
        widget=forms.Textarea(
            attrs={
                "rows": 18,
                "autocomplete": "off",
                "data-cover-letter-input": "true",
            }
        ),
    )

    def clean_body_html(self) -> str:
        return str(self.cleaned_data.get("body_html", ""))
