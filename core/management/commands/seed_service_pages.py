"""
Seed TradeCategory and ServiceArea data for the service landing page system.

Usage:
    python manage.py seed_service_pages
    python manage.py seed_service_pages --trades-only
    python manage.py seed_service_pages --areas-only
"""
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from core.models import TradeCategory, ServiceArea


TRADES = [
    # Home Services
    {
        'name': 'Plumber',
        'category_type': 'home_service',
        'icon': 'bi-droplet-fill',
        'emergency_keywords': 'emergency plumber, 24 hour plumber, plumber near me, burst pipe repair, emergency pipe repair',
        'service_keywords': 'drain cleaning, water heater repair, toilet repair, pipe leak, sewer line, faucet replacement, garbage disposal, water line repair, gas line service, bathroom plumbing, kitchen plumbing, water pressure',
        'pain_points': 'burst pipe flooding basement, no hot water, clogged drain backing up, toilet overflowing, sewer smell, leaking faucet, frozen pipes',
    },
    {
        'name': 'Electrician',
        'category_type': 'home_service',
        'icon': 'bi-lightning-fill',
        'emergency_keywords': 'emergency electrician, 24 hour electrician, electrician near me, power outage repair',
        'service_keywords': 'electrical repair, panel upgrade, outlet installation, wiring, lighting, ceiling fan installation, generator installation, EV charger, circuit breaker, smoke detector, rewiring, landscape lighting',
        'pain_points': 'power outage, tripping breakers, sparking outlet, flickering lights, no power to room, burning smell from panel',
    },
    {
        'name': 'HVAC Technician',
        'category_type': 'home_service',
        'icon': 'bi-thermometer-half',
        'emergency_keywords': 'AC repair, heating repair, emergency HVAC, 24 hour AC repair, HVAC near me',
        'service_keywords': 'AC repair, heating repair, furnace installation, air conditioning, ductwork, HVAC maintenance, heat pump, boiler repair, thermostat installation, mini split, central air, duct cleaning',
        'pain_points': 'no heat in winter, AC not cooling, furnace making noise, high energy bills, uneven temperatures, bad air quality',
    },
    {
        'name': 'General Contractor',
        'category_type': 'home_service',
        'icon': 'bi-hammer',
        'emergency_keywords': 'general contractor near me, home renovation, remodeling contractor',
        'service_keywords': 'home renovation, kitchen remodel, bathroom remodel, basement finishing, addition, deck building, framing, drywall, trim work, structural repair',
        'pain_points': 'outdated kitchen, small bathroom, unfinished basement, need more space, water damage repair',
    },
    {
        'name': 'Roofer',
        'category_type': 'home_service',
        'icon': 'bi-house-fill',
        'emergency_keywords': 'emergency roof repair, roof leak repair, 24 hour roofer, roofer near me',
        'service_keywords': 'roof repair, roof replacement, shingle repair, flat roof, roof leak, gutter installation, skylight, chimney flashing, roof inspection, storm damage repair',
        'pain_points': 'roof leaking, missing shingles after storm, water stains on ceiling, old roof needs replacement, ice dam',
    },
    {
        'name': 'Painter',
        'category_type': 'home_service',
        'icon': 'bi-palette-fill',
        'emergency_keywords': 'house painter near me, painting contractor, interior painter',
        'service_keywords': 'interior painting, exterior painting, commercial painting, cabinet painting, staining, wallpaper removal, drywall repair, pressure washing, deck staining, color consultation',
        'pain_points': 'peeling paint, outdated colors, moving into new home, selling house needs fresh paint',
    },
    {
        'name': 'Landscaper',
        'category_type': 'home_service',
        'icon': 'bi-tree-fill',
        'emergency_keywords': 'landscaper near me, landscaping service, lawn care near me',
        'service_keywords': 'landscaping, lawn care, tree trimming, yard cleanup, garden design, sprinkler installation, mulching, sod installation, retaining wall, patio design, snow removal',
        'pain_points': 'overgrown yard, dead lawn, need curb appeal, drainage problems, new construction needs landscaping',
    },
    {
        'name': 'Pest Control',
        'category_type': 'home_service',
        'icon': 'bi-bug-fill',
        'emergency_keywords': 'exterminator near me, pest control near me, emergency pest removal',
        'service_keywords': 'pest removal, termite treatment, rodent control, bed bug treatment, ant removal, cockroach control, mosquito treatment, wildlife removal, inspection, prevention',
        'pain_points': 'mice in walls, cockroaches in kitchen, bed bugs, termite damage, ants everywhere, wasps nest',
    },
    {
        'name': 'Locksmith',
        'category_type': 'emergency',
        'icon': 'bi-key-fill',
        'emergency_keywords': 'emergency locksmith, 24 hour locksmith, locksmith near me, locked out',
        'service_keywords': 'lockout service, lock change, key duplication, smart lock installation, rekey, deadbolt, safe opening, car lockout, commercial locks, access control',
        'pain_points': 'locked out of house, lost keys, broken lock, need locks changed after move, security upgrade',
    },
    {
        'name': 'Handyman',
        'category_type': 'home_service',
        'icon': 'bi-tools',
        'emergency_keywords': 'handyman near me, handyman service, home repair service',
        'service_keywords': 'home repair, furniture assembly, drywall repair, minor plumbing, minor electrical, door installation, shelf mounting, caulking, weather stripping, tile repair',
        'pain_points': 'small repairs piling up, cant do it myself, need things hung/mounted, minor fixes before selling',
    },
    {
        'name': 'Mover',
        'category_type': 'home_service',
        'icon': 'bi-truck',
        'emergency_keywords': 'movers near me, moving company, local movers, last minute movers',
        'service_keywords': 'local moving, long distance moving, packing services, storage, piano moving, office moving, furniture moving, junk removal, loading/unloading',
        'pain_points': 'moving to new home, downsizing, relocating for work, need heavy items moved',
    },
    {
        'name': 'Cleaning Service',
        'category_type': 'home_service',
        'icon': 'bi-stars',
        'emergency_keywords': 'house cleaning near me, maid service, cleaning service near me',
        'service_keywords': 'house cleaning, deep cleaning, move-in cleaning, move-out cleaning, regular maid service, post-construction cleaning, spring cleaning, carpet cleaning, window cleaning',
        'pain_points': 'no time to clean, moving out need deposit back, post-renovation mess, hosting guests',
    },
    {
        'name': 'Fencing',
        'category_type': 'home_service',
        'icon': 'bi-border-all',
        'emergency_keywords': 'fence installer near me, fence company, fencing contractor',
        'service_keywords': 'fence installation, fence repair, wood fence, vinyl fence, chain link fence, aluminum fence, privacy fence, gate installation, post replacement',
        'pain_points': 'need privacy, dog keeps escaping, old fence falling down, property line dispute, pool code requires fence',
    },
    {
        'name': 'Flooring',
        'category_type': 'home_service',
        'icon': 'bi-grid-3x3',
        'emergency_keywords': 'flooring installer near me, floor installation, flooring company',
        'service_keywords': 'hardwood flooring, tile installation, carpet installation, laminate flooring, vinyl plank, floor refinishing, subfloor repair, heated floors, stair treads',
        'pain_points': 'old carpet needs replacing, water damaged floor, scratched hardwood, updating for home sale',
    },
    {
        'name': 'Garage Door',
        'category_type': 'home_service',
        'icon': 'bi-door-closed-fill',
        'emergency_keywords': 'garage door repair near me, emergency garage door, garage door company',
        'service_keywords': 'garage door repair, garage door installation, garage door opener, spring replacement, cable repair, panel replacement, insulation, smart opener',
        'pain_points': 'garage door wont open, broken spring, loud grinding noise, off track, remote not working',
    },
    {
        'name': 'Tree Service',
        'category_type': 'home_service',
        'icon': 'bi-tree',
        'emergency_keywords': 'tree removal near me, emergency tree service, tree company',
        'service_keywords': 'tree removal, tree trimming, stump grinding, emergency tree removal, tree pruning, land clearing, tree health assessment, crane removal',
        'pain_points': 'dead tree near house, storm damage, overgrown branches, roots damaging foundation, tree blocking view',
    },
    {
        'name': 'Power Washing',
        'category_type': 'home_service',
        'icon': 'bi-water',
        'emergency_keywords': 'pressure washing near me, power washing service',
        'service_keywords': 'pressure washing, deck cleaning, driveway cleaning, house washing, concrete cleaning, fence cleaning, patio cleaning, graffiti removal',
        'pain_points': 'dirty siding, green/mossy deck, stained driveway, preparing to paint, selling house',
    },
    {
        'name': 'Paving',
        'category_type': 'home_service',
        'icon': 'bi-signpost-split-fill',
        'emergency_keywords': 'paving company near me, asphalt paving, driveway paving',
        'service_keywords': 'driveway paving, asphalt paving, concrete paving, patio installation, walkway, sealcoating, pothole repair, parking lot paving',
        'pain_points': 'cracked driveway, potholes, need new walkway, expanding parking, curb appeal',
    },
    {
        'name': 'Mason',
        'category_type': 'home_service',
        'icon': 'bi-bricks',
        'emergency_keywords': 'mason near me, masonry contractor, brick repair near me',
        'service_keywords': 'brick repair, stone work, retaining wall, chimney repair, stucco, concrete work, pointing, foundation repair, outdoor fireplace, stone veneer',
        'pain_points': 'crumbling mortar, leaning chimney, cracked foundation, water seeping through brick, deteriorating stoop',
    },
    {
        'name': 'Pool Service',
        'category_type': 'home_service',
        'icon': 'bi-moisture',
        'emergency_keywords': 'pool service near me, pool repair, pool company',
        'service_keywords': 'pool cleaning, pool repair, pool installation, pool opening, pool closing, liner replacement, pump repair, filter service, heater repair, salt system',
        'pain_points': 'green pool, pump not working, liner tearing, need winterization, opening for season',
    },
    {
        'name': 'Gutter',
        'category_type': 'home_service',
        'icon': 'bi-funnel-fill',
        'emergency_keywords': 'gutter installer near me, gutter company, gutter repair',
        'service_keywords': 'gutter installation, gutter cleaning, gutter repair, gutter guards, downspout, seamless gutters, leaf protection, ice dam prevention',
        'pain_points': 'overflowing gutters, clogged gutters, water pooling at foundation, sagging gutters, ice dams',
    },
    {
        'name': 'Window',
        'category_type': 'home_service',
        'icon': 'bi-window',
        'emergency_keywords': 'window installer near me, window replacement, window company',
        'service_keywords': 'window installation, window replacement, window repair, glass repair, storm windows, energy efficient windows, bay windows, sliding doors',
        'pain_points': 'drafty windows, high energy bills, foggy glass, broken window, old single-pane',
    },
    {
        'name': 'Siding',
        'category_type': 'home_service',
        'icon': 'bi-building',
        'emergency_keywords': 'siding contractor near me, siding installation, siding company',
        'service_keywords': 'siding installation, siding repair, vinyl siding, fiber cement siding, wood siding, insulated siding, trim work, soffit and fascia',
        'pain_points': 'damaged siding, peeling paint, rotting wood, curb appeal, energy efficiency',
    },
    # Commercial Services
    {
        'name': 'Commercial Cleaning',
        'category_type': 'commercial_service',
        'icon': 'bi-building-fill-check',
        'emergency_keywords': 'commercial cleaning near me, office cleaning, janitorial service',
        'service_keywords': 'office cleaning, janitorial services, floor waxing, carpet cleaning, post-construction cleaning, restaurant cleaning, medical facility cleaning, window cleaning',
        'pain_points': 'dirty office, health code compliance, tenant turnover cleaning, special event cleanup',
    },
    {
        'name': 'Commercial HVAC',
        'category_type': 'commercial_service',
        'icon': 'bi-fan',
        'emergency_keywords': 'commercial HVAC near me, commercial AC repair, rooftop unit repair',
        'service_keywords': 'commercial AC, commercial heating, rooftop units, commercial refrigeration, VRF systems, chiller repair, boiler service, building automation',
        'pain_points': 'AC out in office, restaurant refrigeration down, rooftop unit failure, tenant complaints',
    },
    {
        'name': 'Fire Protection',
        'category_type': 'commercial_service',
        'icon': 'bi-fire',
        'emergency_keywords': 'fire alarm company near me, fire sprinkler service, fire protection',
        'service_keywords': 'fire alarm, fire sprinkler, fire suppression, fire extinguisher, fire safety inspection, fire escape, emergency lighting, backflow prevention',
        'pain_points': 'fire code violation, inspection due, new building needs system, alarm keeps going off',
    },
    {
        'name': 'Security System',
        'category_type': 'commercial_service',
        'icon': 'bi-shield-lock-fill',
        'emergency_keywords': 'security camera installation near me, alarm system, business security',
        'service_keywords': 'security camera, alarm system, access control, video surveillance, intercom, doorbell camera, motion detection, monitoring service',
        'pain_points': 'break-in, need surveillance, employee theft, after-hours security, insurance requires alarm',
    },
    # Professional Services
    {
        'name': 'Insurance Agent',
        'category_type': 'professional',
        'icon': 'bi-shield-check',
        'emergency_keywords': 'insurance agent near me, insurance broker, business insurance',
        'service_keywords': 'business insurance, home insurance, auto insurance, liability insurance, workers comp, commercial insurance, umbrella policy, bonds',
        'pain_points': 'new business needs insurance, rates too high, claim denied, need workers comp, lender requires insurance',
    },
    {
        'name': 'Mortgage Broker',
        'category_type': 'professional',
        'icon': 'bi-bank2',
        'emergency_keywords': 'mortgage broker near me, home loan, mortgage lender',
        'service_keywords': 'mortgage, home loan, refinance, FHA loan, VA loan, first time homebuyer, jumbo loan, preapproval, rate lock, home equity',
        'pain_points': 'buying first home, rates dropped want to refinance, need preapproval fast, bad credit mortgage',
    },
    {
        'name': 'Real Estate Agent',
        'category_type': 'professional',
        'icon': 'bi-house-heart-fill',
        'emergency_keywords': 'real estate agent near me, realtor, listing agent',
        'service_keywords': 'buy home, sell home, listing agent, buyer agent, property valuation, open house, negotiation, closing, investment property',
        'pain_points': 'selling home, buying first home, relocating, investment property, foreclosure help',
    },
    {
        'name': 'Lawyer',
        'category_type': 'professional',
        'icon': 'bi-briefcase-fill',
        'emergency_keywords': 'lawyer near me, attorney, legal services',
        'service_keywords': 'business lawyer, real estate lawyer, personal injury, contract attorney, estate planning, family law, immigration, tenant rights, corporate',
        'pain_points': 'starting business needs LLC, buying property, accident injury, contract dispute, divorce',
    },
    {
        'name': 'Accountant',
        'category_type': 'professional',
        'icon': 'bi-calculator-fill',
        'emergency_keywords': 'accountant near me, CPA, tax preparation',
        'service_keywords': 'tax preparation, bookkeeping, business accounting, tax planning, payroll, audit, financial statement, QuickBooks, business formation',
        'pain_points': 'tax season, behind on bookkeeping, IRS letter, starting business, need payroll setup',
    },
    {
        'name': 'Dentist',
        'category_type': 'professional',
        'icon': 'bi-emoji-smile-fill',
        'emergency_keywords': 'dentist near me, emergency dentist, dental clinic',
        'service_keywords': 'teeth cleaning, dental implants, emergency dentist, cosmetic dentistry, orthodontist, root canal, crowns, veneers, whitening, pediatric dentist',
        'pain_points': 'toothache, broken tooth, havent been in years, need cleaning, wisdom teeth',
    },
    {
        'name': 'Chiropractor',
        'category_type': 'professional',
        'icon': 'bi-person-arms-up',
        'emergency_keywords': 'chiropractor near me, back pain treatment, spinal adjustment',
        'service_keywords': 'spinal adjustment, back pain, neck pain, sports injury, sciatica, headache relief, posture correction, rehabilitation',
        'pain_points': 'back pain, neck stiffness, car accident injury, sports injury, chronic headaches',
    },
    {
        'name': 'Veterinarian',
        'category_type': 'professional',
        'icon': 'bi-heart-pulse-fill',
        'emergency_keywords': 'vet near me, animal hospital, emergency vet, pet doctor',
        'service_keywords': 'pet care, vaccinations, spay/neuter, dental cleaning, surgery, wellness exam, microchip, senior pet care, exotic animals',
        'pain_points': 'sick pet, new puppy needs shots, emergency injury, aging pet, annual checkup overdue',
    },
    {
        'name': 'Auto Mechanic',
        'category_type': 'professional',
        'icon': 'bi-wrench-adjustable',
        'emergency_keywords': 'auto mechanic near me, car repair, auto shop',
        'service_keywords': 'oil change, brake repair, transmission, check engine light, tire service, AC repair, battery replacement, exhaust, suspension, state inspection',
        'pain_points': 'check engine light on, strange noise, brakes squealing, car wont start, overheating',
    },
    {
        'name': 'Tow Truck',
        'category_type': 'emergency',
        'icon': 'bi-truck-front-fill',
        'emergency_keywords': 'tow truck near me, towing service, 24 hour towing, roadside assistance',
        'service_keywords': 'towing service, roadside assistance, flatbed tow, emergency towing, jump start, tire change, fuel delivery, winch out',
        'pain_points': 'car broke down, flat tire, dead battery, locked keys in car, accident needs tow',
    },
]


AREAS = [
    # NYC Boroughs
    {'name': 'Manhattan', 'slug': 'manhattan-ny', 'area_type': 'borough', 'county': 'New York County', 'lat': 40.7831, 'lng': -73.9712, 'pop': 1694251},
    {'name': 'Brooklyn', 'slug': 'brooklyn-ny', 'area_type': 'borough', 'county': 'Kings County', 'lat': 40.6782, 'lng': -73.9442, 'pop': 2736074},
    {'name': 'Queens', 'slug': 'queens-ny', 'area_type': 'borough', 'county': 'Queens County', 'lat': 40.7282, 'lng': -73.7949, 'pop': 2405464},
    {'name': 'Bronx', 'slug': 'bronx-ny', 'area_type': 'borough', 'county': 'Bronx County', 'lat': 40.8448, 'lng': -73.8648, 'pop': 1472654},
    {'name': 'Staten Island', 'slug': 'staten-island-ny', 'area_type': 'borough', 'county': 'Richmond County', 'lat': 40.5795, 'lng': -74.1502, 'pop': 495747},

    # Nassau County
    {'name': 'Nassau County', 'slug': 'nassau-county-ny', 'area_type': 'county', 'county': 'Nassau County', 'lat': 40.7289, 'lng': -73.5594, 'pop': 1395774},
    {'name': 'Hempstead', 'slug': 'hempstead-ny', 'area_type': 'town', 'county': 'Nassau County', 'lat': 40.7062, 'lng': -73.6187, 'pop': 53891},
    {'name': 'Freeport', 'slug': 'freeport-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.6576, 'lng': -73.5832, 'pop': 43783},
    {'name': 'Rockville Centre', 'slug': 'rockville-centre-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.6587, 'lng': -73.6410, 'pop': 24023},
    {'name': 'Valley Stream', 'slug': 'valley-stream-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.6643, 'lng': -73.7084, 'pop': 37511},
    {'name': 'Lynbrook', 'slug': 'lynbrook-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.6548, 'lng': -73.6718, 'pop': 19911},
    {'name': 'Garden City', 'slug': 'garden-city-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.7268, 'lng': -73.6343, 'pop': 22371},
    {'name': 'Massapequa', 'slug': 'massapequa-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.6812, 'lng': -73.4724, 'pop': 21685},
    {'name': 'Farmingdale', 'slug': 'farmingdale-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.7326, 'lng': -73.4454, 'pop': 8869},
    {'name': 'Mineola', 'slug': 'mineola-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.7490, 'lng': -73.6407, 'pop': 19267},
    {'name': 'Great Neck', 'slug': 'great-neck-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.8007, 'lng': -73.7285, 'pop': 10492},
    {'name': 'Port Washington', 'slug': 'port-washington-ny', 'area_type': 'village', 'county': 'Nassau County', 'lat': 40.8257, 'lng': -73.6982, 'pop': 16232},
    {'name': 'Glen Cove', 'slug': 'glen-cove-ny', 'area_type': 'city', 'county': 'Nassau County', 'lat': 40.8623, 'lng': -73.6332, 'pop': 28158},
    {'name': 'Long Beach', 'slug': 'long-beach-ny', 'area_type': 'city', 'county': 'Nassau County', 'lat': 40.5884, 'lng': -73.6579, 'pop': 33275},
    {'name': 'North Hempstead', 'slug': 'north-hempstead-ny', 'area_type': 'town', 'county': 'Nassau County', 'lat': 40.7901, 'lng': -73.6876},
    {'name': 'Oyster Bay', 'slug': 'oyster-bay-ny', 'area_type': 'town', 'county': 'Nassau County', 'lat': 40.8654, 'lng': -73.5318},

    # Suffolk County
    {'name': 'Suffolk County', 'slug': 'suffolk-county-ny', 'area_type': 'county', 'county': 'Suffolk County', 'lat': 40.9429, 'lng': -72.6829, 'pop': 1525920},
    {'name': 'Babylon', 'slug': 'babylon-ny', 'area_type': 'town', 'county': 'Suffolk County', 'lat': 40.6957, 'lng': -73.3259, 'pop': 213234},
    {'name': 'Huntington', 'slug': 'huntington-ny', 'area_type': 'town', 'county': 'Suffolk County', 'lat': 40.8682, 'lng': -73.4257, 'pop': 203264},
    {'name': 'Islip', 'slug': 'islip-ny', 'area_type': 'town', 'county': 'Suffolk County', 'lat': 40.7301, 'lng': -73.2104, 'pop': 335543},
    {'name': 'Smithtown', 'slug': 'smithtown-ny', 'area_type': 'town', 'county': 'Suffolk County', 'lat': 40.8559, 'lng': -73.2006, 'pop': 117801},
    {'name': 'Brookhaven', 'slug': 'brookhaven-ny', 'area_type': 'town', 'county': 'Suffolk County', 'lat': 40.8382, 'lng': -72.9159, 'pop': 486040},
    {'name': 'Patchogue', 'slug': 'patchogue-ny', 'area_type': 'village', 'county': 'Suffolk County', 'lat': 40.7654, 'lng': -73.0154, 'pop': 12556},
    {'name': 'Port Jefferson', 'slug': 'port-jefferson-ny', 'area_type': 'village', 'county': 'Suffolk County', 'lat': 40.9465, 'lng': -73.0691, 'pop': 8143},
    {'name': 'Bay Shore', 'slug': 'bay-shore-ny', 'area_type': 'village', 'county': 'Suffolk County', 'lat': 40.7254, 'lng': -73.2451, 'pop': 31849},
    {'name': 'Lindenhurst', 'slug': 'lindenhurst-ny', 'area_type': 'village', 'county': 'Suffolk County', 'lat': 40.6868, 'lng': -73.3734, 'pop': 27819},
    {'name': 'Amityville', 'slug': 'amityville-ny', 'area_type': 'village', 'county': 'Suffolk County', 'lat': 40.6790, 'lng': -73.4168, 'pop': 9523},

    # Westchester
    {'name': 'Westchester County', 'slug': 'westchester-county-ny', 'area_type': 'county', 'county': 'Westchester County', 'lat': 41.1220, 'lng': -73.7949, 'pop': 1004457},
    {'name': 'White Plains', 'slug': 'white-plains-ny', 'area_type': 'city', 'county': 'Westchester County', 'lat': 41.0340, 'lng': -73.7629, 'pop': 58109},
    {'name': 'Yonkers', 'slug': 'yonkers-ny', 'area_type': 'city', 'county': 'Westchester County', 'lat': 40.9312, 'lng': -73.8987, 'pop': 211569},
    {'name': 'New Rochelle', 'slug': 'new-rochelle-ny', 'area_type': 'city', 'county': 'Westchester County', 'lat': 40.9115, 'lng': -73.7824, 'pop': 79726},
    {'name': 'Mount Vernon', 'slug': 'mount-vernon-ny', 'area_type': 'city', 'county': 'Westchester County', 'lat': 40.9126, 'lng': -73.8371, 'pop': 73893},
    {'name': 'Scarsdale', 'slug': 'scarsdale-ny', 'area_type': 'village', 'county': 'Westchester County', 'lat': 41.0051, 'lng': -73.7846, 'pop': 17890},
    {'name': 'Mamaroneck', 'slug': 'mamaroneck-ny', 'area_type': 'village', 'county': 'Westchester County', 'lat': 40.9490, 'lng': -73.7335, 'pop': 19426},
    {'name': 'Tarrytown', 'slug': 'tarrytown-ny', 'area_type': 'village', 'county': 'Westchester County', 'lat': 41.0762, 'lng': -73.8587, 'pop': 11277},

    # Long Island aggregate
    {'name': 'Long Island', 'slug': 'long-island-ny', 'area_type': 'county', 'county': '', 'lat': 40.7891, 'lng': -73.1350, 'pop': 2921694},

    # Queens neighborhoods
    {'name': 'Astoria', 'slug': 'astoria-ny', 'area_type': 'neighborhood', 'county': 'Queens County', 'lat': 40.7723, 'lng': -73.9301, 'pop': 78793},
    {'name': 'Flushing', 'slug': 'flushing-ny', 'area_type': 'neighborhood', 'county': 'Queens County', 'lat': 40.7654, 'lng': -73.8318, 'pop': 72008},
    {'name': 'Jamaica', 'slug': 'jamaica-ny', 'area_type': 'neighborhood', 'county': 'Queens County', 'lat': 40.7025, 'lng': -73.7888, 'pop': 68510},
    {'name': 'Bayside', 'slug': 'bayside-ny', 'area_type': 'neighborhood', 'county': 'Queens County', 'lat': 40.7724, 'lng': -73.7693, 'pop': 47846},
    {'name': 'Forest Hills', 'slug': 'forest-hills-ny', 'area_type': 'neighborhood', 'county': 'Queens County', 'lat': 40.7180, 'lng': -73.8448, 'pop': 67611},

    # Brooklyn neighborhoods
    {'name': 'Williamsburg', 'slug': 'williamsburg-ny', 'area_type': 'neighborhood', 'county': 'Kings County', 'lat': 40.7081, 'lng': -73.9571, 'pop': 78992},
    {'name': 'Park Slope', 'slug': 'park-slope-ny', 'area_type': 'neighborhood', 'county': 'Kings County', 'lat': 40.6710, 'lng': -73.9777, 'pop': 67724},
    {'name': 'Bushwick', 'slug': 'bushwick-ny', 'area_type': 'neighborhood', 'county': 'Kings County', 'lat': 40.6945, 'lng': -73.9182, 'pop': 112434},
    {'name': 'Bay Ridge', 'slug': 'bay-ridge-ny', 'area_type': 'neighborhood', 'county': 'Kings County', 'lat': 40.6345, 'lng': -74.0283, 'pop': 80622},
    {'name': 'Flatbush', 'slug': 'flatbush-ny', 'area_type': 'neighborhood', 'county': 'Kings County', 'lat': 40.6410, 'lng': -73.9570, 'pop': 110875},

    # Manhattan neighborhoods
    {'name': 'Harlem', 'slug': 'harlem-ny', 'area_type': 'neighborhood', 'county': 'New York County', 'lat': 40.8116, 'lng': -73.9465, 'pop': 116345},
    {'name': 'Upper East Side', 'slug': 'upper-east-side-ny', 'area_type': 'neighborhood', 'county': 'New York County', 'lat': 40.7736, 'lng': -73.9566, 'pop': 217265},
    {'name': 'Upper West Side', 'slug': 'upper-west-side-ny', 'area_type': 'neighborhood', 'county': 'New York County', 'lat': 40.7870, 'lng': -73.9754, 'pop': 218000},
    {'name': 'East Village', 'slug': 'east-village-ny', 'area_type': 'neighborhood', 'county': 'New York County', 'lat': 40.7265, 'lng': -73.9815, 'pop': 64000},
    {'name': 'Chelsea', 'slug': 'chelsea-ny', 'area_type': 'neighborhood', 'county': 'New York County', 'lat': 40.7465, 'lng': -74.0014, 'pop': 47000},
    {'name': 'Midtown', 'slug': 'midtown-ny', 'area_type': 'neighborhood', 'county': 'New York County', 'lat': 40.7549, 'lng': -73.9840},

    # Bronx neighborhoods
    {'name': 'Fordham', 'slug': 'fordham-ny', 'area_type': 'neighborhood', 'county': 'Bronx County', 'lat': 40.8615, 'lng': -73.8901},
    {'name': 'Riverdale', 'slug': 'riverdale-ny', 'area_type': 'neighborhood', 'county': 'Bronx County', 'lat': 40.9005, 'lng': -73.9124, 'pop': 48028},
    {'name': 'Pelham Bay', 'slug': 'pelham-bay-ny', 'area_type': 'neighborhood', 'county': 'Bronx County', 'lat': 40.8526, 'lng': -73.8387},
    {'name': 'Throggs Neck', 'slug': 'throggs-neck-ny', 'area_type': 'neighborhood', 'county': 'Bronx County', 'lat': 40.8187, 'lng': -73.8178},
]

# Neighborhood parent mappings (name -> parent borough name)
NEIGHBORHOOD_PARENTS = {
    'Astoria': 'Queens', 'Flushing': 'Queens', 'Jamaica': 'Queens',
    'Bayside': 'Queens', 'Forest Hills': 'Queens',
    'Williamsburg': 'Brooklyn', 'Park Slope': 'Brooklyn', 'Bushwick': 'Brooklyn',
    'Bay Ridge': 'Brooklyn', 'Flatbush': 'Brooklyn',
    'Harlem': 'Manhattan', 'Upper East Side': 'Manhattan', 'Upper West Side': 'Manhattan',
    'East Village': 'Manhattan', 'Chelsea': 'Manhattan', 'Midtown': 'Manhattan',
    'Fordham': 'Bronx', 'Riverdale': 'Bronx', 'Pelham Bay': 'Bronx', 'Throggs Neck': 'Bronx',
}

# Nassau/Suffolk towns -> parent county
COUNTY_PARENTS = {
    'Hempstead': 'Nassau County', 'North Hempstead': 'Nassau County', 'Oyster Bay': 'Nassau County',
    'Glen Cove': 'Nassau County', 'Long Beach': 'Nassau County', 'Freeport': 'Nassau County',
    'Rockville Centre': 'Nassau County', 'Valley Stream': 'Nassau County', 'Lynbrook': 'Nassau County',
    'Garden City': 'Nassau County', 'Massapequa': 'Nassau County', 'Farmingdale': 'Nassau County',
    'Mineola': 'Nassau County', 'Great Neck': 'Nassau County', 'Port Washington': 'Nassau County',
    'Babylon': 'Suffolk County', 'Huntington': 'Suffolk County', 'Islip': 'Suffolk County',
    'Smithtown': 'Suffolk County', 'Brookhaven': 'Suffolk County', 'Patchogue': 'Suffolk County',
    'Port Jefferson': 'Suffolk County', 'Bay Shore': 'Suffolk County', 'Lindenhurst': 'Suffolk County',
    'Amityville': 'Suffolk County',
    'White Plains': 'Westchester County', 'Yonkers': 'Westchester County',
    'New Rochelle': 'Westchester County', 'Mount Vernon': 'Westchester County',
    'Scarsdale': 'Westchester County', 'Mamaroneck': 'Westchester County', 'Tarrytown': 'Westchester County',
}


class Command(BaseCommand):
    help = 'Seed TradeCategory and ServiceArea data for service landing pages.'

    def add_arguments(self, parser):
        parser.add_argument('--trades-only', action='store_true', help='Only seed trades')
        parser.add_argument('--areas-only', action='store_true', help='Only seed areas')

    def handle(self, *args, **options):
        trades_only = options.get('trades_only')
        areas_only = options.get('areas_only')

        if not areas_only:
            self._seed_trades()
        if not trades_only:
            self._seed_areas()

        self.stdout.write(self.style.SUCCESS('Seed complete.'))

    def _seed_trades(self):
        created = 0
        for t in TRADES:
            obj, was_created = TradeCategory.objects.update_or_create(
                slug=slugify(t['name']),
                defaults={
                    'name': t['name'],
                    'category_type': t['category_type'],
                    'icon': t.get('icon', ''),
                    'emergency_keywords': t.get('emergency_keywords', ''),
                    'service_keywords': t.get('service_keywords', ''),
                    'pain_points': t.get('pain_points', ''),
                },
            )
            if was_created:
                created += 1

        self.stdout.write(f'  Trades: {created} created, {len(TRADES) - created} updated (total {len(TRADES)})')

    def _seed_areas(self):
        created = 0
        area_map = {}

        for a in AREAS:
            obj, was_created = ServiceArea.objects.update_or_create(
                slug=a['slug'],
                state='NY',
                defaults={
                    'name': a['name'],
                    'area_type': a['area_type'],
                    'county': a.get('county', ''),
                    'state_full': 'New York',
                    'latitude': a.get('lat'),
                    'longitude': a.get('lng'),
                    'population': a.get('pop'),
                },
            )
            area_map[a['name']] = obj
            if was_created:
                created += 1

        # Set parent relationships
        for name, parent_name in {**NEIGHBORHOOD_PARENTS, **COUNTY_PARENTS}.items():
            child = area_map.get(name)
            parent = area_map.get(parent_name)
            if child and parent and child.parent_area_id != parent.id:
                child.parent_area = parent
                child.save(update_fields=['parent_area'])

        # Set neighboring areas for boroughs
        boroughs = [area_map.get(b) for b in ['Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island'] if area_map.get(b)]
        for b in boroughs:
            b.neighboring_areas.set([x for x in boroughs if x.id != b.id])

        self.stdout.write(f'  Areas: {created} created, {len(AREAS) - created} updated (total {len(AREAS)})')
