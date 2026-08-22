from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 900
HEIGHT = 620
MAX_BARS = 10
TRANSITION_FRAMES = 8
FRAME_DURATION_MS = 110


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ) if bold else (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _frame(payload: dict, frame: dict, previous: dict | None) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#0a0e1a")
    draw = ImageDraw.Draw(image)
    title_font = _font(25, bold=True)
    text_font = _font(17)
    small_font = _font(15)
    value_font = _font(16, bold=True)

    draw.text((36, 25), payload["title"], fill="#c89b3c", font=title_font)
    draw.text((36, 61), f'{payload["queue"]} · {payload["period"]}', fill="#718096", font=small_font)
    draw.text((WIDTH - 36, 62), frame["date"], fill="#334155", font=title_font, anchor="ra")

    values = {item["name"]: float(item["value"]) for item in frame["values"]}
    old_values = {item["name"]: float(item["value"]) for item in previous["values"]} if previous else values
    names = sorted(values, key=values.get, reverse=True)[:MAX_BARS]
    maximum = max(values.values(), default=1.0) or 1.0
    left, right = 210, WIDTH - 80
    top, row_height, bar_height = 110, 45, 28

    for index, name in enumerate(names):
        y = top + index * row_height
        value = values[name]
        old_value = old_values.get(name, 0.0)
        current = old_value + (value - old_value) * frame["progress"]
        x_end = left + (right - left) * max(current, 0.0) / maximum
        color = (78, 145, 196) if index % 3 == 0 else (89, 176, 142) if index % 3 == 1 else (196, 142, 67)
        draw.text((left - 12, y + bar_height / 2), f"#{index + 1}", fill="#4a5568", font=small_font, anchor="rm")
        draw.text((left - 24, y + bar_height / 2), name, fill="#e2e8f0", font=text_font, anchor="rs")
        draw.rounded_rectangle((left, y, max(left + 1, x_end), y + bar_height), radius=5, fill=color)
        draw.text((x_end + 9, y + bar_height / 2), f"{current:.1f}", fill="#a0aec0", font=value_font, anchor="lm")

    draw.line((left, top + MAX_BARS * row_height + 8, right, top + MAX_BARS * row_height + 8), fill="#1e2d40", width=1)
    draw.text((36, HEIGHT - 50), "Performance Score is cumulative; higher is better", fill="#718096", font=small_font)
    return image


def render_race_gif(payload: dict) -> bytes:
    frames = payload.get("frames", [])
    if not frames:
        raise ValueError("Cannot render a race without frames.")

    images: list[Image.Image] = []
    for index, current in enumerate(frames):
        previous = frames[index - 1] if index else current
        for transition in range(TRANSITION_FRAMES):
            current_frame = dict(current, progress=(transition + 1) / TRANSITION_FRAMES)
            images.append(_frame(payload, current_frame, previous))

    output = BytesIO()
    images[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        optimize=True,
    )
    return output.getvalue()