import json
from pathlib import Path

from baraholka.sources.olx import parse

PAYLOAD = json.loads((Path(__file__).parent / "fixtures/olx_offers.json").read_text())


def test_parse_olx():
    gpu, negotiable, free = parse(PAYLOAD)

    assert gpu.external_id == "310635134"
    assert gpu.price_ron == 1600 and not gpu.is_free
    assert gpu.city == "Bucuresti"
    assert "<br" not in gpu.description
    assert gpu.photos == ["https://frankfurt.apollo.olxcdn.com:443/v1/files/pb72zjp6kr202-RO/image;s=800x600"]
    assert gpu.posted_at.year == 2026

    assert negotiable.price_ron == 5100  # type "arranged" = цена с торгом
    assert free.is_free and free.price_ron == 0
