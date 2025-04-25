from django.shortcuts import render
from datetime import date
# Create your views here.

all_posts = [
    {
        "slug": "ballroom-dancing",
    "image": "post-1.jpg",
    "author": "Anosh",
    "date": date(2025,4,24),
    "title": "Ballroom Dancing",
    "excerpt": "I dance about 12 dance styles that are called ballroom dancing. At the moment I'm working on a Cha-Cha and tango coreography",
    "content": """
                Consequuntur nulla a ex commodi possimus dicta doloremque, possimus aut 
                enim consectetur sed distinctio alias officia voluptas sit eum quia, est nesciunt 
                accusamus placeat nam repellat necessitatibus, recusandae ipsum nam debitis, alias totam 
                explicabo facere numquam odio voluptatem beatae natus dolor? Deleniti 
                deserunt asperiores aperiam, nobis ex odit fugit magnam omnis earum 
                ut debitis ea sapiente. Odio magni voluptatem nemo assumenda quisquam, 
                dolorum nam optio distinctio iure temporibus esse quia rem porro, atque 
                repellendus enim excepturi placeat aliquam quo sit molestias ad quibusdam. 
                Amet atque sed obcaecati odio iusto quis delectus eum voluptatibus ullam, 
                nisi eum quod, eum consequuntur voluptatibus qui perferendis possimus, aliquid 
                rerum praesentium expedita omnis, consectetur ullam accusamus repellendus hic dolores?
                """
    },
    {
        "slug": "hike-in-the-mountains",
        "image": "post-2.jpg",
        "author": "Max",
        "date": date(2024, 7, 21),
        "title": "Mountain Hiking",
        "excerpt": "There's nothing like the views you get when hiking in the mountains! And I wasn't even prepared for what happened whilst I was enjoying the view!",
        "content": """
          Lorem ipsum dolor sit amet consectetur adipisicing elit. Officiis nobis
          aperiam est praesentium, quos iste consequuntur omnis exercitationem quam
          velit labore vero culpa ad mollitia? Quis architecto ipsam nemo. Odio.

          Lorem ipsum dolor sit amet consectetur adipisicing elit. Officiis nobis
          aperiam est praesentium, quos iste consequuntur omnis exercitationem quam
          velit labore vero culpa ad mollitia? Quis architecto ipsam nemo. Odio.

          Lorem ipsum dolor sit amet consectetur adipisicing elit. Officiis nobis
          aperiam est praesentium, quos iste consequuntur omnis exercitationem quam
          velit labore vero culpa ad mollitia? Quis architecto ipsam nemo. Odio.
        """
    },
    {
        "slug": "into-the-woods",
        "image": "post-3.jpg",
        "author": "Maximilian",
        "date": date(2020, 8, 5),
        "title": "Nature At Its Best",
        "excerpt": "Nature is amazing! The amount of inspiration I get when walking in nature is incredible!",
        "content": """
          Lorem ipsum dolor sit amet consectetur adipisicing elit. Officiis nobis
          aperiam est praesentium, quos iste consequuntur omnis exercitationem quam
          velit labore vero culpa ad mollitia? Quis architecto ipsam nemo. Odio.

          Lorem ipsum dolor sit amet consectetur adipisicing elit. Officiis nobis
          aperiam est praesentium, quos iste consequuntur omnis exercitationem quam
          velit labore vero culpa ad mollitia? Quis architecto ipsam nemo. Odio.

          Lorem ipsum dolor sit amet consectetur adipisicing elit. Officiis nobis
          aperiam est praesentium, quos iste consequuntur omnis exercitationem quam
          velit labore vero culpa ad mollitia? Quis architecto ipsam nemo. Odio.
        """
    }

    ]

def get_date(post):
    return post['date']

def starting_page(request):
    sorted_posts = sorted(all_posts, key=get_date)
    latest_posts = sorted_posts[-3:]
    return render(request, "blog/index.html",{
        "posts": latest_posts
        })

def posts(request):
    return render(request, "blog/all-posts.html",{
        "all_posts": all_posts
    })

def post_details(request, slug):
    return render(request, "blog/post-detail.html")