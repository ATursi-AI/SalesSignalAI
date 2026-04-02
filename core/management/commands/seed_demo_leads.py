import hashlib
import random
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from core.models import Lead, LeadAssignment, BusinessProfile, ServiceCategory


DEMO_LEADS = [
    {
        'platform': 'craigslist',
        'content': "Need a plumber ASAP! My kitchen sink is backed up and water is everywhere. Located in Garden City, Long Island. Please call if you can come today. Will pay for emergency service.",
        'location': 'Garden City, NY',
        'zip': '11530',
        'author': 'desperate_homeowner',
        'age_minutes': 15,
        'urgency': 'hot',
        'ai_summary': 'Emergency plumbing needed - backed up kitchen sink flooding, Garden City, wants same-day service.',
        'ai_response': "Hi there! I saw your post about the kitchen sink backup. I'm a licensed plumber based nearby and can come out today. I specialize in emergency drain clearing and can usually resolve kitchen backups within an hour. Would you like me to head over? Feel free to call me directly.",
    },
    {
        'platform': 'reddit',
        'content': "Anyone know a good electrician in Mineola area? I need some outlets added in my garage and maybe a panel upgrade. Not super urgent but would like to get it done in the next couple weeks. Recommendations appreciated!",
        'location': 'Mineola, NY',
        'zip': '11501',
        'author': 'u/garage_workshop_guy',
        'age_minutes': 45,
        'urgency': 'hot',
        'ai_summary': 'Electrician needed in Mineola for garage outlet installation and potential panel upgrade.',
        'ai_response': "Hey! I'm a licensed electrician serving the Mineola area. Adding garage outlets and panel upgrades are some of my most common jobs. I'd be happy to come take a look and give you a free estimate. I can usually schedule within a few days. Want me to DM you my number?",
    },
    {
        'platform': 'patch',
        'content': "Looking for recommendations for a reliable house cleaning service in the Westbury/New Cassel area. Bi-weekly cleaning for a 3-bedroom house. Must be insured and have references. Budget around $150-200 per visit.",
        'location': 'Westbury, NY',
        'zip': '11590',
        'author': 'Sarah M.',
        'age_minutes': 90,
        'urgency': 'warm',
        'ai_summary': 'House cleaning needed in Westbury, bi-weekly, 3BR, insured with references, budget $150-200.',
        'ai_response': "Hi Sarah! I run a fully insured residential cleaning service in the Westbury area. We'd love to take care of your home. For a 3-bedroom bi-weekly cleaning, we're right in your budget range. Happy to provide references from several long-term clients in your neighborhood. Can we schedule a quick walkthrough?",
    },
    {
        'platform': 'nextdoor',
        'content': "Does anyone have a landscaper they love? Our yard is a disaster after winter and we need a full spring cleanup plus regular mowing service. We're in Levittown near the Wantagh border.",
        'location': 'Levittown, NY',
        'zip': '11756',
        'author': 'Mike T.',
        'age_minutes': 120,
        'urgency': 'warm',
        'ai_summary': 'Landscaper needed in Levittown for spring cleanup and regular mowing service.',
        'ai_response': "Hey Mike! I do full spring cleanups and weekly mowing in the Levittown/Wantagh area. I can come by for a free estimate this week. Most spring cleanups I do take about a day and include debris removal, bed edging, and first mow. My regular mowing clients love the consistency. Want to set up a time?",
    },
    {
        'platform': 'facebook',
        'content': "URGENT: Can anyone recommend an HVAC company? Our furnace just died and it's 20 degrees outside. We have small kids and need someone who can come out today. We're in Hicksville. Price is not an issue, just need heat!",
        'location': 'Hicksville, NY',
        'zip': '11801',
        'author': 'Jennifer Walsh',
        'age_minutes': 10,
        'urgency': 'hot',
        'ai_summary': 'URGENT: Furnace failure in Hicksville, family with small children, needs same-day emergency HVAC repair.',
        'ai_response': "Jennifer, I can help! I'm an HVAC technician and I'm available to come out right now. I carry common furnace parts on my truck so there's a good chance I can get you up and running today. I'll head your way - please call me at your earliest convenience so I can get your address.",
    },
    {
        'platform': 'reddit',
        'content': "Looking for a roofer on Long Island. I think I have a small leak - noticed a water stain on the ceiling after the last storm. House is in Massapequa, ranch style, probably 20 years since last roof. Need someone to take a look and give me an honest assessment.",
        'location': 'Massapequa, NY',
        'zip': '11758',
        'author': 'u/LI_homeowner_2005',
        'age_minutes': 180,
        'urgency': 'warm',
        'ai_summary': 'Roofer needed in Massapequa for leak inspection, water stain after storm, older ranch roof.',
        'ai_response': "Hi! I'm a licensed roofing contractor on Long Island. A water stain after a storm is definitely something to get checked out quickly before it causes more damage. I offer free roof inspections and will give you an honest assessment - whether it's a simple repair or time for a replacement. Can I come take a look this week?",
    },
    {
        'platform': 'craigslist',
        'content': "Need junk removed from my garage and basement. Mostly old furniture, boxes, and some appliances. About a truckload worth. Located in Plainview. Looking for someone this Saturday if possible.",
        'location': 'Plainview, NY',
        'zip': '11803',
        'author': '',
        'age_minutes': 300,
        'urgency': 'new',
        'ai_summary': 'Junk removal needed in Plainview - garage and basement cleanout, approx one truckload, Saturday preferred.',
        'ai_response': "Hi! I do junk removal in the Plainview area and Saturday works great for me. For about a truckload of furniture, boxes, and appliances, I can give you a firm quote once I see everything. I handle all the heavy lifting and make sure everything is disposed of responsibly. Want to schedule a time Saturday morning?",
    },
    {
        'platform': 'houzz',
        'content': "We're planning a kitchen remodel and looking for a contractor in the Nassau County area. Want to update cabinets, countertops, and flooring. Maybe open up the wall to the dining room. Looking for someone with a portfolio of similar projects.",
        'location': 'Nassau County, NY',
        'zip': '11501',
        'author': 'houzz_user_design',
        'age_minutes': 360,
        'urgency': 'new',
        'ai_summary': 'Kitchen remodel contractor needed in Nassau County - cabinets, counters, flooring, possible wall removal.',
        'ai_response': "Hi! I'm a general contractor specializing in kitchen remodels throughout Nassau County. Opening up walls, updating cabinets, and new countertops are right in my wheelhouse. I'd love to show you our portfolio of completed kitchens in the area. Can we schedule a consultation to discuss your vision and walk through the space?",
    },
    {
        'platform': 'alignable',
        'content': "Our office building (3 floors) needs a new commercial cleaning company. Current company has been unreliable. Located in Hempstead. Need nightly cleaning Mon-Fri plus floor waxing quarterly. Multi-year contract preferred.",
        'location': 'Hempstead, NY',
        'zip': '11550',
        'author': 'Hempstead Property Mgmt',
        'age_minutes': 480,
        'urgency': 'new',
        'ai_summary': 'Commercial cleaning needed in Hempstead - 3-floor office building, nightly Mon-Fri, quarterly floor waxing.',
        'ai_response': "Hello! I run a commercial cleaning company serving the Hempstead area. We specialize in office buildings exactly like yours and understand how important reliability is. We offer nightly cleaning with dedicated crews and quarterly floor waxing. I'd love to walk through your building and put together a competitive proposal for a multi-year agreement.",
    },
    {
        'platform': 'google_qna',
        'content': "Is this company still in business? I need an emergency locksmith in Freeport and called 3 different numbers with no answer. Can someone help? I'm locked out of my house.",
        'location': 'Freeport, NY',
        'zip': '11520',
        'author': 'Google User',
        'age_minutes': 25,
        'urgency': 'hot',
        'ai_summary': 'Emergency lockout in Freeport - person locked out of house, competitor phones unanswered.',
        'ai_response': "I'm a mobile locksmith and I can be in Freeport within 20 minutes! I handle residential lockouts quickly and affordably. Call or text me right now and I'll head your way immediately.",
    },
    {
        'platform': 'yelp_review',
        'content': "Terrible experience with this painter. They started the job and then disappeared for a week. When they came back the work was sloppy - paint drips everywhere, uneven lines, didn't tape properly. Had to hire someone else to fix it. Located in Merrick. Don't waste your money.",
        'location': 'Merrick, NY',
        'zip': '11566',
        'author': 'David R.',
        'age_minutes': 600,
        'urgency': 'new',
        'ai_summary': 'Competitor negative review in Merrick - sloppy painting job, abandoned mid-project, reviewer needs fix.',
        'ai_response': "Hi David, I'm sorry to hear about your experience. As a professional painter in the Merrick area, I take pride in clean, precise work. I'd be happy to come take a look at what needs to be fixed and give you a fair estimate to make it right. No disappearing acts - I always complete what I start.",
    },
    {
        'platform': 'craigslist',
        'content': "Looking for someone to pressure wash my driveway, patio, and vinyl siding. House is in East Meadow. Would also like gutters cleaned if you offer that. Prefer weekend availability.",
        'location': 'East Meadow, NY',
        'zip': '11554',
        'author': '',
        'age_minutes': 200,
        'urgency': 'warm',
        'ai_summary': 'Pressure washing in East Meadow - driveway, patio, vinyl siding plus gutter cleaning, weekends.',
        'ai_response': "Hi! I offer pressure washing and gutter cleaning services in East Meadow. Driveway, patio, and siding is a popular combo - usually takes about half a day and makes a huge difference. I have weekend availability this month. Want me to swing by for a free estimate?",
    },
    {
        'platform': 'reddit',
        'content': "My mom needs a handyman in the Oceanside area. She's elderly and living alone - needs some grab bars installed in the bathroom, a leaky faucet fixed, and a few other small things around the house. Looking for someone patient and trustworthy who won't overcharge her.",
        'location': 'Oceanside, NY',
        'zip': '11572',
        'author': 'u/caring_son_89',
        'age_minutes': 150,
        'urgency': 'warm',
        'ai_summary': 'Handyman needed in Oceanside for elderly resident - grab bars, leaky faucet, small repairs.',
        'ai_response': "I'd be happy to help your mom! I'm a handyman in the Oceanside area and I regularly do exactly this kind of work for elderly homeowners. Grab bar installation, faucet repair, and small fix-ups are my bread and butter. I'm patient, fairly priced, and happy to provide references from other seniors I've helped. Feel free to DM me.",
    },
    {
        'platform': 'patch',
        'content': "Tree fell in my backyard during last night's storm in Baldwin. Need someone with a chainsaw and truck to remove it. It's a medium-sized oak, didn't hit the house thankfully. Looking for competitive pricing.",
        'location': 'Baldwin, NY',
        'zip': '11510',
        'author': 'Tom K.',
        'age_minutes': 70,
        'urgency': 'hot',
        'ai_summary': 'Storm-downed oak tree removal in Baldwin, medium-sized, needs chainsaw and hauling.',
        'ai_response': "Tom, glad it didn't hit the house! I'm a tree service company nearby and can come assess the situation today. We have the equipment to safely cut up and remove the oak, including stump grinding if needed. I'll bring my crew out for a free estimate. Can you send me the address?",
    },
    {
        'platform': 'nextdoor',
        'content': "We just moved to Lynbrook and need a pest control company. Seeing a lot of ants in the kitchen and some mice evidence in the garage. Would love recommendations for someone who does regular quarterly treatments.",
        'location': 'Lynbrook, NY',
        'zip': '11563',
        'author': 'New Lynbrook Resident',
        'age_minutes': 250,
        'urgency': 'new',
        'ai_summary': 'Pest control needed in Lynbrook - ants in kitchen, mice in garage, wants quarterly treatments.',
        'ai_response': "Welcome to Lynbrook! I run a local pest control company and would love to help you get settled in pest-free. I'll do a full home inspection, treat the ant and mouse issues right away, and set you up on a quarterly prevention plan. First treatment includes a thorough inspection at no extra charge. Want to schedule a visit?",
    },
]


class Command(BaseCommand):
    help = 'Seed demo leads for testing the lead feed and dashboard'

    def handle(self, *args, **options):
        profiles = BusinessProfile.objects.filter(onboarding_complete=True)
        if not profiles.exists():
            profiles = BusinessProfile.objects.all()
        if not profiles.exists():
            self.stdout.write(self.style.ERROR('No business profiles found. Register a user first.'))
            return

        categories = {c.name.lower(): c for c in ServiceCategory.objects.all()}
        now = timezone.now()
        created_count = 0

        for data in DEMO_LEADS:
            content_hash = hashlib.sha256(data['content'].encode()).hexdigest()

            if Lead.objects.filter(content_hash=content_hash).exists():
                continue

            age = timedelta(minutes=data['age_minutes'])
            discovered = now - age

            # Try to match a service category
            service_type = None
            content_lower = data['content'].lower()
            for name, cat in categories.items():
                keywords = cat.default_keywords or []
                for kw in keywords:
                    if kw.lower() in content_lower:
                        service_type = cat
                        break
                if service_type:
                    break

            lead = Lead.objects.create(
                platform=data['platform'],
                source_url=f"https://example.com/{data['platform']}/{content_hash[:8]}",
                source_content=data['content'],
                source_author=data.get('author', ''),
                source_posted_at=discovered,
                detected_location=data.get('location', ''),
                detected_zip=data.get('zip', ''),
                detected_service_type=service_type,
                matched_keywords=[],
                urgency_score={'hot': 90, 'warm': 65, 'new': 40, 'stale': 10}.get(data['urgency'], 50),
                urgency_level=data['urgency'],
                ai_summary=data.get('ai_summary', ''),
                ai_suggested_response=data.get('ai_response', ''),
                content_hash=content_hash,
            )
            # Fix discovered_at (auto_now_add)
            Lead.objects.filter(id=lead.id).update(discovered_at=discovered)

            # Assign to all profiles
            for profile in profiles:
                LeadAssignment.objects.get_or_create(
                    lead=lead,
                    business=profile,
                    defaults={'status': 'new'},
                )

            created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Created {created_count} demo leads assigned to {profiles.count()} business(es)'
        ))
