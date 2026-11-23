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
    estimate_background,
    noise_level,
    normalize_illumination,
    reduce_noise,
    rotate_upright,
    whiten_paper,
)
from evaluation.ocr_benchmark import HEADER, PhotoDefects, degrade, make_page
from tests.support.helpers import encode, make_text_image

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


def test_grain_does_not_inflate_the_background_estimate() -> None:
    rng = np.random.default_rng(0)
    paper = Image.fromarray(
        np.clip(150 + rng.normal(0, 15, (1100, 850)), 0, 255).astype(np.uint8), "L"
    )

    background = pixels(estimate_background(paper))

    # A max filter on the raw grain would report its peaks (~195).
    assert np.median(background) == pytest.approx(150, rel=0.15)


def sparse_photo(**defects: float) -> Image.Image:
    """A few lines of text on a full page (text is under 1% of the pixels),
    spoiled like a phone photo."""
    return degrade(make_page(HEADER), PhotoDefects(**defects))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "defects",
    [
        {"brightness": 0.6, "grain": 3, "jpeg_quality": 70},
        {"brightness": 0.5, "grain": 18, "jpeg_quality": 50},
        {"brightness": 0.35, "grain": 8, "jpeg_quality": 60},
        {"brightness": 0.75, "grain": 12, "shadow": True, "jpeg_quality": 50},
    ],
    ids=["dark", "dark, very grainy", "very dark", "shadowed, grainy"],
)
def test_sparse_dark_photos_get_white_paper_and_black_text(
    defects: dict[str, float],
) -> None:
    """Regression: the contrast stretch used to clip the darkest 0.5% of
    pixels. On a sparse page that is mostly paper grain, which then turned
    black and buried the text (Tesseract read nothing)."""
    fixed = pixels(normalize_illumination(sparse_photo(**defects)))

    paper = fixed[1200:2000, 200:1500]  # below the text
    text = fixed[140:520, 100:700]
    assert (paper > 240).mean() > 0.98
    assert text.min() < 40
    assert (text < 100).mean() > 0.02


def test_whiten_paper_leaves_clean_paper_alone() -> None:
    image = page()

    assert whiten_paper(image) is image


def test_whiten_paper_keeps_text_dark() -> None:
    grey = Image.fromarray(
        np.clip(pixels(grainy(page(), 10)) * 0.8, 0, 255).astype(np.uint8), "L"
    )

    white = pixels(whiten_paper(grey))

    assert np.median(white) == 255
    assert white.min() < 40


def test_whiten_paper_ignores_moire_and_keeps_grey_text() -> None:
    """Regression: a photo of a screen (broad moire stripes) once counted
    as grain, the white point dropped below the grey text and erased it."""
    width, height = 1200, 900
    stripes = 200 + 40 * np.sin(np.arange(width) / 6.0)
    paper = np.tile(stripes, (height, 1))
    text = pixels(make_text_image(LINES, size=(width, height)).convert("L")) < 128
    image = Image.fromarray(np.where(text, 120, paper).astype(np.uint8), "L")

    white = pixels(whiten_paper(image))

    assert white[text].mean() < 180  # grey text is still visibly dark


# ---------------------------------------------------------------- noise


def grainy(base: Image.Image, strength: float = 25.0) -> Image.Image:
    rng = np.random.default_rng(0)
    noisy = pixels(base) + rng.normal(0, strength, pixels(base).shape)
    return Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8), mode="L")


def test_noise_level_tells_grainy_from_clean() -> None:
    assert noise_level(page()) < 1.0
    assert noise_level(grainy(page())) > image_prep.NOISE_THRESHOLD


def jpeg(image: Image.Image, quality: int) -> Image.Image:
    from io import BytesIO

    return Image.open(BytesIO(encode(image, "JPEG", quality=quality))).convert("L")


def test_noise_level_sees_grain_smoothed_by_jpeg() -> None:
    """JPEG compression softens grain into small blotches that a 3x3
    median comparison (the old measure) scored as clean."""
    assert noise_level(jpeg(grainy(page(), 10), 50)) > image_prep.NOISE_THRESHOLD


def test_noise_level_sees_grain_in_enlarged_images() -> None:
    """Small photos are enlarged before clean-up; each grain is then a blob
    several pixels wide."""
    small = grainy(page().resize((700, 250)), 15)
    enlarged = small.resize((2100, 750), Image.Resampling.LANCZOS)

    assert noise_level(enlarged) > image_prep.NOISE_THRESHOLD


@pytest.mark.parametrize("factor", [0.5, 0.4])
def test_resized_clean_text_is_not_mistaken_for_grain(factor: float) -> None:
    """The old measure scored downscaled text edges as noise."""
    original = page()
    small = original.resize(
        (round(original.width * factor), round(original.height * factor)),
        Image.Resampling.LANCZOS,
    )
    enlarged = jpeg(small, 75).resize(original.size, Image.Resampling.LANCZOS)

    assert noise_level(jpeg(small, 75)) < image_prep.NOISE_THRESHOLD
    assert noise_level(enlarged) < image_prep.NOISE_THRESHOLD


def test_grain_is_measured_relative_to_the_paper() -> None:
    """Brightening dark paper amplifies its grain by the same factor, so a
    dimmed copy of a grainy page scores like the original."""
    noisy = grainy(page(), 10)
    dark = Image.fromarray((pixels(noisy) * 0.4).astype(np.uint8), "L")

    assert noise_level(dark) == pytest.approx(noise_level(noisy), rel=0.3)


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
