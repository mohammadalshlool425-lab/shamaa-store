"""Case attachment identifiers remain scoped to their parent case."""

from io import BytesIO

from PIL import Image


def _png_evidence():
    output = BytesIO()
    Image.new("RGB", (48, 48), (30, 90, 150)).save(output, format="PNG")
    return output.getvalue()


def _ship(seller, shipment):
    expected = "new"
    for status in ("confirmed", "preparing", "shipped"):
        response = seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": status, "expected_status": expected},
        )
        assert response.status_code == 200, response.text
        expected = status


def test_attachment_id_cannot_be_replayed_under_another_case(market):
    factory = market["factory"]
    buyer = market["buyer"]
    factory.cart(buyer, market["p"], 1)
    quote = factory.cart(buyer, market["q"], 1)
    checkout, _, _ = factory.checkout(buyer, quote)
    assert checkout.status_code == 201, checkout.text

    shipments = checkout.json()["order"]["shipments"]
    shipment_a = next(row for row in shipments if row["store_id"] == market["a"]["store_id"])
    shipment_b = next(row for row in shipments if row["store_id"] == market["b"]["store_id"])
    _ship(market["a"], shipment_a)
    _ship(market["b"], shipment_b)

    def open_case(shipment_id):
        response = buyer["client"].post("/api/cases", json={
            "seller_order_id": shipment_id,
            "reason": "not_received",
            "description": "لم تصل الشحنة وأحتاج إلى مراجعة مسارها.",
        })
        assert response.status_code == 201, response.text
        return response.json()["case"]["id"]

    case_a = open_case(shipment_a["id"])
    case_b = open_case(shipment_b["id"])
    uploaded = buyer["client"].post(
        f"/api/cases/{case_a}/attachments",
        files={"file": ("evidence.png", _png_evidence(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment_id = uploaded.json()["attachment_id"]

    own_case_download = buyer["client"].get(
        f"/api/cases/{case_a}/attachments/{attachment_id}"
    )
    assert own_case_download.status_code == 200

    # The same buyer participates in both cases; the attachment still belongs
    # only to case A and must not be retrievable through case B's URL.
    cross_case_download = buyer["client"].get(
        f"/api/cases/{case_b}/attachments/{attachment_id}"
    )
    assert cross_case_download.status_code == 404
