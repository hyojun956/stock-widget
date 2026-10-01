"""GT Task Manager 아이콘(GT + TM)과 같은 스타일로 GT + Stock 아이콘 생성 → icon.ico"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

GT_BLUE = (51, 51, 168)       # #3333A8
WHITE = (255, 255, 255)
SUB = (200, 200, 240)

sizes = [256, 128, 64, 48, 32, 16]
images = []

for size in sizes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded blue background
    margin = int(size * 0.04)
    r = int(size * 0.20)
    draw.rounded_rectangle([margin, margin, size - margin, size - margin],
                           radius=r, fill=(*GT_BLUE, 255))

    # "GT" text centered
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(size * 0.48))
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "GT", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1] - int(size * 0.03)
    draw.text((tx, ty), "GT", fill=WHITE, font=font)

    # "Stock" small in bottom-right (TM 자리) — 글자가 길어서 오른쪽 정렬
    if size >= 48:
        try:
            sm_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", max(8, int(size * 0.14)))
        except Exception:
            sm_font = font
        sb = draw.textbbox((0, 0), "Stock", font=sm_font)
        sx = size - int(size * 0.10) - (sb[2] - sb[0]) - sb[0]
        sy = size - int(size * 0.26)
        draw.text((sx, sy), "Stock", fill=SUB, font=sm_font)

    images.append(img)

here = Path(__file__).parent
images[0].save(str(here / "icon.ico"), format="ICO",
               sizes=[(s, s) for s in sizes], append_images=images[1:])
images[0].save(str(here / "icon.png"))
print("GT Stock icon created:", here / "icon.ico")
