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
# Mean absolute difference between the image and its 3x3 median, on a
# central crop. Clean renders and scans score ~0-2; sensor noise scores
# higher. Noise is only removed from images that are about to be brightened
# (see ``normalize_illumination``): brightening multiplies it, while on
# white paper Tesseract copes with it better than with the thin strokes and
# decimal points a median filter erodes.
NOISE_THRESHOLD = 4.0
NOISE_SAMPLE = 512
# Brightening gain (white / median paper brightness) above which noise is
# removed first.
NOISE_BRIGHTENING_GAIN = 1.5

# --- illumination and contrast ------------------------------------------
# The background (paper) is estimated on a small copy: a max filter wipes
# out the dark text strokes, a blur smooths what is left.
BACKGROUND_SIDE = 400
BACKGROUND_MAX_FILTER = 9
BACKGROUND_BLUR = 12
# Correct when the paper is darker than this somewhere (shadow, low light)
# or varies more than this across the page.
BACKGROUND_DARK = 200
BACKGROUND_UNEVEN = 40
# Percent of darkest/lightest pixels ignored when stretching the contrast.
CONTRAST_CUTOFF = 0.5

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


def noise_level(image: Image.Image) -> float:
    """Mean absolute difference from a 3x3 median filter, on a central crop."""
    width, height = image.size
    side = min(NOISE_SAMPLE, width, height)
    left, top = (width - side) // 2, (height - side) // 2
    crop = image.crop((left, top, left + side, top + side))
    smoothed = crop.filter(ImageFilter.MedianFilter(3))
    return float(np.abs(_to_array(crop) - _to_array(smoothed)).mean())


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
            Image.Resampling.BILINEAR,
        )
    small = small.filter(ImageFilter.MaxFilter(BACKGROUND_MAX_FILTER))
    small = small.filter(ImageFilter.GaussianBlur(BACKGROUND_BLUR))
    return small.resize(image.size, Image.Resampling.BILINEAR)


def normalize_illumination(image: Image.Image) -> Image.Image:
    """Flatten shadows and lift dark photos, then stretch the contrast.

    Dividing by the estimated background turns unevenly lit paper into an
    even white while keeping text dark. Evenly lit, white paper is left as
    it is (dark header bars with white text would otherwise be washed out).
    A dark, grainy image is denoised first, so the grain is not amplified.
    """
    background = _to_array(estimate_background(image))
    low, median, high = np.percentile(background, [5, 50, 95])
    if low >= BACKGROUND_DARK and high - low <= BACKGROUND_UNEVEN:
        return image
    if 255.0 / max(float(median), 1.0) >= NOISE_BRIGHTENING_GAIN:
        image = reduce_noise(image)
    flattened = _to_array(image) / np.maximum(background, 1.0) * 255.0
    return ImageOps.autocontrast(_from_array(flattened), cutoff=CONTRAST_CUTOFF)


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
