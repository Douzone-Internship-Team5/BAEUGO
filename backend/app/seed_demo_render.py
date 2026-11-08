"""시연 시드용 슬라이드·썸네일 이미지를 그린다. 이미지를 바꿀 때만 실행한다.

컨테이너에는 한글 글꼴이 없으므로 글꼴 파일 경로를 넘긴다 (예: Noto Sans KR).
실행: docker compose exec api python -m app.seed_demo_render /tmp/NotoSansKR-VF.ttf
결과: app/seed_assets/demo/{강좌 slug}/thumbnail.png, {영상 slug}.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.seed_demo_data import CATEGORY_LABELS, COURSES, INSTRUCTOR_NAMES, DemoCourse, DemoVideo

OUTPUT_DIR = Path(__file__).parent / "seed_assets" / "demo"
WIDTH, HEIGHT = 960, 540
PAPER = "#f8f9fb"
INK = "#1b2033"
MUTED = "#5d6479"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def tint(color: str, amount: float) -> tuple[int, int, int]:
    """amount 0이면 원래 색, 1이면 흰색."""
    r, g, b = hex_to_rgb(color)
    return (
        round(r + (255 - r) * amount),
        round(g + (255 - g) * amount),
        round(b + (255 - b) * amount),
    )


def font(path: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(path, size)
    try:
        face.set_variation_by_axes([weight])
    except (OSError, AttributeError):
        # 가변 글꼴이 아니면 기본 굵기로 그린다.
        pass
    return face


def wrap(draw: ImageDraw.ImageDraw, text: str, face, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=face) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_thumbnail(course: DemoCourse, font_path: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), hex_to_rgb(course.color))
    draw = ImageDraw.Draw(image)
    # 오른쪽 아래 큰 원 두 개로 강좌마다 같은 틀, 다른 색을 준다.
    draw.ellipse((600, 220, 1140, 760), fill=tint(course.color, 0.18))
    draw.ellipse((720, 330, 1040, 650), fill=tint(course.color, 0.32))

    label_face = font(font_path, 26, 600)
    label = CATEGORY_LABELS[course.category_id]
    label_width = draw.textlength(label, font=label_face)
    draw.rounded_rectangle((56, 56, 56 + label_width + 36, 104), radius=24, fill="#ffffff")
    draw.text((74, 63), label, font=label_face, fill=hex_to_rgb(course.color))

    title_face = font(font_path, 62, 700)
    y = 150
    for line in wrap(draw, course.title, title_face, 760):
        draw.text((56, y), line, font=title_face, fill="#ffffff")
        y += 80

    name_face = font(font_path, 28, 500)
    draw.text(
        (56, HEIGHT - 84),
        f"{INSTRUCTOR_NAMES[course.instructor_email]} 강사",
        font=name_face,
        fill=tint(course.color, 0.75),
    )
    return image


def render_slide(course: DemoCourse, video: DemoVideo, font_path: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 18, HEIGHT), fill=hex_to_rgb(course.color))

    small_face = font(font_path, 22, 500)
    kind = "무료 OT" if video.slug == "ot" else "학습 영상"
    draw.text((64, 48), f"{course.title} · {kind}", font=small_face, fill=MUTED)

    title_face = font(font_path, 48, 700)
    y = 96
    for line in wrap(draw, video.title, title_face, 830):
        draw.text((64, y), line, font=title_face, fill=INK)
        y += 64

    bullet_face = font(font_path, 32, 500)
    y += 36
    for bullet in video.bullets:
        draw.ellipse((66, y + 14, 80, y + 28), fill=hex_to_rgb(course.color))
        for index, line in enumerate(wrap(draw, bullet, bullet_face, 780)):
            draw.text((100, y), line, font=bullet_face, fill=INK)
            y += 46 if index == 0 else 42
        y += 18

    brand_face = font(font_path, 20, 700)
    draw.text((WIDTH - 140, HEIGHT - 52), "BAEUGO", font=brand_face, fill=MUTED)
    return image


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 색 수를 줄여 레포에 들어가는 PNG 크기를 줄인다.
    image.quantize(colors=64).save(path, optimize=True)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("사용법: python -m app.seed_demo_render <한글 글꼴 파일 경로>")
    font_path = sys.argv[1]
    for course in COURSES:
        course_dir = OUTPUT_DIR / course.slug
        save(render_thumbnail(course, font_path), course_dir / "thumbnail.png")
        for video in (course.ot, *(section.lesson for section in course.sections)):
            save(render_slide(course, video, font_path), course_dir / f"{video.slug}.png")


if __name__ == "__main__":
    main()
