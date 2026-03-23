from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator

from core.models import BlogPost


def blog_list(request):
    """Paginated list of published blog posts."""
    posts = BlogPost.objects.filter(is_published=True).order_by('-published_at')
    paginator = Paginator(posts, 10)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'blog/list.html', {
        'posts': page,
        'page_obj': page,
    })


def blog_detail(request, slug):
    """Single blog post."""
    post = get_object_or_404(BlogPost, slug=slug, is_published=True)
    related = (
        BlogPost.objects
        .filter(is_published=True)
        .exclude(pk=post.pk)
        .order_by('-published_at')[:3]
    )
    return render(request, 'blog/detail.html', {
        'post': post,
        'related_posts': related,
    })
