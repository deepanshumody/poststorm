import base64
import io

from PIL import Image

from backend import images


def test_data_uri_roundtrip_and_downscale():
    img = Image.new("RGB", (4000, 3000), "white")
    uri = images.image_to_data_uri(img, max_dim=1600)
    assert uri.startswith("data:image/png;base64,")
    raw = base64.b64decode(uri.split(",", 1)[1])
    out = Image.open(io.BytesIO(raw))
    assert max(out.size) <= 1600


def test_load_png(tmp_path):
    p = tmp_path / "x.png"
    Image.new("RGB", (100, 80), "white").save(p)
    pages = images.load_page_images(str(p))
    assert len(pages) == 1 and pages[0].size == (100, 80)
