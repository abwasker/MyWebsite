from django.urls import path

from . import views

urlpatterns = [
    path("", views.path_index, name="learning-index"),
    path("<slug:path_slug>/", views.path_overview, name="learning-path"),
    # Kind is in the URL rather than a single /notes/<slug>/ route: the slug is
    # only unique per path, and a reader benefits from seeing what they are about
    # to open. It also leaves room for kind-specific pages later.
    path("<slug:path_slug>/modules/<slug:slug>/", views.module_detail, name="learning-module"),
    path("<slug:path_slug>/concepts/<slug:slug>/", views.concept_detail, name="learning-concept"),
    path("<slug:path_slug>/sources/<slug:slug>/", views.source_detail, name="learning-source"),
]
