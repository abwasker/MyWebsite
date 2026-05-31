from django import template

from blog.markdown_utils import render_markdown_text


register = template.Library()


@register.simple_tag
def render_markdown(source, owner=None):
    return render_markdown_text(source, owner)
