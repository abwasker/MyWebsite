from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.cache import cache

from .forms import CommentForm, SignUpForm
from .models import Comment, Poem

all_posts = [
    {
        "slug": "ballroom-dancing",
        "image": "post-1.jpg",
        "author": "Anosh",
        "date": date(2025, 4, 24),
        "title": "Ballroom Dancing",
        "excerpt": "I dance around 12 ballroom styles. Right now I am rehearsing a new Cha-Cha and Tango choreography.",
        "content": """
            This year I started building routines with more intention. Every practice session has a single goal,
            and that has helped me improve faster with cleaner footwork and better timing.

            I also started recording sessions and reviewing posture, frame, and musicality. It is uncomfortable at first,
            but seeing each detail has become one of the best learning tools I have used.
        """,
    },
    {
        "slug": "hike-in-the-mountains",
        "image": "post-2.jpg",
        "author": "Max",
        "date": date(2024, 7, 21),
        "title": "Mountain Hiking",
        "excerpt": "A mountain trail, changing weather, and a view that made every uphill step worth it.",
        "content": """
            The trail started easy, but the final ascent was steep and technical. Slowing down and pacing each section
            made the whole hike more enjoyable.

            At the summit, cloud cover lifted for a few minutes and the entire valley opened up. Moments like that
            are exactly why I keep going back outdoors.
        """,
    },
    {
        "slug": "into-the-woods",
        "image": "post-3.jpg",
        "author": "Maximilian",
        "date": date(2020, 8, 5),
        "title": "Nature At Its Best",
        "excerpt": "Walking through the woods resets the mind and always leaves me with fresh ideas.",
        "content": """
            Time in nature gives me creative energy I do not get from screens. I carry a notebook on these walks
            and collect ideas for writing, product features, and visual concepts.

            This habit has become a reliable way to think clearly, especially when I am stuck on a technical problem.
        """,
    },
]

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


def get_date(post):
    return post["date"]


def landing_page(request):
    sorted_posts = sorted(all_posts, key=get_date, reverse=True)
    return render(
        request,
        "home.html",
        {
            "latest_posts": sorted_posts[:3],
        },
    )


def portfolio_page(request):
    return render(request, "portfolio.html", {"portfolio": portfolio_content})


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
    sorted_posts = sorted(all_posts, key=get_date, reverse=True)
    return render(
        request,
        "blog/all-posts.html",
        {
            "all_posts": sorted_posts,
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
    identified_post = next((post for post in all_posts if post["slug"] == slug), None)
    if identified_post is None:
        raise Http404("Post not found.")

    approved_comments = (
        Comment.objects.filter(post_slug=slug, is_approved=True)
        .select_related("user")
    )

    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")

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
                    post_slug=slug,
                    post_title=identified_post["title"],
                    user=request.user,
                    content=comment_form.cleaned_data["content"],
                )
                messages.success(
                    request,
                    "Comment submitted. It will appear once approved by an admin.",
                )
                return redirect("post-details", slug=slug)
    else:
        comment_form = CommentForm()

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
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Your account has been created.")
            next_url = request.POST.get("next") or request.GET.get("next")
            return redirect(next_url or "home")
    else:
        form = SignUpForm()

    return render(request, "registration/signup.html", {"form": form})


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
