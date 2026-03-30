#!/usr/bin/env python3
"""
Create SalesSignalAI products in Stripe and generate Payment Links.
Run once to set up all products. Idempotent - skips existing products.
"""

import os
import stripe
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

if not stripe.api_key:
    print("ERROR: STRIPE_SECRET_KEY not found in .env")
    exit(1)

# Product definitions
PRODUCTS = [
    # A LA CARTE - Monthly Subscriptions
    {"name": "SSAI - Email Drip Campaign AI", "price": 19900, "interval": "month"},
    {"name": "SSAI - Email Drip Campaign Human+AI", "price": 39900, "interval": "month"},
    {"name": "SSAI - Video Email Drip AI", "price": 34900, "interval": "month"},
    {"name": "SSAI - Video Email Drip Human+AI", "price": 59900, "interval": "month"},
    {"name": "SSAI - Lead Access Dashboard", "price": 29900, "interval": "month"},
    {"name": "SSAI - Social Listings AI (3 platforms)", "price": 34900, "interval": "month"},
    {"name": "SSAI - Social Listings Human+AI (5+ platforms)", "price": 69900, "interval": "month"},
    {"name": "SSAI - Inbound Call Center AI", "price": 39900, "interval": "month"},
    {"name": "SSAI - Inbound Call Center Human", "price": 69900, "interval": "month"},
    {"name": "SSAI - Outbound Call Center AI", "price": 59900, "interval": "month"},
    {"name": "SSAI - Outbound Call Center Human", "price": 119900, "interval": "month"},
    {"name": "SSAI - Landing Page AI Monthly", "price": 9900, "interval": "month"},
    {"name": "SSAI - Landing Page Human Monthly", "price": 14900, "interval": "month"},
    {"name": "SSAI - Outbound Sales Team AI", "price": 399900, "interval": "month"},
    {"name": "SSAI - Outbound Sales Team Human", "price": 749900, "interval": "month"},
    {"name": "SSAI - SEO + AEO AI", "price": 39900, "interval": "month"},
    {"name": "SSAI - SEO + AEO Human+AI", "price": 79900, "interval": "month"},
    {"name": "SSAI - BYO Leads Standard Base", "price": 19900, "interval": "month"},
    {"name": "SSAI - BYO Leads Emergency Base", "price": 29900, "interval": "month"},
    
    # PACKAGES - Monthly Subscriptions
    {"name": "SSAI - Starter AI", "price": 59900, "interval": "month"},
    {"name": "SSAI - Starter Human+AI", "price": 99900, "interval": "month"},
    {"name": "SSAI - Growth AI", "price": 119900, "interval": "month"},
    {"name": "SSAI - Growth Human+AI", "price": 199900, "interval": "month"},
    {"name": "SSAI - Dominate AI", "price": 199900, "interval": "month"},
    {"name": "SSAI - Dominate Human+AI", "price": 349900, "interval": "month"},
    {"name": "SSAI - Closer AI", "price": 399900, "interval": "month"},
    {"name": "SSAI - Closer Human+AI", "price": 649900, "interval": "month"},
    {"name": "SSAI - Full Service AI+Human", "price": 799900, "interval": "month"},
    {"name": "SSAI - Full Service Human", "price": 1299900, "interval": "month"},
    
    # ONE-TIME
    {"name": "SSAI - Setup Fee", "price": 29900, "interval": None},
    {"name": "SSAI - Landing Page AI Setup", "price": 39900, "interval": None},
    {"name": "SSAI - Landing Page Human Setup", "price": 99900, "interval": None},
    
    # PER-UNIT (one-time, for manual invoicing)
    {"name": "SSAI - Human-Qualified Lead", "price": 12500, "interval": None},
    {"name": "SSAI - Appointment AI", "price": 9900, "interval": None},
    {"name": "SSAI - Appointment Human", "price": 17500, "interval": None},
    {"name": "SSAI - BYO Standard Appointment", "price": 9900, "interval": None},
    {"name": "SSAI - BYO Emergency Appointment", "price": 14900, "interval": None},
]


def find_product_by_name(name):
    """Search for existing product by name."""
    products = stripe.Product.list(limit=100, active=True)
    for product in products.auto_paging_iter():
        if product.name == name:
            return product
    return None


def find_price_for_product(product_id):
    """Get the default price for a product."""
    prices = stripe.Price.list(product=product_id, active=True, limit=1)
    if prices.data:
        return prices.data[0]
    return None


def create_product_and_price(name, price_cents, interval=None):
    """Create a product and price, or return existing."""
    
    # Check if product exists
    existing = find_product_by_name(name)
    if existing:
        price = find_price_for_product(existing.id)
        print(f"  ✓ EXISTS: {name}")
        return existing, price
    
    # Create product
    product = stripe.Product.create(
        name=name,
        metadata={"source": "salessignalai"}
    )
    
    # Create price
    price_data = {
        "product": product.id,
        "unit_amount": price_cents,
        "currency": "usd",
    }
    
    if interval:
        price_data["recurring"] = {"interval": interval}
    
    price = stripe.Price.create(**price_data)
    
    # Set as default price
    stripe.Product.modify(product.id, default_price=price.id)
    
    print(f"  + CREATED: {name}")
    return product, price


def create_payment_link(price_id, product_name):
    """Create a payment link for a price."""
    try:
        link = stripe.PaymentLink.create(
            line_items=[{"price": price_id, "quantity": 1}],
            after_completion={
                "type": "redirect",
                "redirect": {"url": "https://salessignalai.com/signup/"}
            }
        )
        return link.url
    except Exception as e:
        print(f"    Warning: Could not create payment link for {product_name}: {e}")
        return None


def main():
    print("\n" + "="*60)
    print("SalesSignalAI - Stripe Product Setup")
    print("="*60 + "\n")
    
    results = []
    
    print("Creating products...\n")
    
    for item in PRODUCTS:
        product, price = create_product_and_price(
            item["name"],
            item["price"],
            item.get("interval")
        )
        
        if price:
            link = create_payment_link(price.id, item["name"])
            
            # Format price for display
            amount = item["price"] / 100
            if item.get("interval"):
                price_str = f"${amount:,.0f}/mo"
            else:
                price_str = f"${amount:,.0f}"
            
            results.append({
                "name": item["name"],
                "price": price_str,
                "link": link or "N/A"
            })
    
    # Print results table
    print("\n" + "="*60)
    print("PAYMENT LINKS")
    print("="*60 + "\n")
    
    print("| Product | Price | Payment Link |")
    print("|---------|-------|--------------|")
    
    for r in results:
        print(f"| {r['name']} | {r['price']} | {r['link']} |")
    
    # Save to file
    output_path = "stripe_payment_links.md"
    with open(output_path, "w") as f:
        f.write("# SalesSignalAI Payment Links\n\n")
        f.write("Generated payment links for texting to customers.\n\n")
        f.write("| Product | Price | Payment Link |\n")
        f.write("|---------|-------|--------------|\n")
        for r in results:
            f.write(f"| {r['name']} | {r['price']} | {r['link']} |\n")
    
    print(f"\n✓ Saved to {output_path}")
    print(f"\nTotal products: {len(results)}")
    print("\nDone! Copy the payment links above and text them to customers.\n")


if __name__ == "__main__":
    main()
