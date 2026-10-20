"""Routes for the practice tools.

⚠️ The URL slug stays ``interval-ladder`` even though the scope note was renamed
to "Music Interval Learning App" on 2026-10-20. A document title is not a product
name: the tool is still called Interval Ladder on screen, and renaming a note
should not silently change what a visitor sees in the address bar. Whether the
URL itself should change is open question 2 in the scope.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("interval-ladder/", views.interval_ladder, name="interval-ladder"),
    path(
        "interval-ladder/attempts/",
        views.save_attempts,
        name="interval-ladder-attempts",
    ),
    path(
        "interval-ladder/progress/",
        views.progress,
        name="interval-ladder-progress",
    ),
]
