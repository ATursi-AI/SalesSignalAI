import json
import re

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone

import requests

from core.models import TrackedCompetitor, CompetitorReview


@login_required
def competitor_list(request):
    profile = request.user.business_profile
    competitors = TrackedCompetitor.objects.filter(
        business=profile, is_active=True,
    ).order_by('-current_google_rating')

    context = {
        'competitors': competitors,
        'total': competitors.count(),
    }
    return render(request, 'competitors/list.html', context)


@login_required
def competitor_add(request):
    profile = request.user.business_profile

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        google_place_id = request.POST.get('google_place_id', '').strip()
        yelp_url = request.POST.get('yelp_url', '').strip()
        website = request.POST.get('website', '').strip()
        phone = request.POST.get('phone', '').strip()

        if not name:
            return JsonResponse({'error': 'Business name is required'}, status=400)

        competitor = TrackedCompetitor.objects.create(
            business=profile,
            name=name,
            google_place_id=google_place_id,
            yelp_url=yelp_url,
            website=website,
            phone=phone,
        )

        # Auto-populate from Google Places if place_id provided
        if google_place_id:
            _populate_from_google(competitor, google_place_id)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'competitor_id': competitor.id,
                'redirect': f'/competitors/{competitor.id}/',
            })

        return redirect('competitor_detail', competitor_id=competitor.id)

    return render(request, 'competitors/add.html')


@login_required
def competitor_detail(request, competitor_id):
    profile = request.user.business_profile
    competitor = get_object_or_404(
        TrackedCompetitor, id=competitor_id, business=profile,
    )

    reviews = competitor.reviews.all().order_by('-review_date')
    negative_reviews = reviews.filter(is_negative=True)
    opportunity_reviews = reviews.filter(is_opportunity=True)

    context = {
        'competitor': competitor,
        'reviews': reviews[:50],
        'negative_count': negative_reviews.count(),
        'opportunity_count': opportunity_reviews.count(),
        'total_reviews': reviews.count(),
    }
    return render(request, 'competitors/detail.html', context)


@login_required
def competitor_delete(request, competitor_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    profile = request.user.business_profile
    competitor = get_object_or_404(
        TrackedCompetitor, id=competitor_id, business=profile,
    )
    competitor.is_active = False
    competitor.save(update_fields=['is_active'])

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True})

    return redirect('competitor_list')


@login_required
def competitor_lookup(request):
    """AJAX endpoint: look up a business on Google Places API by name."""
    query = request.GET.get('q', '').strip()
    if not query or len(query) < 3:
        return JsonResponse({'results': []})

    api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return JsonResponse({'results': [], 'error': 'Google Maps API key not configured'})

    # Use Text Search (New) or fallback to legacy
    results = _google_places_search(query, api_key)
    return JsonResponse({'results': results})


def _google_places_search(query, api_key):
    """Search Google Places for a business."""
    # Try New Places API first
    url = 'https://places.googleapis.com/v1/places:searchText'
    headers = {
        'Content-Type': 'application/json',
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.websiteUri,places.nationalPhoneNumber',
    }
    payload = {'textQuery': query, 'maxResultCount': 5}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for place in data.get('places', []):
                results.append({
                    'place_id': place.get('id', ''),
                    'name': place.get('displayName', {}).get('text', ''),
                    'address': place.get('formattedAddress', ''),
                    'rating': place.get('rating'),
                    'review_count': place.get('userRatingCount'),
                    'website': place.get('websiteUri', ''),
                    'phone': place.get('nationalPhoneNumber', ''),
                })
            return results
    except requests.RequestException:
        pass

    # Fallback: legacy API
    try:
        legacy_url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
        resp = requests.get(legacy_url, params={
            'query': query, 'key': api_key,
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for place in data.get('results', [])[:5]:
                results.append({
                    'place_id': place.get('place_id', ''),
                    'name': place.get('name', ''),
                    'address': place.get('formatted_address', ''),
                    'rating': place.get('rating'),
                    'review_count': place.get('user_ratings_total'),
                    'website': '',
                    'phone': '',
                })
            return results
    except requests.RequestException:
        pass

    return []


def _populate_from_google(competitor, place_id):
    """Populate competitor details from Google Places API."""
    api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
    if not api_key:
        return

    # Try New API
    url = f'https://places.googleapis.com/v1/places/{place_id}'
    headers = {
        'X-Goog-Api-Key': api_key,
        'X-Goog-FieldMask': 'displayName,formattedAddress,rating,userRatingCount,websiteUri,nationalPhoneNumber',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            competitor.current_google_rating = data.get('rating')
            competitor.current_review_count = data.get('userRatingCount')
            if not competitor.website and data.get('websiteUri'):
                competitor.website = data['websiteUri']
            if not competitor.phone and data.get('nationalPhoneNumber'):
                competitor.phone = data['nationalPhoneNumber']
            competitor.last_checked = timezone.now()
            competitor.save()
            return
    except requests.RequestException:
        pass

    # Fallback: legacy Place Details
    try:
        legacy_url = 'https://maps.googleapis.com/maps/api/place/details/json'
        resp = requests.get(legacy_url, params={
            'place_id': place_id,
            'fields': 'name,rating,user_ratings_total,website,formatted_phone_number',
            'key': api_key,
        }, timeout=10)
        if resp.status_code == 200:
            result = resp.json().get('result', {})
            competitor.current_google_rating = result.get('rating')
            competitor.current_review_count = result.get('user_ratings_total')
            if not competitor.website and result.get('website'):
                competitor.website = result['website']
            if not competitor.phone and result.get('formatted_phone_number'):
                competitor.phone = result['formatted_phone_number']
            competitor.last_checked = timezone.now()
            competitor.save()
    except requests.RequestException:
        pass
