from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.cache import cache
from django.urls import reverse

from .forms import CommentForm
from .models import BlogPost, Comment, Poem

portfolio_content = {
    "name": "Anosh",
    "headline": "Developer | Creative Thinker | Problem Solver",
    "summary": "I build clean, practical web experiences with a focus on useful design and maintainable code.",
    "skills": [
        "Python",
        "Django",
        "HTML/CSS",
        "JavaScript",
        "Git & GitHub",
        "SQL",
    ],
    "experience": [
        {
            "role": "Freelance Developer",
            "company": "Independent",
            "period": "2024 - Present",
            "details": "Built personal and client web projects with Django, focusing on clear UX and stable deployment workflows.",
        }
    ],
    "projects": [
        {
            "name": "A Notion To Ponder",
            "description": "A combined portfolio and blog platform for publishing posts and showcasing work.",
            "url": "/",
            "link_label": "View Website",
        },
        {
            "name": "Personal Learning Projects",
            "description": "A collection of experiments around APIs, frontend UI patterns, and backend architecture.",
            "url": "/blog/",
            "link_label": "Read Blog",
        },
        {
            "name": "Resume Access",
            "description": "Password-protected resume view for employers and potential clients.",
            "url": "/resume/",
            "link_label": "Open Resume",
        },
    ],
    "education": "Add your degree, certifications, or formal training details here.",
}


def landing_page(request):
    latest_posts = BlogPost.objects.published().select_related("category")[:3]
    return render(
        request,
        "home.html",
        {
            "latest_posts": latest_posts,
        },
    )


def portfolio_page(request):
    return render(request, "portfolio.html", {"portfolio": portfolio_content})


def about_page(request):
    return render(request, "about.html")


def resume_page(request):
    session_key = "resume_access_granted"
    access_password = getattr(settings, "RESUME_ACCESS_PASSWORD", "viewresumeonwebsite")
    request_email = getattr(settings, "RESUME_REQUEST_EMAIL", "testemail@test.com")

    if request.method == "POST":
        if request.POST.get("action") == "lock":
            request.session.pop(session_key, None)
            messages.success(request, "Resume has been locked.")
            return redirect("resume")

        submitted_password = request.POST.get("resume_password", "")
        if submitted_password == access_password:
            request.session[session_key] = True
            messages.success(request, "Resume access granted.")
            return redirect("resume")
        messages.error(request, "Incorrect password. Please try again.")

    has_access = request.session.get(session_key, False)
    return render(
        request,
        "resume.html",
        {
            "has_access": has_access,
            "portfolio": portfolio_content,
            "request_email": request_email,
        },
    )


def blog_home(request):
    posts = (
        BlogPost.objects.published()
        .select_related("category")
        .prefetch_related("tags")
    )
    return render(
        request,
        "blog/all-posts.html",
        {
            "all_posts": posts,
        },
    )


def poetry_home(request):
    return render(
        request,
        "blog/poetry.html",
        {
            "poems": Poem.objects.all(),
        },
    )


def poem_detail(request, slug):
    identified_poem = get_object_or_404(Poem, slug=slug)
    return render(
        request,
        "blog/poem-detail.html",
        {
            "poem": identified_poem,
        },
    )


def post_details(request, slug):
    identified_post = get_object_or_404(
        BlogPost.objects.published().select_related("category").prefetch_related("tags"),
        slug=slug,
    )

    approved_comments = (
        identified_post.comments.filter(is_approved=True)
        .select_related("user")
    )

    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=reverse("login"))

        if not identified_post.allow_comments:
            messages.error(request, "Comments are closed for this post.")
            return redirect("post-details", slug=slug)

        comment_form = CommentForm(request.POST)
        if comment_form.is_valid():
            if comment_form.cleaned_data.get("website"):
                messages.error(request, "Comment could not be posted.")
                return redirect("post-details", slug=slug)

            rate_limit_message = _check_comment_rate_limit(request.user.id)
            if rate_limit_message:
                comment_form.add_error(None, rate_limit_message)
            else:
                Comment.objects.create(
                    post=identified_post,
                    post_slug=slug,
                    post_title=identified_post.title,
                    user=request.user,
                    content=comment_form.cleaned_data["content"],
                )
                messages.success(
                    request,
                    "Comment submitted. It will appear once approved by an admin.",
                )
                return redirect("post-details", slug=slug)
    else:
        comment_form = CommentForm() if identified_post.allow_comments else None

    return render(
        request,
        "blog/post-detail.html",
        {
            "post": identified_post,
            "approved_comments": approved_comments,
            "comment_form": comment_form,
        },
    )


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        messages.info(
            request,
            "Public signup is closed for v1. Accounts are created by the site admin.",
        )
        return redirect("signup")

    return render(request, "registration/signup.html")


def _check_comment_rate_limit(user_id):
    now = timezone.now()
    minute_key = f"comment-cooldown-{user_id}"
    daily_key = f"comment-daily-{user_id}-{now.date().isoformat()}"

    if cache.get(minute_key):
        return "Please wait a few seconds before posting another comment."

    day_count = cache.get(daily_key, 0)
    if day_count >= 20:
        return "You have reached the daily comment limit. Try again tomorrow."

    cache.set(minute_key, 1, timeout=20)
    cache.set(daily_key, day_count + 1, timeout=86400)
    return None
