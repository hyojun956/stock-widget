"""GT Task Manager 아이콘(GT + TM)과 같은 스타일로 GT + Stock 아이콘 생성 → icon.ico

작은 크기(16~48)를 각각 따로 그리면 곡선·글자 테두리가 거칠어 보이므로,
큰 캔버스(MASTER)에 한 번만 그린 뒤 LANCZOS로 축소해 모든 크기를 깔끔하게 만든다."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

GT_BLUE = (51, 51, 168)       # #3333A8
WHITE = (255, 255, 255)
SUB = (200, 200, 240)

MASTER = 1024                             # 이 해상도로 한 번 그리고 나머지는 축소
sizes = [512, 256, 128, 96, 64, 48, 40, 32, 24, 20, 16]

size = MASTER
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Rounded blue background
margin = int(size * 0.04)
r = int(size * 0.20)
draw.rounded_rectangle([margin, margin, size - margin, size - margin],
                       radius=r, fill=(*GT_BLUE, 255))

# "GT" text centered
font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(size * 0.48))
bbox = draw.textbbox((0, 0), "GT", font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
tx = (size - tw) // 2 - bbox[0]
ty = (size - th) // 2 - bbox[1] - int(size * 0.03)
draw.text((tx, ty), "GT", fill=WHITE, font=font)

# "Stock" small in bottom-right (TM 자리) — 글자가 길어서 오른쪽 정렬
sm_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", int(size * 0.14))
sb = draw.textbbox((0, 0), "Stock", font=sm_font)
sx = size - int(size * 0.10) - (sb[2] - sb[0]) - sb[0]
sy = size - int(size * 0.26)
draw.text((sx, sy), "Stock", fill=SUB, font=sm_font)

master = img
images = []
for s in sizes:
    small = master.resize((s, s), Image.LANCZOS)
    if s < 48:
        # 아주 작은 크기에서는 "Stock" 글자가 뭉개지므로 GT만 남긴 버전을 따로 그림
        small = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(small)
        m = int(s * 0.04)
        d.rounded_rectangle([m, m, s - m, s - m], radius=int(s * 0.20), fill=(*GT_BLUE, 255))
        f = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(s * 0.60))
        b = d.textbbox((0, 0), "GT", font=f)
        tw, th = b[2] - b[0], b[3] - b[1]
        d.text(((s - tw) // 2 - b[0], (s - th) // 2 - b[1] - int(s * 0.02)), "GT", fill=WHITE, font=f)
    images.append(small)

here = Path(__file__).parent
images[0].save(str(here / "icon.ico"), format="ICO",
               sizes=[(s, s) for s in sizes], append_images=images[1:])
master.resize((256, 256), Image.LANCZOS).save(str(here / "icon.png"))
print("GT Stock icon created:", here / "icon.ico")
