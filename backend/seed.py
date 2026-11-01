"""
Seed the database with initial catalog and sample business data.

Run after installing dependencies and filling in backend/.env:

    python seed.py

Safe to re-run: it skips records that already exist.

This seed script does NOT create demo Supabase Auth accounts.
Users should create their own accounts through the normal signup flow.
"""

from app.database import SessionLocal
from app import models

db = SessionLocal()


def get_or_create(model, defaults=None, **kwargs):
    instance = db.query(model).filter_by(**kwargs).first()

    if instance:
        return instance, False

    params = {**kwargs, **(defaults or {})}
    instance = model(**params)
    db.add(instance)
    db.flush()

    return instance, True


def main():

    # ---------------------------------------------------------------------
    # Catalog
    # ---------------------------------------------------------------------

    cat_names = [
        "Groceries",
        "Beverages",
        "Stationery",
        "Cosmetics",
        "Household",
    ]

    categories = {}

    for name in cat_names:
        cat, _ = get_or_create(
            models.Category,
            name=name,
        )
        categories[name] = cat

    brand_names = [
        "Generic",
        "Everest",
        "Himalaya",
        "Wai Wai",
        "CG",
    ]

    brands = {}

    for name in brand_names:
        brand, _ = get_or_create(
            models.Brand,
            name=name,
        )
        brands[name] = brand

    products = [
        dict(
            sku="GRC-001",
            name="Basmati Rice 5kg",
            category="Groceries",
            brand="Generic",
            cost_price=650,
            unit_price=780,
            stock_quantity=40,
            reorder_level=10,
        ),
        dict(
            sku="GRC-002",
            name="Cooking Oil 1L",
            category="Groceries",
            brand="Generic",
            cost_price=280,
            unit_price=330,
            stock_quantity=30,
            reorder_level=8,
        ),
        dict(
            sku="GRC-003",
            name="Wai Wai Noodles (pack of 6)",
            category="Groceries",
            brand="Wai Wai",
            cost_price=120,
            unit_price=150,
            stock_quantity=60,
            reorder_level=15,
        ),
        dict(
            sku="BEV-001",
            name="Mineral Water 1L",
            category="Beverages",
            brand="Generic",
            cost_price=20,
            unit_price=30,
            stock_quantity=100,
            reorder_level=20,
        ),
        dict(
            sku="BEV-002",
            name="Everest Tea 250g",
            category="Beverages",
            brand="Everest",
            cost_price=180,
            unit_price=220,
            stock_quantity=25,
            reorder_level=8,
        ),
        dict(
            sku="STA-001",
            name="A4 Notebook",
            category="Stationery",
            brand="Generic",
            cost_price=40,
            unit_price=60,
            stock_quantity=80,
            reorder_level=15,
        ),
        dict(
            sku="STA-002",
            name="Ballpoint Pen (box of 10)",
            category="Stationery",
            brand="Generic",
            cost_price=90,
            unit_price=130,
            stock_quantity=35,
            reorder_level=10,
        ),
        dict(
            sku="COS-001",
            name="Himalaya Face Wash 100ml",
            category="Cosmetics",
            brand="Himalaya",
            cost_price=210,
            unit_price=260,
            stock_quantity=18,
            reorder_level=6,
        ),
        dict(
            sku="HOU-001",
            name="CG Detergent Powder 1kg",
            category="Household",
            brand="CG",
            cost_price=150,
            unit_price=185,
            stock_quantity=6,
            reorder_level=10,
        ),
        dict(
            sku="HOU-002",
            name="Dish Soap 500ml",
            category="Household",
            brand="Generic",
            cost_price=95,
            unit_price=120,
            stock_quantity=4,
            reorder_level=10,
        ),
    ]

    for product in products:
        get_or_create(
            models.Product,
            sku=product["sku"],
            defaults=dict(
                name=product["name"],
                category_id=categories[product["category"]].id,
                brand_id=brands[product["brand"]].id,
                cost_price=product["cost_price"],
                unit_price=product["unit_price"],
                stock_quantity=product["stock_quantity"],
                reorder_level=product["reorder_level"],
            ),
        )

    # ---------------------------------------------------------------------
    # Suppliers
    # ---------------------------------------------------------------------

    get_or_create(
        models.Supplier,
        name="Kathmandu Wholesale Traders",
        defaults=dict(
            contact_person="Ram Shrestha",
            phone="9801000000",
            email="sales@kwt.example",
            address="Kalimati, Kathmandu",
        ),
    )

    get_or_create(
        models.Supplier,
        name="Everest FMCG Distributors",
        defaults=dict(
            contact_person="Sita Gurung",
            phone="9802000000",
            email="orders@efd.example",
            address="Balaju, Kathmandu",
        ),
    )

    # ---------------------------------------------------------------------
    # Customers
    # ---------------------------------------------------------------------

    get_or_create(
        models.Customer,
        name="Walk-in / Cash Customer",
        defaults=dict(
            phone="",
            email="",
            credit_limit=0,
        ),
    )

    get_or_create(
        models.Customer,
        name="Gita Karki",
        defaults=dict(
            phone="9822222222",
            email="gita@example.com",
            address="Patan, Lalitpur",
            credit_limit=3000,
        ),
    )

    db.commit()

    print("Seed complete.")
    print("No demo authentication accounts were created.")


if __name__ == "__main__":
    try:
        main()
    finally:
        db.close()
