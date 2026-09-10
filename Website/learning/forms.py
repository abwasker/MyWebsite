"""Forms for the private study layer.

Plain Django forms, submitted by ordinary POST — no JavaScript. Matches the
site's existing pattern (the blog's comment form), keeps CSRF handling standard,
and means the study layer works with JS disabled.

⚠️ These forms are the ENTRY POINT FOR USER-GENERATED CONTENT into the learning
app — the first anywhere in it. Everything typed here is later rendered, and
`nh3` sanitisation in `blog.markdown_utils` is what makes that safe. Do not add a
rendering path that bypasses it.
"""
from django import forms

from .models import NoteStatus, Question, UserNote


class UserNoteForm(forms.ModelForm):
    """The `My Notes` editor — the content that exists NOWHERE ELSE (scope §2.8)."""

    class Meta:
        model = UserNote
        fields = ["body_markdown"]
        widgets = {
            "body_markdown": forms.Textarea(attrs={
                "rows": 14,
                "class": "learning-editor",
                "placeholder": "Your own thinking. Markdown, $LaTeX$ and [[wikilinks]] all work.",
            }),
        }
        labels = {"body_markdown": ""}

    # Blank is legitimate: clearing a note is a real edit, not a validation error.
    def clean_body_markdown(self):
        return self.cleaned_data.get("body_markdown") or ""


class NoteStatusForm(forms.ModelForm):
    class Meta:
        model = NoteStatus
        fields = ["status"]
        widgets = {"status": forms.Select(attrs={"class": "learning-select"})}
        labels = {"status": ""}


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ["title", "body"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "learning-input",
                "placeholder": "What don't you understand yet?",
            }),
            "body": forms.Textarea(attrs={
                "rows": 4, "class": "learning-editor",
                "placeholder": "Optional detail.",
            }),
        }
        labels = {"title": "", "body": ""}


class QuestionAnswerForm(forms.ModelForm):
    """Resolving a question. Saving an answer marks it answered (see the view)."""

    class Meta:
        model = Question
        fields = ["answer"]
        widgets = {
            "answer": forms.Textarea(attrs={
                "rows": 5, "class": "learning-editor",
                "placeholder": "What you worked out.",
            }),
        }
        labels = {"answer": ""}
