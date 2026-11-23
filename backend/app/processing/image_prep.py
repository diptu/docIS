"""
Image clean-up before OCR: noise reduction, illumination and contrast
correction, and deskewing.

Photos of documents are often dark, shadowed, grainy or slightly rotated.
Tesseract binarises with one global threshold and expects horizontal text
lines, so each of these defects costs recognised characters. Every step
measures the image first and changes it only when needed, so clean scans
and rendered PDF pages pass through essentially untouched.

All functions take and return 8-bit grayscale ("L") images and only use
Pillow and NumPy.
"""

import math

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# --- noise -------------------------------------------------------------
# Grain is measured with Immerkaer's Laplacian-difference estimator, which
# cancels smooth content and keeps pixel-to-pixel fluctuation, including
# the fine grain JPEG compression leaves behind (a 3x3 median comparison
# misses it). The strongest responses (text edges) are ignored, and the
# result is scaled to a white page: grain on dark paper is amplified by
# the brightening that follows, so it counts for more. Clean scans, PDF
# renders and resized text score below ~1.5; visible grain scores 3+.
NOISE_THRESHOLD = 2.5
NOISE_SAMPLE = 1024
# Share of the strongest Laplacian responses (text edges) left out.
NOISE_EDGE_SHARE = 0.1
# Grain is also measured on every 2nd..4th pixel: in an image that was
# enlarged (small photos are, before OCR) each grain is a blob several
# pixels wide, which a pixel-scale measure sees as smooth.
NOISE_STRIDES = (1, 2, 3, 4)
# Noise is only removed from images whose lighting is about to be
# corrected (see ``normalize_illumination``): brightening multiplies it,
# while on white paper Tesseract copes with it better than with the thin
# strokes and decimal points a median filter erodes.

# --- illumination and contrast ------------------------------------------
# The background (paper) is estimated on a small, box-averaged copy (the
# averaging cancels grain), then a max filter wipes out the dark text
# strokes and a blur smooths what is left.
BACKGROUND_SIDE = 400
BACKGROUND_MAX_FILTER = 9
BACKGROUND_BLUR = 12
# Correct when the paper is darker than this somewhere (shadow, low light)
# or varies more than this across the page.
BACKGROUND_DARK = 200
BACKGROUND_UNEVEN = 40
# Percent of the lightest pixels ignored when stretching the contrast. The
# dark end is not clipped: on a sparse page (a receipt, a short invoice)
# text is under 0.5% of the pixels, and clipping there would stretch paper
# grain to black instead of the text.
CONTRAST_CUTOFF = 0.5
# After flattening, paper within this many grain deviations (pixel-level
# fluctuation, as for noise) of its median brightness is pushed to pure
# white, so leftover grain does not read as ink. Skipped when the estimated
# paper level is implausibly dark.
PAPER_WHITE_SIGMAS = 2.0
PAPER_MIN_WHITE = 64.0

# --- deskew --------------------------------------------------------------
SKEW_SIDE = 1000
SKEW_MAX_ANGLE = 15.0
SKEW_COARSE_STEP = 0.5
SKEW_FINE_STEP = 0.1
# Rotate only when the best angle is at least this far from level and
# lines up the text clearly better than leaving it alone.
SKEW_MIN_ANGLE = 0.3
SKEW_MIN_GAIN = 1.1
# Below this share of dark pixels there is too little text to measure.
SKEW_MIN_INK = 0.002


# Quarter turns clockwise -> Pillow transpose (Pillow's ROTATE_n is
# counter-clockwise).
CLOCKWISE_TURNS = {
    1: Image.Transpose.ROTATE_270,
    2: Image.Transpose.ROTATE_180,
    3: Image.Transpose.ROTATE_90,
}


def _to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image, dtype=np.float32)


def _from_array(array: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="L")


def _central_crop(image: Image.Image, side: int) -> Image.Image:
    width, height = image.size
    side = min(side, width, height)
    left, top = (width - side) // 2, (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def _grain_sigma(pixels: np.ndarray) -> float:
    """Immerkaer's estimate of the grain's standard deviation."""
    response = np.abs(
        pixels[:-2, :-2]
        - 2 * pixels[:-2, 1:-1]
        + pixels[:-2, 2:]
        - 2 * pixels[1:-1, :-2]
        + 4 * pixels[1:-1, 1:-1]
        - 2 * pixels[1:-1, 2:]
        + pixels[2:, :-2]
        - 2 * pixels[2:, 1:-1]
        + pixels[2:, 2:]
    )
    cutoff = np.percentile(response, 100 * (1 - NOISE_EDGE_SHARE))
    kept = response[response <= cutoff]
    # 6 = sqrt of the kernel's sum of squared weights; sqrt(pi/2) turns a
    # mean absolute deviation into a standard deviation.
    return math.sqrt(math.pi / 2) * float(kept.mean()) / 6


def _grain(image: Image.Image) -> float:
    """Grain standard deviation in 8-bit levels, on a central crop: the
    strongest of several pixel strides (Immerkaer's estimator, text edges
    excluded). Smooth structure (lighting, moire, page edges) does not count."""
    pixels = _to_array(_central_crop(image, NOISE_SAMPLE))
    strides = [s for s in NOISE_STRIDES if min(pixels.shape) // s >= 3]
    if not strides:
        return 0.0
    return max(_grain_sigma(pixels[::s, ::s]) for s in strides)


def noise_level(image: Image.Image) -> float:
    """Grain strength relative to white paper: the grain's standard
    deviation in 8-bit levels, scaled as if the paper were white."""
    paper = max(
        float(np.percentile(_to_array(_central_crop(image, NOISE_SAMPLE)), 90)), 1.0
    )
    return _grain(image) * 255.0 / paper


def reduce_noise(image: Image.Image) -> Image.Image:
    """Median-filter grainy images; leave clean ones alone."""
    if min(image.size) < 3 or noise_level(image) < NOISE_THRESHOLD:
        return image
    return image.filter(ImageFilter.MedianFilter(3))


def estimate_background(image: Image.Image) -> Image.Image:
    """The paper's brightness at every pixel, without the text."""
    scale = BACKGROUND_SIDE / max(image.size)
    small = image
    if scale < 1:
        small = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.BOX,
        )
    small = small.filter(ImageFilter.MaxFilter(BACKGROUND_MAX_FILTER))
    small = small.filter(ImageFilter.GaussianBlur(BACKGROUND_BLUR))
    return small.resize(image.size, Image.Resampling.BILINEAR)


def whiten_paper(image: Image.Image) -> Image.Image:
    """Push paper, grain included, to pure white.

    The paper level is the median brightness (paper is most of any page);
    the grain is measured like ``noise_level``, as pixel-to-pixel
    fluctuation, so broad structure (a dark surround, moire on a photo of a
    screen, light grey text) does not count. Everything within
    ``PAPER_WHITE_SIGMAS`` grain deviations of the paper becomes white and
    darker tones are stretched to match, so text keeps its contrast while
    the grain between it disappears.
    """
    pixels = _to_array(image)
    paper = float(np.median(pixels))
    white = paper - PAPER_WHITE_SIGMAS * _grain(image)
    if white <= PAPER_MIN_WHITE or white >= 255.0:
        return image
    return _from_array(pixels * (255.0 / white))


def normalize_illumination(image: Image.Image) -> Image.Image:
    """Flatten shadows and lift dark photos, then stretch the contrast and
    whiten the paper.

    Dividing by the estimated background turns unevenly lit paper into an
    even white while keeping text dark. Evenly lit, white paper is left as
    it is (dark header bars with white text would otherwise be washed out).
    A grainy image is denoised first, so the grain is not amplified.
    """
    background = _to_array(estimate_background(image))
    low, high = np.percentile(background, [5, 95])
    if low >= BACKGROUND_DARK and high - low <= BACKGROUND_UNEVEN:
        return image
    image = reduce_noise(image)
    flattened = _to_array(image) / np.maximum(background, 1.0) * 255.0
    stretched = ImageOps.autocontrast(
        _from_array(flattened), cutoff=(0, CONTRAST_CUTOFF)
    )
    return whiten_paper(stretched)


def _ink_mask(image: Image.Image) -> np.ndarray:
    """Dark pixels (text), by Otsu's threshold."""
    pixels = np.asarray(image, dtype=np.uint8)
    histogram = np.bincount(pixels.ravel(), minlength=256).astype(np.float64)
    total = histogram.sum()
    levels = np.arange(256)
    weight_dark = np.cumsum(histogram)
    weight_light = total - weight_dark
    sum_dark = np.cumsum(histogram * levels)
    mean_dark = sum_dark / np.maximum(weight_dark, 1)
    mean_light = (sum_dark[-1] - sum_dark) / np.maximum(weight_light, 1)
    between = weight_dark * weight_light * (mean_dark - mean_light) ** 2
    threshold = int(np.argmax(between))
    return pixels <= threshold


def _alignment(ink: Image.Image, angle: float) -> float:
    """How sharply text rows stand out after rotating by ``angle``: the sum
    of squared differences between neighbouring row ink counts."""
    rotated = np.asarray(
        ink.rotate(angle, resample=Image.Resampling.NEAREST, expand=True)
    )
    rows = rotated.sum(axis=1, dtype=np.float64)
    return float(np.square(np.diff(rows)).sum())


def detect_skew(image: Image.Image) -> float:
    """The rotation (degrees, counter-clockwise) that levels the text, or 0."""
    scale = min(1.0, SKEW_SIDE / max(image.size))
    small = image
    if scale < 1:
        small = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.BILINEAR,
        )
    mask = _ink_mask(small)
    if mask.mean() < SKEW_MIN_INK or mask.mean() > 0.5:
        return 0.0
    ink = Image.fromarray(mask.astype(np.uint8))

    def best(angles: np.ndarray) -> tuple[float, float]:
        scores = [_alignment(ink, float(a)) for a in angles]
        i = int(np.argmax(scores))
        return float(angles[i]), scores[i]

    steps = round(SKEW_MAX_ANGLE / SKEW_COARSE_STEP)
    coarse, _ = best(np.linspace(-SKEW_MAX_ANGLE, SKEW_MAX_ANGLE, 2 * steps + 1))
    fine_steps = round(SKEW_COARSE_STEP / SKEW_FINE_STEP)
    angle, score = best(
        coarse + np.linspace(-SKEW_COARSE_STEP, SKEW_COARSE_STEP, 2 * fine_steps + 1)
    )
    if abs(angle) < SKEW_MIN_ANGLE or score < _alignment(ink, 0.0) * SKEW_MIN_GAIN:
        return 0.0
    return round(angle, 2)


def deskew(image: Image.Image) -> Image.Image:
    """Rotate so text lines are horizontal; new corners are filled white."""
    angle = detect_skew(image)
    if angle == 0.0:
        return image
    return image.rotate(
        angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255
    )


def rotate_upright(image: Image.Image, degrees: int) -> Image.Image:
    """Rotate clockwise by a multiple of 90 degrees (Tesseract OSD's answer)."""
    turn = CLOCKWISE_TURNS.get((degrees // 90) % 4)
    return image if turn is None else image.transpose(turn)


def enhance(image: Image.Image) -> Image.Image:
    """Illumination and contrast (with noise reduction for dark, grainy
    images), then deskew."""
    if math.prod(image.size) == 0:
        return image
    image = normalize_illumination(image)
    return deskew(image)
