from django.shortcuts import render


def about_page(request):
    return render(request, 'static_pages/about.html')


def privacy_page(request):
    return render(request, 'static_pages/privacy.html')


def terms_page(request):
    return render(request, 'static_pages/terms.html')
