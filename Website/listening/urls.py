from django.urls import path

from . import views

urlpatterns = [
    path("", views.listening_home, name="listening-home"),
    # The format is validated against export.RENDERERS in the view, so an
    # unknown value is a 404 rather than an error page.
    path("download/<str:output_format>/", views.listening_download, name="listening-download"),
]
