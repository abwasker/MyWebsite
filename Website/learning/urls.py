from django.urls import path, re_path

from . import study_views, views

# The three kind segments, constrained IN THE PATTERN so an unknown one 404s at
# routing rather than reaching a view. Keeps one route per concern instead of
# three near-identical copies of each.
KIND = r"(?P<kind_segment>modules|concepts|sources)"
SLUG = r"(?P<slug>[-\w]+)"
PATH = r"(?P<path_slug>[-\w]+)"

urlpatterns = [
    # ---- PUBLIC (views.py — never queries a user-owned model) ----
    path("", views.path_index, name="learning-index"),
    path("<slug:path_slug>/", views.path_overview, name="learning-path"),
    path("<slug:path_slug>/modules/<slug:slug>/", views.module_detail, name="learning-module"),
    path("<slug:path_slug>/concepts/<slug:slug>/", views.concept_detail, name="learning-concept"),
    path("<slug:path_slug>/sources/<slug:slug>/", views.source_detail, name="learning-source"),

    # ---- PRIVATE (study_views.py — every one carries @study_gate) ----
    # Kept together and clearly labelled so a future addition lands in the right
    # half. test_every_non_public_route_is_gated enforces this rather than
    # trusting the comment.
    path("<slug:path_slug>/dashboard/", study_views.dashboard, name="learning-dashboard"),
    re_path(rf"^{PATH}/{KIND}/{SLUG}/study/$",
            study_views.study_note, name="learning-study"),
    re_path(rf"^{PATH}/{KIND}/{SLUG}/study/note/$",
            study_views.save_user_note, name="learning-save-note"),
    re_path(rf"^{PATH}/{KIND}/{SLUG}/study/status/$",
            study_views.set_status, name="learning-set-status"),
    re_path(rf"^{PATH}/{KIND}/{SLUG}/study/tasks/$",
            study_views.save_tasks, name="learning-save-tasks"),
    re_path(rf"^{PATH}/{KIND}/{SLUG}/study/question/$",
            study_views.ask_question, name="learning-ask-question"),
    path("<slug:path_slug>/questions/<int:question_id>/answer/",
         study_views.answer_question, name="learning-answer-question"),
]

# Route names that are deliberately PUBLIC. The gating test treats every other
# name in this module as private and asserts it rejects an anonymous request, so
# adding a route without gating it fails the suite.
PUBLIC_ROUTE_NAMES = {
    "learning-index",
    "learning-path",
    "learning-module",
    "learning-concept",
    "learning-source",
}
