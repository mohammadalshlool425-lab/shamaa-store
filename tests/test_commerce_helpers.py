import sqlite3

import pytest
from fastapi import HTTPException

from server.commerce import cart_quote, commission_rate, coupon_quote, product_image


def test_product_image_prefers_private_media_then_known_demo_then_placeholder():
    assert product_image({"media_id": "asset-123", "demo_image": "lamp"}) == "/media/asset-123/480.webp"
    assert product_image({"media_id": None, "demo_image": "lamp"}) == "/static/images/lamp.webp"
    assert product_image({"media_id": None, "demo_image": "unrecognized"}) == "/static/images/placeholder.svg"


def test_commission_rate_uses_parent_category_then_global_default():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE categories (id TEXT PRIMARY KEY, commission_bps INTEGER)")
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO categories VALUES ('parent', 1200)")
    connection.execute("INSERT INTO settings VALUES ('commission_bps', '500')")
    product = {
        "store_commission_bps": None,
        "category_commission_bps": None,
        "parent_id": "parent",
    }

    assert commission_rate(connection, product) == 1200
    connection.execute("UPDATE categories SET commission_bps=NULL WHERE id='parent'")
    assert commission_rate(connection, product) == 500
    connection.close()


def test_coupon_quote_rejects_malformed_missing_exhausted_and_zero_value_codes():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE coupons (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            active INTEGER NOT NULL,
            starts_at INTEGER,
            expires_at INTEGER,
            max_uses INTEGER,
            used_count INTEGER NOT NULL,
            store_id INTEGER NOT NULL,
            minimum_subtotal_fils INTEGER NOT NULL,
            max_uses_per_buyer INTEGER NOT NULL,
            discount_type TEXT NOT NULL,
            discount_value INTEGER NOT NULL,
            maximum_discount_fils INTEGER
        );
        CREATE TABLE coupon_redemptions (coupon_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL);
        """
    )
    quote = {
        "groups": [{"store_id": 7, "subtotal_fils": 1000}],
        "total_fils": 1200,
    }

    with pytest.raises(HTTPException) as malformed:
        coupon_quote(connection, 3, quote, "x")
    assert malformed.value.status_code == 422

    with pytest.raises(HTTPException) as missing:
        coupon_quote(connection, 3, quote, "MISSING")
    assert missing.value.status_code == 422

    connection.execute(
        "INSERT INTO coupons VALUES (1,'LIMIT10',1,NULL,NULL,10,0,7,0,1,'percent',1000,NULL)"
    )
    connection.execute("INSERT INTO coupon_redemptions VALUES (1,3)")
    with pytest.raises(HTTPException) as exhausted:
        coupon_quote(connection, 3, quote, "LIMIT10")
    assert exhausted.value.status_code == 409

    connection.execute(
        "INSERT INTO coupons VALUES (2,'ZERO10',1,NULL,NULL,NULL,0,7,0,2,'percent',0,NULL)"
    )
    with pytest.raises(HTTPException) as zero:
        coupon_quote(connection, 3, quote, "ZERO10")
    assert zero.value.status_code == 409
    connection.close()


def test_coupon_quote_caps_fixed_discount_to_store_subtotal_and_maximum():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE coupons (
            id INTEGER PRIMARY KEY, code TEXT, active INTEGER, starts_at INTEGER,
            expires_at INTEGER, max_uses INTEGER, used_count INTEGER, store_id INTEGER,
            minimum_subtotal_fils INTEGER, max_uses_per_buyer INTEGER,
            discount_type TEXT, discount_value INTEGER, maximum_discount_fils INTEGER
        )"""
    )
    connection.execute("CREATE TABLE coupon_redemptions (coupon_id INTEGER, buyer_id INTEGER)")
    connection.execute(
        "INSERT INTO coupons VALUES (1,'FIXED10',1,NULL,NULL,NULL,0,7,500,2,'fixed',2000,1500)"
    )
    quote = {
        "groups": [{"store_id": 7, "subtotal_fils": 1000}],
        "total_fils": 1200,
    }

    result = coupon_quote(connection, 3, quote, "FIXED10")
    assert result["discount_fils"] == 1000
    assert result["total_fils"] == 200
    connection.close()


def test_strict_checkout_quote_rejects_sellers_own_product(factory):
    seller = factory.seller()
    product = factory.product(seller)
    with factory.db.transaction() as connection:
        connection.execute(
            "INSERT INTO carts(user_id,product_id,variant_id,variant_key,quantity) VALUES(?,?,NULL,0,1)",
            (seller["id"], product["id"]),
        )

    with factory.db.read() as connection:
        with pytest.raises(HTTPException) as rejected:
            cart_quote(connection, seller["id"], strict=True)
    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "own_product"
