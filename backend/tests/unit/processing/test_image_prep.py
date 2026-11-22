"""Image clean-up before OCR (app/processing/image_prep.py). Pure image
processing: no Tesseract needed."""

import numpy as np
import pytest
from PIL import Image, ImageChops

from app.processing import image_prep
from app.processing.image_prep import (
    deskew,
    detect_skew,
    enhance,
    noise_level,
    normalize_illumination,
    reduce_noise,
    rotate_upright,
)
from tests.support.helpers import make_text_image

LINES = [
    "ACME GmbH Invoice 2026-001",
    "Total due: 1,250.00 EUR",
    "Payment within 30 days",
    "Bank transfer to account 12345678",
]


def page() -> Image.Image:
    return make_text_image(LINES, size=(1400, 500)).convert("L")


def tilted(angle: float) -> Image.Image:
    return page().rotate(
        angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255
    )


def pixels(image: Image.Image) -> np.ndarray:
    return np.asarray(image, dtype=np.float32)


# ---------------------------------------------------------------- deskew


@pytest.mark.parametrize("angle", [-12.0, -4.0, 2.5, 7.0, 14.0])
def test_detects_the_tilt_of_text(angle: float) -> None:
    assert detect_skew(tilted(angle)) == pytest.approx(-angle, abs=0.3)


def test_level_text_is_not_rotated() -> None:
    image = page()

    assert detect_skew(image) == 0.0
    assert deskew(image) is image


def test_deskew_levels_the_text() -> None:
    straightened = deskew(tilted(9.0))

    assert detect_skew(straightened) == 0.0
    # Corners uncovered by the rotation are white, not black.
    assert straightened.getpixel((0, 0)) == 255


@pytest.mark.parametrize(
    "image",
    [Image.new("L", (800, 600), 255), Image.new("L", (800, 600), 0)],
    ids=["blank", "all ink"],
)
def test_pages_without_measurable_text_are_left_alone(image: Image.Image) -> None:
    assert detect_skew(image) == 0.0


def test_tilts_beyond_the_search_range_are_not_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_prep, "SKEW_MAX_ANGLE", 3.0)

    assert abs(detect_skew(tilted(10.0))) <= 3.0


# ------------------------------------------------- illumination, contrast


def shadowed() -> Image.Image:
    """The page with a dark shadow over its right half."""
    array = pixels(page())
    width = array.shape[1]
    light = np.ones_like(array)
    light[:, width // 2 :] = 0.35
    return Image.fromarray((array * light).astype(np.uint8), mode="L")


def test_clean_white_paper_is_left_unchanged() -> None:
    image = page()

    assert normalize_illumination(image) is image


def test_shadows_are_flattened_and_text_stays_dark() -> None:
    image = shadowed()
    right = (900, 0, 1400, 40)  # paper in the shadowed half, above the text

    assert pixels(image.crop(right)).mean() < 100
    fixed = normalize_illumination(image)

    assert pixels(fixed.crop(right)).mean() > 230
    assert pixels(fixed).min() < 60  # text survives


def test_dark_photos_are_brightened_and_stretched() -> None:
    dark = Image.fromarray((pixels(page()) * 0.25).astype(np.uint8), mode="L")

    fixed = normalize_illumination(dark)

    assert np.percentile(pixels(fixed), 50) > 230  # paper is white again
    assert pixels(fixed).min() < 40  # and text is black


# ---------------------------------------------------------------- noise


def grainy(base: Image.Image, strength: float = 25.0) -> Image.Image:
    rng = np.random.default_rng(0)
    noisy = pixels(base) + rng.normal(0, strength, pixels(base).shape)
    return Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8), mode="L")


def test_noise_level_tells_grainy_from_clean() -> None:
    assert noise_level(page()) < 1.0
    assert noise_level(grainy(page())) > image_prep.NOISE_THRESHOLD


def test_reduce_noise_smooths_grainy_images_only() -> None:
    clean = page()
    noisy = grainy(clean)

    assert reduce_noise(clean) is clean
    assert noise_level(reduce_noise(noisy)) < noise_level(noisy) / 2


def test_dark_grainy_photos_are_denoised_before_brightening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Image.Image] = []
    real = image_prep.reduce_noise
    monkeypatch.setattr(
        image_prep, "reduce_noise", lambda im: calls.append(im) or real(im)
    )
    dark = Image.fromarray((pixels(grainy(page(), 10)) * 0.3).astype(np.uint8), "L")

    normalize_illumination(dark)

    assert len(calls) == 1


def test_grainy_white_scans_are_not_median_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A median filter erodes thin strokes and decimal points; on white
    paper Tesseract reads the noisy original better (measured)."""
    monkeypatch.setattr(
        image_prep,
        "reduce_noise",
        lambda _: pytest.fail("noise reduction must not run on white paper"),
    )

    enhance(grainy(page(), 8))


# --------------------------------------------------------- orientation


@pytest.mark.parametrize(
    ("turn", "rotate"),
    [
        (Image.Transpose.ROTATE_90, 90),  # page turned counter-clockwise
        (Image.Transpose.ROTATE_180, 180),
        (Image.Transpose.ROTATE_270, 270),  # page turned clockwise
    ],
)
def test_rotate_upright_undoes_quarter_turns(
    turn: Image.Transpose, rotate: int
) -> None:
    original = page()

    restored = rotate_upright(original.transpose(turn), rotate)

    assert ImageChops.difference(restored, original).getbbox() is None


def test_rotate_upright_by_zero_is_a_no_op() -> None:
    image = page()

    assert rotate_upright(image, 0) is image
    assert rotate_upright(image, 360) is image


def test_enhance_accepts_empty_images() -> None:
    empty = Image.new("L", (0, 0))

    assert enhance(empty) is empty
