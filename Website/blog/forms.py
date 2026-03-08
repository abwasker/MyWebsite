from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")


class CommentForm(forms.Form):
    content = forms.CharField(
        label="Comment",
        max_length=1200,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Share your thoughts on this post...",
            }
        ),
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if len(content) < 3:
            raise forms.ValidationError("Comment must be at least 3 characters.")
        return content
