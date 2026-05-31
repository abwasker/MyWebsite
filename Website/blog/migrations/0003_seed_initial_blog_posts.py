from datetime import datetime, timezone

from django.db import migrations


INITIAL_POSTS = [
    {
        "slug": "ballroom-dancing",
        "cover_image": "post-1.jpg",
        "author_name": "Anosh",
        "published_at": datetime(2025, 4, 24, 12, 0, tzinfo=timezone.utc),
        "title": "Ballroom Dancing",
        "excerpt": "I dance around 12 ballroom styles. Right now I am rehearsing a new Cha-Cha and Tango choreography.",
        "markdown_body": """
This year I started building routines with more intention. Every practice session has a single goal,
and that has helped me improve faster with cleaner footwork and better timing.

I also started recording sessions and reviewing posture, frame, and musicality. It is uncomfortable at first,
but seeing each detail has become one of the best learning tools I have used.
""".strip(),
    },
    {
        "slug": "hike-in-the-mountains",
        "cover_image": "post-2.jpg",
        "author_name": "Max",
        "published_at": datetime(2024, 7, 21, 12, 0, tzinfo=timezone.utc),
        "title": "Mountain Hiking",
        "excerpt": "A mountain trail, changing weather, and a view that made every uphill step worth it.",
        "markdown_body": """
The trail started easy, but the final ascent was steep and technical. Slowing down and pacing each section
made the whole hike more enjoyable.

At the summit, cloud cover lifted for a few minutes and the entire valley opened up. Moments like that
are exactly why I keep going back outdoors.
""".strip(),
    },
    {
        "slug": "into-the-woods",
        "cover_image": "post-3.jpg",
        "author_name": "Maximilian",
        "published_at": datetime(2020, 8, 5, 12, 0, tzinfo=timezone.utc),
        "title": "Nature At Its Best",
        "excerpt": "Walking through the woods resets the mind and always leaves me with fresh ideas.",
        "markdown_body": """
Time in nature gives me creative energy I do not get from screens. I carry a notebook on these walks
and collect ideas for writing, product features, and visual concepts.

This habit has become a reliable way to think clearly, especially when I am stuck on a technical problem.
""".strip(),
    },
]


def seed_initial_posts(apps, schema_editor):
    BlogPost = apps.get_model("blog", "BlogPost")
    Category = apps.get_model("blog", "Category")
    Comment = apps.get_model("blog", "Comment")

    blog_category, _ = Category.objects.get_or_create(
        slug="blog",
        defaults={
            "name": "Blog",
            "description": "General writing and updates.",
        },
    )

    for post_data in INITIAL_POSTS:
        BlogPost.objects.update_or_create(
            slug=post_data["slug"],
            defaults={
                **post_data,
                "status": "published",
                "category": blog_category,
                "allow_comments": True,
            },
        )

    for comment in Comment.objects.filter(post__isnull=True):
        post = BlogPost.objects.filter(slug=comment.post_slug).first()
        if post:
            comment.post = post
            comment.save(update_fields=["post"])


def unseed_initial_posts(apps, schema_editor):
    BlogPost = apps.get_model("blog", "BlogPost")
    Category = apps.get_model("blog", "Category")

    BlogPost.objects.filter(slug__in=[post["slug"] for post in INITIAL_POSTS]).delete()
    Category.objects.filter(slug="blog", posts__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0002_blogpost_category_tag_comment_post_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_initial_posts, unseed_initial_posts),
    ]
