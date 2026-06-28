import base64
import io
from pathlib import Path

from PIL import Image


def pdf_to_page_images(pdf_path: str, dpi: int = 150) -> list[Image.Image]:
    """Rasterize a PDF to PIL images using PyMuPDF (no system deps)."""
    import fitz  # PyMuPDF

    pages = []
    zoom = dpi / 72.0
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pages.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    return pages


def load_page_images(path: str) -> list[Image.Image]:
    """Load page images from a .pdf, .png, or .jpg/.jpeg path."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return pdf_to_page_images(path)
    return [Image.open(path).convert("RGB")]


def image_to_data_uri(img: Image.Image, max_dim: int = 1600, fmt: str = "PNG") -> str:
    img = img.convert("RGB")
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    b64 = base64.b64encode(buf.getvalue()).decode()
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64,{b64}"
