"""Deterministic image metrics (no AI): formulas, letterbox crop, determinism."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.domains.media.imaging import WORK_WIDTH, InvalidImageError, compute_image_metrics


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _solid(color, size=(640, 360)) -> bytes:
    return _png(Image.new("RGB", size, color))


def _checker(size=(640, 360), cell=20) -> Image.Image:
    im = Image.new("RGB", size, (0, 0, 0))
    d = ImageDraw.Draw(im)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2 == 0:
                d.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(255, 255, 255))
    return im


def test_uniform_grey_has_no_contrast_colour_or_edges():
    m = compute_image_metrics(_solid((128, 128, 128)))
    assert m.luminance_mean == pytest.approx(128 / 255, abs=1e-3)
    assert m.contrast_rms == 0 and m.colorfulness == 0
    assert m.sharpness_laplacian == 0 and m.edge_density == 0
    assert m.saliency_center_ratio is None  # no gradient energy at all
    assert m.dominant_colors == [{"hex": "#808080", "share": 1.0}]
    assert (m.work_width, m.work_height, m.letterbox_cropped) == (WORK_WIDTH, 180, False)


def test_scales_and_ordering_of_colourfulness_and_brightness():
    red, grey = compute_image_metrics(_solid((220, 20, 20))), compute_image_metrics(_solid((120, 120, 120)))
    assert red.colorfulness > 50 > grey.colorfulness == 0
    white, black = compute_image_metrics(_solid((255, 255, 255))), compute_image_metrics(_solid((0, 0, 0)))
    assert white.luminance_mean == pytest.approx(1.0) and black.luminance_mean == 0


def test_blur_lowers_sharpness_but_not_the_amount_of_edges():
    sharp_im = _checker()
    blurred = compute_image_metrics(_png(sharp_im.filter(ImageFilter.GaussianBlur(3))))
    sharp = compute_image_metrics(_png(sharp_im))
    assert sharp.sharpness_laplacian > 10 * blurred.sharpness_laplacian
    # edges are thinned to 1 px lines: the same layout => about the same edge density, blurred or not
    assert blurred.edge_density == pytest.approx(sharp.edge_density, rel=0.35)
    assert sharp.contrast_rms > blurred.contrast_rms


def test_busier_picture_has_higher_edge_density():
    simple, busy = (
        compute_image_metrics(_png(_checker(cell=80))),
        compute_image_metrics(_png(_checker(cell=20))),
    )
    assert busy.edge_density > 2.5 * simple.edge_density
    assert 0 < simple.edge_density < busy.edge_density < 0.5


def test_saliency_centre_ratio_follows_the_subject():
    def subject_at(box):
        im = Image.new("RGB", (640, 360), (40, 40, 40))
        ImageDraw.Draw(im).rectangle(box, fill=(250, 220, 30))
        return compute_image_metrics(_png(im)).saliency_center_ratio

    centred, corner = subject_at((260, 130, 380, 230)), subject_at((10, 10, 110, 90))
    assert centred is not None and corner is not None
    assert centred > 0.9 and corner < 0.05  # 0.25 would be evenly spread


def test_letterboxed_hqdefault_is_cropped_before_measuring():
    content = Image.new("RGB", (480, 270), (200, 60, 60))
    boxed = Image.new("RGB", (480, 360), (0, 0, 0))  # 4:3 hqdefault with 45 px bars (12.5 %)
    boxed.paste(content, (0, 45))
    m, plain = compute_image_metrics(_png(boxed)), compute_image_metrics(_png(content))
    assert m.letterbox_cropped and m.work_height == 180  # 16:9 again
    assert m.luminance_mean == pytest.approx(plain.luminance_mean, abs=0.01)
    assert all(c["hex"] != "#000000" for c in m.dominant_colors)


def test_dark_image_and_one_sided_bar_are_not_cropped():
    assert not compute_image_metrics(_solid((5, 5, 5), (480, 360))).letterbox_cropped
    one_bar = Image.new("RGB", (480, 360), (200, 200, 0))
    ImageDraw.Draw(one_bar).rectangle((0, 0, 479, 44), fill=(0, 0, 0))  # top only (dark sky, not a bar)
    assert not compute_image_metrics(_png(one_bar)).letterbox_cropped


def test_metrics_are_deterministic_and_resolution_independent():
    im = _checker((1280, 720), cell=40)
    a, b = compute_image_metrics(_png(im)), compute_image_metrics(_png(im))
    assert a == b
    small = compute_image_metrics(_png(im.resize((640, 360), Image.Resampling.NEAREST)))
    assert small.edge_density == pytest.approx(a.edge_density, rel=0.1)  # both analysed at 320 px


def test_dominant_colours_are_sorted_by_share():
    im = Image.new("RGB", (640, 360), (20, 40, 200))
    ImageDraw.Draw(im).rectangle((0, 0, 159, 359), fill=(240, 240, 240))  # 25 % white
    colours = compute_image_metrics(_png(im)).dominant_colors
    assert [c["share"] for c in colours] == sorted((c["share"] for c in colours), reverse=True)
    assert colours[0]["share"] == pytest.approx(0.75, abs=0.03)
    assert sum(c["share"] for c in colours) == pytest.approx(1.0, abs=0.01)


def test_invalid_bytes_raise():
    with pytest.raises(InvalidImageError):
        compute_image_metrics(b"definitely not an image")
