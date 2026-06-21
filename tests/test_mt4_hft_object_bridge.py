from app.main import _extract_mt4_rich_fields, _mt4_debug_rich_fields


def test_mt4_rich_fields_keep_nearest_hft_object_contract():
    payload = {
        "hft_object": {
            "name": "prefix_fut_hft_point_123",
            "type": "hft",
            "price": 1.1012,
            "distance": 0.0003,
            "strength": 4,
        }
    }

    rich = _extract_mt4_rich_fields(payload)

    assert rich["hft_object_found"] is True
    assert rich["hft_object_name"] == "prefix_fut_hft_point_123"
    assert rich["hft_object_type"] == "hft"
    assert rich["hft_object_price"] == 1.1012
    assert rich["hft_object_distance"] == 0.0003
    assert rich["hft_object_strength"] == 4
    assert _mt4_debug_rich_fields(rich)["hft_object_type"] == "hft"


def test_mt4_rich_fields_accept_flat_ice_object_contract():
    rich = _extract_mt4_rich_fields({
        "hft_object_name": "fut_ice_point_999",
        "hft_object_type": "ice",
        "hft_object_price": "1.0990",
        "hft_object_distance": "0.0001",
        "hft_object_strength": "2",
    })

    assert rich["hft_object_found"] is True
    assert rich["hft_object_type"] == "ice"
    assert rich["hft_object_price"] == 1.099
