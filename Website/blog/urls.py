from django.urls import path

from . import views
urlpatterns = [
    path("", views.blog_home, name="all-posts"),
    path("posts/", views.blog_home, name="blog-posts"),
    path("posts/<slug:slug>/", views.post_details, name="post-details"),
    path("poetry/", views.poetry_home, name="poetry-home"),
    path("poetry/<slug:slug>/", views.poem_detail, name="poem-detail"),
]