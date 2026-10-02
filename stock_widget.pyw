"""
실시간 주가 위젯 (Windows 데스크톱)
- 테두리 없는 작은 창, 마우스로 드래그해서 원하는 곳에 배치
- 실시간 현재가 / 전일대비 / 등락률 / 거래량 (상승 빨강, 하락 파랑)
- 국내(코스피·코스닥) + 해외(미국 등) 종목 지원 — 네이버 증권 데이터
- 우클릭 메뉴: 종목 추가/삭제, 투명도, 항상 위, 갱신 주기, 종료
- 위치와 종목 목록은 설정 파일에 자동 저장
- 배포용 exe는 GitHub Releases에서 새 버전을 확인해 클릭 한 번으로 업데이트
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
from datetime import datetime

APP_VERSION = "1.0.7"
GITHUB_REPO = "hyojun956/stock-widget"   # 업데이트를 받아올 저장소 (release.ps1 로 배포)
# API 대신 릴리스 첨부파일 직접 링크 사용 → 사무실 전체가 같은 IP여도 GitHub API 호출 제한(시간당 60회)에 안 걸림
RELEASE_BASE = f"https://github.com/{GITHUB_REPO}/releases/latest/download/"
UPDATE_CHECK_MINUTES = 30

FROZEN = getattr(sys, "frozen", False)   # PyInstaller로 만든 exe로 실행 중인지
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
if FROZEN:
    # exe는 업데이트 때 파일이 교체되므로 설정은 사용자 폴더(%APPDATA%\StockWidget)에 보관
    _cfg_dir = os.path.join(os.environ.get("APPDATA", APP_DIR), "StockWidget")
    os.makedirs(_cfg_dir, exist_ok=True)
    CONFIG_PATH = os.path.join(_cfg_dir, "config.json")
else:
    CONFIG_PATH = os.path.join(APP_DIR, "stock_widget_config.json")
# exe 안에 묶인 파일은 PyInstaller 임시 폴더(sys._MEIPASS)에 풀림
ICON_PATH = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "assets", "icon.ico")

DEFAULT_CONFIG = {
    "x": 100,
    "y": 100,
    "alpha": 0.92,
    "topmost": True,
    "interval": 3,
    "win_w": None,   # 수동으로 크기 조절하면 채워짐 (None이면 자동 맞춤)
    "win_h": None,
    "stocks": [
        {"code": "486510", "type": "domestic", "name": "글로벌테크놀로지"},
    ],
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
REALTIME_URL = "https://polling.finance.naver.com/api/realtime/{type}/stock/{codes}"
SEARCH_URL = "https://ac.stock.naver.com/ac?q={q}&target=stock,worldstock"
CHART_URL = "https://api.stock.naver.com/chart/{market}/item/{code}"
ORDERBOOK_URL = "https://m.stock.naver.com/api/stock/{code}/askingPrice"

# 색상 (다크 테마)
BG = "#16181d"
BG_ROW = "#1e2128"
FG = "#e8eaed"
FG_DIM = "#8a8f98"
UP = "#ff4d4f"      # 상승: 빨강
DOWN = "#4d8dff"    # 하락: 파랑
FLAT = "#b0b4ba"    # 보합
FONT = "Malgun Gothic"
SLIDER_W = 64
EDGE = 6          # 창 테두리에서 이 픽셀 안쪽을 잡으면 크기 조절
MIN_W, MIN_H = 270, 30
ALPHA_MIN = 0.3   # 너무 투명해서 안 보이는 것 방지

# 네이버 compareToPreviousPrice.code: 1 상한, 2 상승, 3 보합, 4 하한, 5 하락
UP_CODES = {"1", "2"}
DOWN_CODES = {"4", "5"}


def korean_money(raw):
    """원 단위 금액 → '1,610조 6,497억' (억 미만 버림)."""
    try:
        eok = int(raw) // 10**8
    except (TypeError, ValueError):
        return ""
    jo, eok = divmod(eok, 10**4)
    if jo and eok:
        return f"{jo:,}조 {eok:,}억"
    return f"{jo:,}조" if jo else f"{eok:,}억"


def set_if_changed(widget, **kw):
    """값이 실제로 달라진 옵션만 다시 그림 (불필요한 redraw 방지)."""
    diff = {k: v for k, v in kw.items() if str(widget.cget(k)) != str(v)}
    if diff:
        widget.config(**diff)


def to_num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def mix(c1, c2, t):
    """두 #rrggbb 색을 t(0~1) 비율로 섞음 (Tk 캔버스는 반투명이 없어서 어두운 채움색을 만들 때 사용)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def dark_titlebar(window):
    """Windows 10/11 제목 표시줄을 다크 모드로 (편집 창·차트 창이 흰 제목줄로 튀지 않게)."""
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


def set_crisp_icon(window, path=ICON_PATH):
    """tkinter의 iconbitmap/iconphoto는 작은 비트맵 하나만 뽑아 확대 표시해 흐릿해지므로,
    현재 DPI 배율에 맞는 크기를 ico에서 직접 골라 taskbar/Alt-Tab 아이콘으로 지정한다.
    (-alpha 속성을 쓰는 Tk 창은 내부적으로 래퍼 HWND를 하나 더 두므로 GetParent로 찾아야 함)"""
    try:
        import ctypes
        window.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        big = user32.GetSystemMetrics(11)    # SM_CXICON
        small = user32.GetSystemMetrics(49)  # SM_CXSMICON
        h_big = user32.LoadImageW(0, path, IMAGE_ICON, big, big, LR_LOADFROMFILE)
        h_small = user32.LoadImageW(0, path, IMAGE_ICON, small, small, LR_LOADFROMFILE)
        if h_big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, h_big)
        if h_small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, h_small)
    except Exception:
        pass


# ---------------------------------------------------------------- data
def http_json(url, timeout=5):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_quotes(stocks):
    """{(type, code): quote dict} 반환. 국내/해외를 각각 한 번에 묶어서 요청."""
    result = {}
    groups = {}
    for s in stocks:
        groups.setdefault(s["type"], []).append(s["code"])
    for typ, codes in groups.items():
        url = REALTIME_URL.format(type=typ, codes=",".join(urllib.parse.quote(c) for c in codes))
        data = http_json(url)
        for d in data.get("datas", []):
            code = d.get("itemCode") if typ == "domestic" else d.get("reutersCode")
            result[(typ, code)] = d
    return result


def search_stocks(query):
    url = SEARCH_URL.format(q=urllib.parse.quote(query))
    items = http_json(url).get("items", [])
    out = []
    for it in items:
        if it.get("category") != "stock":
            continue
        if it.get("nationCode") == "KOR":
            out.append({"code": it["code"], "type": "domestic", "name": it["name"],
                        "label": f'{it["name"]}  ({it["code"]} · {it.get("typeName", "")})'})
        else:
            out.append({"code": it["reutersCode"], "type": "worldstock", "name": it["name"],
                        "label": f'{it["name"]}  ({it["code"]} · {it.get("nationName", "")})'})
    return out


def fetch_chart(stock, mode):
    """[(라벨, 시가, 고가, 저가, 종가, 거래량)] — mode: "minute"(당일 1분) / "day"(일봉)."""
    market = "domestic" if stock["type"] == "domestic" else "foreign"
    url = CHART_URL.format(market=market, code=urllib.parse.quote(stock["code"]))
    if mode == "minute":
        rows = http_json(url + "/minute", timeout=8)
        return [(r["localDateTime"][8:10] + ":" + r["localDateTime"][10:12], r["openPrice"], r["highPrice"],
                 r["lowPrice"], r["currentPrice"], r.get("accumulatedTradingVolume") or 0) for r in rows]
    rows = http_json(url + "?periodType=dayCandle", timeout=8).get("priceInfos", [])
    return [(f'{r["localDate"][4:6]}/{r["localDate"][6:8]}', r["openPrice"], r["highPrice"],
             r["lowPrice"], r["closePrice"], r.get("accumulatedTradingVolume") or 0) for r in rows]


def fetch_orderbook(code):
    return http_json(ORDERBOOK_URL.format(code=code), timeout=5)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


# ---------------------------------------------------------------- update
def parse_version(v):
    try:
        return tuple(int(x) for x in v.lstrip("vV").split("."))
    except ValueError:
        return (0,)


def check_latest_release():
    """최신 릴리스의 version.json 확인. 새 버전이면 {"version", "url", "size"} 반환, 아니면 None."""
    info = http_json(RELEASE_BASE + "version.json", timeout=10)
    if parse_version(info.get("version", "0")) <= parse_version(APP_VERSION):
        return None
    return {"version": info["version"], "url": RELEASE_BASE + "StockWidget.exe", "size": info.get("size")}


def install_update(info):
    """새 exe를 내려받아 실행 중인 exe와 교체.
    Windows는 실행 중인 exe를 덮어쓸 수는 없지만 이름 변경은 허용하므로:
    현재 exe → .old 로 이름 변경, 새 파일을 원래 이름으로 이동, 새 exe 실행."""
    exe = sys.executable
    new, old = exe + ".new", exe + ".old"
    req = urllib.request.Request(info["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(new, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    if info.get("size") and os.path.getsize(new) != info["size"]:
        os.remove(new)
        raise IOError("다운로드가 완전하지 않습니다")
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)  # 실패하면 원래대로 되돌림
        raise
    # PyInstaller 내부 환경변수를 물려주면 새 exe가 이전 버전의 임시 폴더를 재사용하려 하므로 초기화
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([exe, "--updated"], cwd=os.path.dirname(exe), env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def cleanup_old_exe():
    if FROZEN:
        try:
            os.remove(sys.executable + ".old")
        except OSError:
            pass


# ---------------------------------------------------------------- UI
class DarkScrollbar(tk.Canvas):
    """기본 tk.Scrollbar는 Windows 테마라 흰색으로 나오고 색을 바꿀 수 없어서 직접 그린 얇은 스크롤바."""

    def __init__(self, master, command, width=8):
        super().__init__(master, width=width, bg=BG, highlightthickness=0, bd=0)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self._grab = 0
        self.thumb = self.create_rectangle(0, 0, 0, 0, fill="#3a3f48", outline="")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: "break")
        self.bind("<Enter>", lambda e: self.itemconfig(self.thumb, fill="#5a606c"))
        self.bind("<Leave>", lambda e: self.itemconfig(self.thumb, fill="#3a3f48"))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def _draw(self):
        h, w = self.winfo_height(), self.winfo_width()
        y0 = self.first * h
        y1 = max(self.last * h, y0 + 16)
        self.coords(self.thumb, 2, y0, w - 2, min(y1, h))

    def _press(self, e):
        h = max(self.winfo_height(), 1)
        y0, y1 = self.first * h, self.last * h
        self._grab = e.y - y0 if y0 <= e.y <= y1 else (y1 - y0) / 2
        self._drag(e)
        return "break"  # 스크롤바를 잡아도 창이 움직이지 않게

    def _drag(self, e):
        self.command("moveto", (e.y - self._grab) / max(self.winfo_height(), 1))
        return "break"


class StockRow:
    def __init__(self, parent, stock):
        self.stock = stock
        self._shown = {}  # 라벨별로 마지막에 그린 값 — 바뀐 것만 다시 그림
        self.frame = tk.Frame(parent, bg=BG_ROW, padx=10, pady=6)
        self.frame.pack(fill="x", pady=(0, 2))
        self.frame.columnconfigure(0, weight=1)

        # 모든 라벨 크기를 고정(width=글자 수)해서 숫자가 바뀌어도 레이아웃·창 크기가 절대 변하지 않게 함
        self.name = tk.Label(self.frame, text=stock.get("name", stock["code"]), bg=BG_ROW, fg=FG,
                             font=(FONT, 10, "bold"), anchor="w", width=16)
        self.price = tk.Label(self.frame, text="-", bg=BG_ROW, fg=FLAT,
                              font=(FONT, 14, "bold"), anchor="e", width=10)
        self.volume = tk.Label(self.frame, text="거래량 -", bg=BG_ROW, fg=FG_DIM,
                               font=(FONT, 8), anchor="w", width=20)
        self.change = tk.Label(self.frame, text="", bg=BG_ROW, fg=FLAT,
                               font=(FONT, 9, "bold"), anchor="e", width=17)
        self.value = tk.Label(self.frame, text="거래대금 -", bg=BG_ROW, fg=FG_DIM,
                              font=(FONT, 8), anchor="w", width=20)
        self.name.grid(row=0, column=0, sticky="w")
        self.price.grid(row=0, column=1, sticky="e")
        self.volume.grid(row=1, column=0, sticky="w")
        self.change.grid(row=1, column=1, sticky="e")
        self.mcap = tk.Label(self.frame, text="", bg=BG_ROW, fg=FG_DIM,
                             font=(FONT, 8), anchor="e", width=22)
        self.value.grid(row=2, column=0, sticky="w")
        self.mcap.grid(row=2, column=1, sticky="e")
        self.widgets = [self.frame, self.name, self.price, self.volume, self.change, self.value, self.mcap]
        for w in self.widgets:
            w.config(cursor="hand2")  # 클릭하면 차트·호가 창

    def _set(self, label, **kw):
        if self._shown.get(label) != kw:
            self._shown[label] = kw
            label.config(**kw)

    def update(self, d):
        code = (d.get("compareToPreviousPrice") or {}).get("code", "3")
        if code in UP_CODES:
            color, arrow, sign = UP, "▲", "+"
        elif code in DOWN_CODES:
            color, arrow, sign = DOWN, "▼", "-"
        else:
            color, arrow, sign = FLAT, "-", ""
        if code in ("1", "4"):
            arrow = "↑" if code == "1" else "↓"  # 상한가/하한가

        price = d.get("closePrice", "-")
        diff = d.get("compareToPreviousClosePrice", "0").lstrip("-")
        ratio = d.get("fluctuationsRatio", "0").lstrip("-")
        name = d.get("stockName") or self.stock.get("name", self.stock["code"])
        if d.get("marketStatus") not in (None, "OPEN"):
            name += "  ·장마감"

        self._set(self.name, text=name)
        self._set(self.price, text=price, fg=color)
        self._set(self.change, text=f"{arrow} {diff}  {sign}{ratio}%", fg=color)
        self._set(self.volume, text=f'거래량 {d.get("accumulatedTradingVolume", "-")}')
        # 네이버가 이미 읽기 쉬운 단위로 줌 — 국내 "2조 7,216억", 해외 "167억 USD"
        self._set(self.value, text=f'거래대금 {d.get("accumulatedTradingValue") or "-"}')
        # 시총: 해외는 "4조 8,207억 USD" 형태로 오고, 국내는 원 단위 숫자만 와서 직접 조/억으로 변환
        mcap = d.get("marketValueHangeul") or korean_money(d.get("marketValueFullRaw"))
        self._set(self.mcap, text=f"시총 {mcap}" if mcap else "")

    def bind_all(self, seq, fn):
        for w in self.widgets:
            w.bind(seq, fn)


class StockWidget:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.rows = []
        self._editor = None
        self.wake = threading.Event()

        self.root = tk.Tk()
        self.root.title("GT Stock")
        try:  # 편집 창 등 모든 창에 GT Stock 아이콘
            self.root.iconbitmap(default=ICON_PATH)
        except tk.TclError:
            pass
        set_crisp_icon(self.root)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", self.cfg["topmost"])
        self.root.attributes("-alpha", self.cfg["alpha"])
        self.root.geometry(f'+{self.cfg["x"]}+{self.cfg["y"]}')
        self.root.minsize(270, 0)
        self.root.bind("<Map>", self.on_restore)

        self.outer = tk.Frame(self.root, bg=BG, padx=2, pady=2,
                              highlightthickness=1, highlightbackground="#2c3038")
        self.outer.pack(fill="both", expand=True)

        # 헤더
        self.header = tk.Frame(self.outer, bg=BG)
        self.header.pack(fill="x", pady=(0, 2))
        self.dot = tk.Label(self.header, text="●", bg=BG, fg=FG_DIM, font=(FONT, 7))
        self.dot.pack(side="left", padx=(6, 2))
        self.clock = tk.Label(self.header, text="연결 중…", bg=BG, fg=FG_DIM, font=(FONT, 8),
                              width=18, anchor="w")
        self.clock.pack(side="left")
        close_btn = tk.Label(self.header, text="✕", bg=BG, fg=FG_DIM, font=(FONT, 9), cursor="hand2")
        close_btn.pack(side="right", padx=(2, 6))
        close_btn.bind("<Button-1>", lambda e: self.quit())
        min_btn = tk.Label(self.header, text="－", bg=BG, fg=FG_DIM, font=(FONT, 9), cursor="hand2")
        min_btn.pack(side="right", padx=2)
        min_btn.bind("<Button-1>", lambda e: self.minimize())
        add_btn = tk.Label(self.header, text="＋", bg=BG, fg=FG_DIM, font=(FONT, 9), cursor="hand2")
        add_btn.pack(side="right", padx=2)
        add_btn.bind("<Button-1>", lambda e: self.open_editor())
        for b in (close_btn, min_btn, add_btn):
            b.bind("<Enter>", lambda e, w=b: w.config(fg=FG))
            b.bind("<Leave>", lambda e, w=b: w.config(fg=FG_DIM))

        # 투명도 슬라이더 (＋ 왼쪽)
        self.slider = tk.Canvas(self.header, width=SLIDER_W, height=16, bg=BG,
                                highlightthickness=0, cursor="hand2")
        self.slider.pack(side="right", padx=(0, 4))
        self.slider.bind("<ButtonPress-1>", self.on_slide)
        self.slider.bind("<B1-Motion>", self.on_slide)
        self.slider.bind("<ButtonRelease-1>", self.end_slide)
        self.slider.bind("<MouseWheel>", lambda e: self.set_alpha(self.cfg["alpha"] + (0.05 if e.delta > 0 else -0.05)))
        self.draw_slider()

        # 종목 목록: 캔버스 + 세로 스크롤바 (4개 이상이면 자동으로 스크롤 영역으로 전환)
        self.body_area = tk.Frame(self.outer, bg=BG)
        self.body_area.pack(fill="both", expand=True)
        self.body_canvas = tk.Canvas(self.body_area, bg=BG, highlightthickness=0, width=270)
        self.body_canvas.pack(side="left", fill="both", expand=True)
        self.body_scroll = DarkScrollbar(self.body_area, command=self.body_canvas.yview)
        self.body = tk.Frame(self.body_canvas, bg=BG)
        self._body_window = self.body_canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body_canvas.configure(yscrollcommand=self.body_scroll.set)
        self.body_canvas.bind("<Configure>", self._on_canvas_configure)
        self.body.bind("<Configure>", self._on_body_configure)
        self.body_canvas.bind("<MouseWheel>", self._on_wheel)
        self.body.bind("<MouseWheel>", self._on_wheel)

        # 창 테두리 어디서든 크기 조절: 마우스가 가장자리 근처면 커서를 바꾸고, 누르면 이동 대신 크기 조절
        self._rz = None
        self._press_at = None
        self._cursor_widget = None
        self.root.bind_all("<Motion>", self._edge_motion, add="+")
        self._details = {}

        for w in (self.outer, self.header, self.dot, self.clock, self.body, self.body_canvas):
            self._bind_common(w)

        # 새 버전이 있을 때만 나타나는 알림 바
        self.update_info = None
        self.update_bar = tk.Label(self.outer, text="", bg="#2b3a55", fg=FG, font=(FONT, 9, "bold"),
                                   pady=4, cursor="hand2")
        self.update_bar.bind("<Button-1>", lambda e: self.do_update())

        self.build_rows()
        self.root.after(50, self.keep_on_screen)
        threading.Thread(target=self.worker, daemon=True).start()
        self.root.after(200, self.drain_queue)
        if FROZEN:
            cleanup_old_exe()
            self.root.after(10000, cleanup_old_exe)  # 업데이트 직후엔 이전 프로세스가 종료될 때까지 잠겨 있을 수 있음
            self.root.after(3000, self.check_update)
        if "--updated" in sys.argv:
            self.clock.config(text=f"v{APP_VERSION} 업데이트 완료")

    # ---- 드래그 / 메뉴
    def _bind_common(self, w):
        w.bind("<ButtonPress-1>", self.start_drag)
        w.bind("<B1-Motion>", self.on_drag)
        w.bind("<ButtonRelease-1>", self.end_drag)
        w.bind("<Button-3>", self.show_menu)

    def start_drag(self, e):
        self._press_at = (e.x_root, e.y_root)
        zone = self._edge_zone(e)
        if zone:
            self._rz = (zone, e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y(),
                        self.body_canvas.winfo_width(), self.body_canvas.winfo_height())
            return
        self._rz = None
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def on_drag(self, e):
        if self._rz:
            self._resize_drag(e)
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def end_drag(self, e):
        self._rz = None
        self.save_config()

    def _edge_zone(self, e):
        """마우스가 창 가장자리 EDGE px 안이면 'n','se' 같은 방향 문자열, 아니면 ''."""
        if e.widget.winfo_toplevel() is not self.root:
            return ""
        x, y = e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        return (("n" if y < EDGE else "s" if y >= h - EDGE else "") +
                ("w" if x < EDGE else "e" if x >= w - EDGE else ""))

    def _edge_motion(self, e):
        if self._rz or not isinstance(e.widget, tk.Misc):
            return
        zone = self._edge_zone(e)
        cursor = {"n": "size_ns", "s": "size_ns", "w": "size_we", "e": "size_we",
                  "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}.get(zone)
        prev = self._cursor_widget
        if prev and (prev[0] is not e.widget or not cursor):
            try:
                prev[0].config(cursor=prev[1])
            except tk.TclError:
                pass
            self._cursor_widget = None
        if cursor and not self._cursor_widget:
            try:
                self._cursor_widget = (e.widget, e.widget.cget("cursor"))
                e.widget.config(cursor=cursor)
            except tk.TclError:
                self._cursor_widget = None

    def _resize_drag(self, e):
        zone, x0r, y0r, wx, wy, cw, ch = self._rz
        dx, dy = e.x_root - x0r, e.y_root - y0r
        w = cw + dx if "e" in zone else cw - dx if "w" in zone else cw
        h = ch + dy if "s" in zone else ch - dy if "n" in zone else ch
        w, h = max(MIN_W, w), max(MIN_H, h)
        x = wx + (cw - w) if "w" in zone else wx   # 왼쪽/위쪽을 잡으면 반대편은 고정되도록 창 위치도 이동
        y = wy + (ch - h) if "n" in zone else wy
        self.cfg["win_w"], self.cfg["win_h"] = w, h
        self.body_canvas.configure(width=w, height=h)
        self.root.geometry(f"+{x}+{y}")
        self.body.update_idletasks()
        self._show_scrollbar(self.body.winfo_reqheight() > h + 1)

    def keep_on_screen(self):
        """모니터 연결이 바뀌어 창이 화면 밖에 있으면 주 모니터로 데려옴 (듀얼 모니터 지원)."""
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        try:
            import ctypes
            import ctypes.wintypes
            pt = ctypes.wintypes.POINT(x + 40, y + 20)
            if ctypes.windll.user32.MonitorFromPoint(pt, 0):  # 0 = MONITOR_DEFAULTTONULL: 어느 모니터 위에도 없으면 NULL
                return
        except Exception:
            return
        self.root.geometry("+100+100")

    def show_menu(self, e):
        m = tk.Menu(self.root, tearoff=0, font=(FONT, 9))
        m.add_command(label="종목 목록 편집 (추가·삭제·순서)…", command=self.open_editor)
        m.add_separator()

        am = tk.Menu(m, tearoff=0, font=(FONT, 9))
        av = tk.DoubleVar(value=self.cfg["alpha"])
        for a in (1.0, 0.92, 0.8, 0.65, 0.5):
            am.add_radiobutton(label=f"{int(a * 100)}%", value=a, variable=av,
                               command=lambda a=a: self.set_alpha(a))
        m.add_cascade(label="투명도", menu=am)

        im = tk.Menu(m, tearoff=0, font=(FONT, 9))
        iv = tk.IntVar(value=self.cfg["interval"])
        for sec in (1, 3, 5, 10, 30):
            im.add_radiobutton(label=f"{sec}초", value=sec, variable=iv,
                               command=lambda s=sec: self.set_interval(s))
        m.add_cascade(label="갱신 주기", menu=im)
        self._menu_vars = (av, iv)

        tv = tk.BooleanVar(value=self.cfg["topmost"])
        m.add_checkbutton(label="항상 위에 표시", variable=tv, command=self.toggle_topmost)
        self._menu_vars += (tv,)
        m.add_command(label="지금 새로고침", command=self.wake.set)
        m.add_separator()
        m.add_command(label=f"업데이트 확인  (현재 v{APP_VERSION})",
                      command=lambda: self.check_update(manual=True))
        m.add_command(label="종료", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def set_alpha(self, a, save=True):
        a = round(min(max(a, ALPHA_MIN), 1.0), 2)
        self.cfg["alpha"] = a
        self.root.attributes("-alpha", a)
        self.draw_slider()
        if save:
            self.save_config()

    # ---- 투명도 슬라이더
    def draw_slider(self):
        c = self.slider
        c.delete("all")
        x0, x1, y = 6, SLIDER_W - 6, 8
        kx = x0 + (self.cfg["alpha"] - ALPHA_MIN) / (1 - ALPHA_MIN) * (x1 - x0)
        c.create_line(x0, y, x1, y, fill="#3a3f48", width=3, capstyle="round")
        c.create_line(x0, y, kx, y, fill=FG_DIM, width=3, capstyle="round")
        c.create_oval(kx - 5, y - 5, kx + 5, y + 5, fill=FG, outline="")

    def on_slide(self, e):
        x0, x1 = 6, SLIDER_W - 6
        t = min(max((e.x - x0) / (x1 - x0), 0), 1)
        self.set_alpha(ALPHA_MIN + t * (1 - ALPHA_MIN), save=False)
        set_if_changed(self.clock, text=f'투명도 {int(self.cfg["alpha"] * 100)}%')
        return "break"  # 슬라이더 조작 중에는 창이 움직이지 않도록 이벤트 전파 차단

    def end_slide(self, e):
        self.save_config()
        return "break"

    def set_interval(self, s):
        self.cfg["interval"] = s
        self.save_config()
        self.wake.set()

    def toggle_topmost(self):
        self.cfg["topmost"] = not self.cfg["topmost"]
        self.root.attributes("-topmost", self.cfg["topmost"])
        self.save_config()

    # ---- 종목 목록 스크롤 / 창 크기 조절
    def _on_canvas_configure(self, e):
        self.body_canvas.itemconfig(self._body_window, width=e.width)

    def _on_body_configure(self, e):
        self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))

    def _on_wheel(self, e):
        if self.body_scroll.winfo_ismapped():
            self.body_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    def _show_scrollbar(self, show):
        if show and not self.body_scroll.winfo_ismapped():
            self.body_scroll.pack(side="right", fill="y")
        elif not show and self.body_scroll.winfo_ismapped():
            self.body_scroll.pack_forget()

    def relayout_body(self):
        """종목이 4개 이상이면 기본적으로 3개 높이만 보여주고 스크롤, 그 전에는 꽉 차게 자동 맞춤.
        사용자가 손잡이로 직접 크기를 조절했으면(win_w/win_h) 그 값을 그대로 우선한다."""
        self.body.update_idletasks()
        content_h = max(self.body.winfo_reqheight(), 1)
        rows = max(len(self.rows), 1)
        if self.cfg.get("win_h"):
            canvas_h = self.cfg["win_h"]
        elif rows >= 4:
            canvas_h = max(int(content_h * 3 / rows), 1)
        else:
            canvas_h = content_h
        canvas_w = self.cfg.get("win_w") or 270
        set_if_changed(self.body_canvas, height=canvas_h, width=canvas_w)
        self._show_scrollbar(content_h > canvas_h + 1)

    # ---- 종목 관리
    def build_rows(self):
        for r in self.rows:
            r.frame.destroy()
        self.rows = []
        if getattr(self, "_empty", None):
            self._empty.destroy()
            self._empty = None
        if not self.cfg["stocks"]:
            lbl = tk.Label(self.body, text="우클릭 또는 ＋ 로 종목을 추가하세요", bg=BG, fg=FG_DIM,
                           font=(FONT, 9), padx=12, pady=10)
            lbl.pack()
            self._bind_common(lbl)
            self._empty = lbl
            self.relayout_body()
            return
        for s in self.cfg["stocks"]:
            row = StockRow(self.body, s)
            row.bind_all("<ButtonPress-1>", self.start_drag)
            row.bind_all("<B1-Motion>", self.on_drag)
            row.bind_all("<ButtonRelease-1>", lambda e, st=s: self.row_release(e, st))
            row.bind_all("<Button-3>", self.show_menu)
            row.bind_all("<MouseWheel>", self._on_wheel)
            self.rows.append(row)
        self.relayout_body()
        self.wake.set()

    def row_release(self, e, stock):
        was_resize = bool(self._rz)
        self.end_drag(e)
        if was_resize or not self._press_at:
            return
        if abs(e.x_root - self._press_at[0]) < 4 and abs(e.y_root - self._press_at[1]) < 4:
            self.open_detail(stock)

    def open_detail(self, stock):
        key = (stock["type"], stock["code"])
        d = self._details.get(key)
        if d and d.win.winfo_exists():
            d.win.deiconify()
            d.win.lift()
            return
        self._details[key] = DetailWindow(self, stock)

    def set_stocks(self, stocks):
        """편집 창에서 바뀐 목록을 바로 위젯에 반영."""
        self.cfg["stocks"] = stocks
        self.save_config()
        self.build_rows()

    def open_editor(self):
        if self._editor and self._editor.win.winfo_exists():
            self._editor.win.lift()
            self._editor.entry.focus_set()
            return
        self._editor = EditDialog(self)

    # ---- 데이터 갱신 (백그라운드 스레드 → 큐 → UI)
    def worker(self):
        while True:
            stocks = list(self.cfg["stocks"])
            if stocks:
                try:
                    self.q.put(("ok", fetch_quotes(stocks)))
                except Exception as ex:  # 네트워크 오류 등
                    self.q.put(("err", str(ex)))
            self.wake.wait(self.cfg["interval"])
            self.wake.clear()

    def drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "ok":
                    for r in self.rows:
                        d = payload.get((r.stock["type"], r.stock["code"]))
                        if d:
                            r.update(d)
                    set_if_changed(self.dot, fg="#3ecf6e")
                    set_if_changed(self.clock, text=datetime.now().strftime("%H:%M:%S"))
                elif kind == "update":
                    self.on_update_result(*payload)
                elif kind == "updated":
                    # 새 exe가 이미 실행됨 → 설정 저장 후 확실히 종료 (남은 스레드가 있어도 대기하지 않음)
                    self.save_config()
                    os._exit(0)
                elif kind == "update_fail":
                    self.update_bar.config(text="업데이트 실패 — 클릭해서 다시 시도", cursor="hand2")
                else:
                    set_if_changed(self.dot, fg=UP)
                    set_if_changed(self.clock, text="연결 오류 · 재시도 중")
        except queue.Empty:
            pass
        self.root.after(200, self.drain_queue)

    # ---- 업데이트
    def check_update(self, manual=False):
        def run():
            try:
                self.q.put(("update", (check_latest_release(), manual)))
            except Exception:
                self.q.put(("update", (None, manual)))
        threading.Thread(target=run, daemon=True).start()
        if not manual:
            self.root.after(UPDATE_CHECK_MINUTES * 60 * 1000, self.check_update)

    def on_update_result(self, info, manual):
        if info:
            # 새 버전이 있으면 묻지 않고 바로 받아서 교체·재시작
            self.update_info = info
            if not self.update_bar.winfo_ismapped():
                self.update_bar.pack(fill="x", pady=(0, 2), before=self.body_area)
            self.do_update()
        elif manual:
            set_if_changed(self.clock, text=f"최신 버전입니다 (v{APP_VERSION})")

    def do_update(self):
        if not self.update_info:
            return
        if not FROZEN:
            self.update_bar.config(text="개발용(.pyw) 실행 중 — 업데이트는 exe에서만 동작")
            return
        self.update_bar.config(text=f"v{self.update_info['version']} 업데이트 중…", cursor="watch")
        info = self.update_info

        def run():
            try:
                install_update(info)
                self.q.put(("updated", None))
            except Exception as ex:
                self.q.put(("update_fail", str(ex)))
        threading.Thread(target=run, daemon=True).start()

    # ---- 저장 / 종료
    def save_config(self):
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def minimize(self):
        """overrideredirect 창은 작업표시줄 항목이 없어 기본 iconify가 깨지므로,
        최소화 직전에 잠깐 해제했다가 복구(<Map>) 시 다시 걸어준다."""
        self.save_config()
        self.root.overrideredirect(False)
        self.root.iconify()

    def on_restore(self, e):
        if e.widget is self.root and self.root.state() == "normal":
            self.root.overrideredirect(True)

    def quit(self):
        self.save_config()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class EditDialog:
    """종목 목록 편집: 위쪽은 내 종목(순서 변경·삭제), 아래쪽은 검색해서 추가. 변경은 즉시 반영."""

    def __init__(self, widget):
        self.widget = widget
        self.stocks = [dict(s) for s in widget.cfg["stocks"]]
        self.results = []
        self._drag_from = None

        master = widget.root
        self.win = tk.Toplevel(master, bg=BG, padx=12, pady=10)
        self.win.title("종목 목록 편집")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        set_crisp_icon(self.win)
        dark_titlebar(self.win)
        # 위젯 옆에 띄우기 (왼쪽 공간이 있으면 왼쪽, 아니면 오른쪽)
        mx, my = master.winfo_x(), master.winfo_y()
        sx = mx - 380 if mx - 380 > master.winfo_vrootx() else mx + master.winfo_width() + 8
        self.win.geometry(f"+{sx}+{my}")
        self.win.bind("<Escape>", lambda e: self.win.destroy())

        lb_opts = dict(font=(FONT, 10), bg=BG_ROW, fg=FG, selectbackground="#3a4252",
                       selectforeground=FG, relief="flat", highlightthickness=0,
                       activestyle="none", width=34, exportselection=False)
        btn_opts = dict(font=(FONT, 9), width=6, bg="#2a2e36", fg=FG, activebackground="#3a4252",
                        activeforeground=FG, relief="flat", bd=0, cursor="hand2")

        # ---- 내 종목
        tk.Label(self.win, text="내 종목  (드래그하거나 ▲▼로 순서 변경, Delete 키로 삭제)",
                 bg=BG, fg=FG_DIM, font=(FONT, 9)).pack(anchor="w")
        top = tk.Frame(self.win, bg=BG)
        top.pack(fill="x", pady=(4, 12))
        self.mine = tk.Listbox(top, height=7, **lb_opts)
        self.mine.pack(side="left")
        self.mine.bind("<ButtonPress-1>", self.drag_start)
        self.mine.bind("<B1-Motion>", self.drag_move)
        self.mine.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_from", None))
        self.mine.bind("<Delete>", lambda e: self.remove())
        side = tk.Frame(top, bg=BG)
        side.pack(side="left", fill="y", padx=(6, 0))
        tk.Button(side, text="▲", command=lambda: self.move(-1), **btn_opts).pack(pady=(0, 4))
        tk.Button(side, text="▼", command=lambda: self.move(1), **btn_opts).pack(pady=(0, 4))
        tk.Button(side, text="삭제", command=self.remove, **dict(btn_opts, fg=UP)).pack(side="bottom")

        # ---- 검색해서 추가
        tk.Label(self.win, text="종목 추가  (종목명 / 코드 / 티커 — 예: 카카오, 000660, TSLA)",
                 bg=BG, fg=FG_DIM, font=(FONT, 9)).pack(anchor="w")
        row = tk.Frame(self.win, bg=BG)
        row.pack(fill="x", pady=(4, 4))
        self.entry = tk.Entry(row, font=(FONT, 11), bg=BG_ROW, fg=FG, insertbackground=FG,
                              relief="flat", width=28)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda e: self.search())
        tk.Button(row, text="검색", command=self.search, **btn_opts).pack(side="left", padx=(6, 0))

        bottom = tk.Frame(self.win, bg=BG)
        bottom.pack(fill="x")
        self.found = tk.Listbox(bottom, height=6, **lb_opts)
        self.found.pack(side="left")
        self.found.bind("<Double-Button-1>", lambda e: self.add())
        self.found.bind("<Return>", lambda e: self.add())
        side2 = tk.Frame(bottom, bg=BG)
        side2.pack(side="left", fill="y", padx=(6, 0))
        tk.Button(side2, text="추가", command=self.add, **btn_opts).pack()

        self.status = tk.Label(self.win, text="", bg=BG, fg=FG_DIM, font=(FONT, 8), anchor="w")
        self.status.pack(fill="x", pady=(6, 0))

        self.refresh_mine()
        self.entry.focus_set()

    # ---- 내 종목 목록
    def refresh_mine(self, select=None):
        self.mine.delete(0, "end")
        for i, s in enumerate(self.stocks, 1):
            market = "국내" if s["type"] == "domestic" else "해외"
            self.mine.insert("end", f'{i}.  {s.get("name", s["code"])}   · {market} {s["code"]}')
        if select is not None and self.stocks:
            select = min(max(select, 0), len(self.stocks) - 1)
            self.mine.selection_set(select)
            self.mine.see(select)

    def apply(self, select=None):
        self.refresh_mine(select)
        self.widget.set_stocks([dict(s) for s in self.stocks])

    def selected(self):
        sel = self.mine.curselection()
        return sel[0] if sel else None

    def move(self, d):
        i = self.selected()
        if i is None or not (0 <= i + d < len(self.stocks)):
            return
        self.stocks[i], self.stocks[i + d] = self.stocks[i + d], self.stocks[i]
        self.apply(i + d)

    def remove(self):
        i = self.selected()
        if i is None:
            self.status.config(text="삭제할 종목을 위 목록에서 선택하세요")
            return
        name = self.stocks.pop(i).get("name")
        self.apply(i)
        self.status.config(text=f"'{name}' 삭제됨")

    def drag_start(self, e):
        self._drag_from = self.mine.nearest(e.y) if self.stocks else None

    def drag_move(self, e):
        if self._drag_from is None:
            return
        to = self.mine.nearest(e.y)
        if to != self._drag_from:
            self.stocks.insert(to, self.stocks.pop(self._drag_from))
            self._drag_from = to
            self.apply(to)

    # ---- 검색 / 추가
    def search(self):
        q = self.entry.get().strip()
        if not q:
            return
        self.status.config(text="검색 중…")
        self.win.update_idletasks()
        try:
            self.results = search_stocks(q)
        except Exception:
            self.results = []
            self.status.config(text="검색 실패 — 인터넷 연결을 확인하세요")
            return
        self.found.delete(0, "end")
        for r in self.results:
            self.found.insert("end", r["label"])
        self.status.config(text=f"검색 결과 {len(self.results)}건 — 더블클릭 또는 [추가]"
                           if self.results else "검색 결과 없음")
        if self.results:
            self.found.selection_set(0)
            self.found.focus_set()

    def add(self):
        sel = self.found.curselection()
        if not sel:
            self.search()
            return
        item = self.results[sel[0]]
        if any(s["code"] == item["code"] and s["type"] == item["type"] for s in self.stocks):
            self.status.config(text=f"'{item['name']}' 은(는) 이미 목록에 있습니다")
            return
        self.stocks.append({"code": item["code"], "type": item["type"], "name": item["name"]})
        self.apply(len(self.stocks) - 1)
        self.status.config(text=f"'{item['name']}' 추가됨")


class DetailWindow:
    """종목 클릭 시 뜨는 창: 실시간 차트(당일 1분 / 일봉) + 호가(국내). 2초마다 갱신."""
    BOOK_W = 210
    ROW_H = 24

    def __init__(self, widget, stock):
        self.widget, self.stock = widget, stock
        self.domestic = stock["type"] == "domestic"
        self.mode = "minute" if self.domestic else "day"   # 해외는 네이버가 분봉을 주지 않음
        self.q = queue.Queue()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.chart_due = True
        self.quote, self.book, self.series = None, None, []
        self.prev_close = None

        master = widget.root
        self.win = tk.Toplevel(master, bg=BG)
        self.win.title(f'{stock.get("name", stock["code"])} · 차트/호가')
        self.win.attributes("-topmost", True)
        self.win.minsize(520, 320)
        set_crisp_icon(self.win)
        dark_titlebar(self.win)
        w, h = (700, 400) if self.domestic else (560, 380)
        mx, my = master.winfo_x(), master.winfo_y()
        sx = mx - w - 12 if mx - w - 12 > master.winfo_vrootx() else mx + master.winfo_width() + 12
        self.win.geometry(f"{w}x{h}+{sx}+{my}")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())

        # ---- 상단: 종목명 / 현재가 / 등락 / 차트 기간 버튼
        top = tk.Frame(self.win, bg=BG, padx=12, pady=8)
        top.pack(fill="x")
        self.title = tk.Label(top, text=stock.get("name", stock["code"]), bg=BG, fg=FG, font=(FONT, 12, "bold"))
        self.title.pack(side="left")
        self.price = tk.Label(top, text="-", bg=BG, fg=FLAT, font=(FONT, 16, "bold"))
        self.price.pack(side="left", padx=(12, 6))
        self.change = tk.Label(top, text="", bg=BG, fg=FLAT, font=(FONT, 10, "bold"))
        self.change.pack(side="left")
        self.mode_btns = {}
        modes = [("day", "일봉")] + ([("minute", "당일")] if self.domestic else [])
        for key, label in modes:
            b = tk.Label(top, text=label, bg=BG_ROW, fg=FG_DIM, font=(FONT, 9), padx=10, pady=2, cursor="hand2")
            b.pack(side="right", padx=(4, 0))
            b.bind("<Button-1>", lambda e, k=key: self.set_mode(k))
            self.mode_btns[key] = b
        self._paint_mode_buttons()

        # ---- 본문: 차트(왼쪽, 늘어남) + 호가(오른쪽, 고정 폭)
        body = tk.Frame(self.win, bg=BG, padx=8)
        body.pack(fill="both", expand=True)
        if self.domestic:
            self.book_cv = tk.Canvas(body, width=self.BOOK_W, bg=BG, highlightthickness=0)
            self.book_cv.pack(side="right", fill="y", padx=(8, 0))
            self.book_cv.bind("<Configure>", lambda e: self.draw_book())
        self.chart = tk.Canvas(body, bg=BG_ROW, highlightthickness=0, cursor="crosshair")
        self.chart.pack(side="left", fill="both", expand=True)
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        self.chart.bind("<Motion>", self.on_hover)
        self.chart.bind("<Leave>", lambda e: self.chart.delete("hover"))

        self.status = tk.Label(self.win, text="불러오는 중…", bg=BG, fg=FG_DIM, font=(FONT, 8), anchor="w", padx=12)
        self.status.pack(fill="x", pady=(4, 6))

        threading.Thread(target=self.worker, daemon=True).start()
        self.win.after(150, self.drain)

    # ---- 데이터 (백그라운드)
    def worker(self):
        key = (self.stock["type"], self.stock["code"])
        last_chart = 0
        while not self.stop.is_set():
            try:
                d = fetch_quotes([self.stock]).get(key)
                if d:
                    self.q.put(("quote", d))
            except Exception:
                self.q.put(("err", None))
            if self.domestic:
                try:
                    self.q.put(("book", fetch_orderbook(self.stock["code"])))
                except Exception:
                    pass
            # 차트는 15초마다 (1분봉이라 더 자주 받을 필요 없음 — 마지막 봉은 현재가로 실시간 갱신)
            if self.chart_due or time.time() - last_chart > 15:
                self.chart_due = False
                mode = self.mode
                try:
                    self.q.put(("chart", (mode, fetch_chart(self.stock, mode))))
                except Exception:
                    pass
                last_chart = time.time()
            self.wake.wait(2)
            self.wake.clear()

    def drain(self):
        if self.stop.is_set():
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "quote":
                    self.on_quote(payload)
                elif kind == "book":
                    self.book = payload
                    self.draw_book()
                elif kind == "chart":
                    mode, series = payload
                    if mode == self.mode:
                        self.series = series
                        self.draw_chart()
                else:
                    self.status.config(text="연결 오류 · 재시도 중")
        except queue.Empty:
            pass
        self.win.after(200, self.drain)

    def on_quote(self, d):
        self.quote = d
        code = (d.get("compareToPreviousPrice") or {}).get("code", "3")
        color = UP if code in UP_CODES else DOWN if code in DOWN_CODES else FLAT
        arrow = "▲" if code in UP_CODES else "▼" if code in DOWN_CODES else "-"
        sign = "+" if code in UP_CODES else "-" if code in DOWN_CODES else ""
        diff = d.get("compareToPreviousClosePrice", "0").lstrip("-")
        ratio = d.get("fluctuationsRatio", "0").lstrip("-")
        set_if_changed(self.title, text=d.get("stockName") or self.stock.get("name", ""))
        set_if_changed(self.price, text=d.get("closePrice", "-"), fg=color)
        set_if_changed(self.change, text=f"{arrow} {diff}  {sign}{ratio}%", fg=color)
        cur = to_num(d.get("closePrice"))
        delta = to_num(diff) * (-1 if code in DOWN_CODES else 1)
        self.prev_close = cur - delta
        # 차트 마지막 봉을 현재가로 갱신해 차트도 실시간으로 움직이게
        if self.series and self.mode == "minute":
            lab, o, h, l, c, v = self.series[-1]
            self.series[-1] = (lab, o, max(h, cur), min(l, cur), cur, v)
            self.draw_chart()
        status = "장중" if d.get("marketStatus") == "OPEN" else "장마감"
        self.status.config(text=f'{status} · 업데이트 {datetime.now().strftime("%H:%M:%S")}'
                                + ("" if self.domestic else "   ·   해외 종목은 일봉 차트만 제공되고 호가 정보는 없습니다"))

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        self.series = []
        self._paint_mode_buttons()
        self.draw_chart()
        self.chart_due = True
        self.wake.set()

    def _paint_mode_buttons(self):
        for k, b in self.mode_btns.items():
            b.config(bg="#3a4252" if k == self.mode else BG_ROW, fg=FG if k == self.mode else FG_DIM)

    def fmt(self, v):
        return f"{v:,.0f}" if self.domestic else f"{v:,.2f}"

    # ---- 차트
    def draw_chart(self):
        cv = self.chart
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        if not self.series:
            cv.create_text(W / 2, H / 2, text="차트 불러오는 중…", fill=FG_DIM, font=(FONT, 9))
            return
        L, R, T, B = 8, 64, 12, 20                 # 여백 (오른쪽은 가격 눈금)
        vol_h = (H - T - B) * 0.18                   # 아래쪽 거래량 영역
        ph = H - T - B - vol_h - 6                   # 가격 영역 높이
        pw = W - L - R
        data = self.series
        n = len(data)
        lows = [r[3] for r in data]
        highs = [r[2] for r in data]
        lo, hi = min(lows), max(highs)
        if self.mode == "minute" and self.prev_close:
            lo, hi = min(lo, self.prev_close), max(hi, self.prev_close)
        pad = (hi - lo) * 0.08 or hi * 0.01 or 1
        lo, hi = lo - pad, hi + pad
        y_of = lambda v: T + (hi - v) / (hi - lo) * ph
        step = pw / max(n, 1)
        x_of = lambda i: L + step * (i + 0.5)
        self._geom = (L, step, n)

        # 가격 눈금 + 가로 격자
        for k in range(5):
            v = lo + (hi - lo) * k / 4
            y = y_of(v)
            cv.create_line(L, y, L + pw, y, fill="#262a33")
            cv.create_text(W - R + 6, y, text=self.fmt(v), anchor="w", fill=FG_DIM, font=(FONT, 8))
        # 시간 눈금
        for k in range(0, n, max(n // 5, 1)):
            cv.create_text(x_of(k), H - B + 10, text=data[k][0], fill=FG_DIM, font=(FONT, 8),
                           anchor="w" if k == 0 else "center")  # 첫 라벨이 왼쪽 끝에서 잘리지 않게

        # 거래량 막대
        vmax = max((r[5] for r in data), default=0) or 1
        vb = T + ph + 6 + vol_h
        for i, r in enumerate(data):
            up = r[4] >= r[1]
            vh = r[5] / vmax * vol_h
            x = x_of(i)
            cv.create_rectangle(x - max(step * 0.35, 0.5), vb - vh, x + max(step * 0.35, 0.5), vb,
                                fill=mix(BG_ROW, UP if up else DOWN, 0.45), outline="")

        if self.mode == "minute":
            last = data[-1][4]
            base = self.prev_close or data[0][1]
            color = UP if last >= base else DOWN
            pts = [(x_of(i), y_of(r[4])) for i, r in enumerate(data)]
            if self.prev_close:   # 전일 종가 점선
                yb = y_of(self.prev_close)
                cv.create_line(L, yb, L + pw, yb, fill=FG_DIM, dash=(3, 3))
                cv.create_text(L + 4, yb - 8, text=f"전일 {self.fmt(self.prev_close)}", anchor="w",
                               fill=FG_DIM, font=(FONT, 7))
            area = [(pts[0][0], T + ph)] + pts + [(pts[-1][0], T + ph)]
            cv.create_polygon(*[c for p in area for c in p], fill=mix(BG_ROW, color, 0.18), outline="")
            if len(pts) > 1:
                cv.create_line(*[c for p in pts for c in p], fill=color, width=1.6)
            cv.create_oval(pts[-1][0] - 3, pts[-1][1] - 3, pts[-1][0] + 3, pts[-1][1] + 3, fill=color, outline="")
        else:
            bw = max(step * 0.6, 1)
            for i, (lab, o, h, l, c, v) in enumerate(data):
                color = UP if c >= o else DOWN
                x = x_of(i)
                cv.create_line(x, y_of(h), x, y_of(l), fill=color)
                y1, y2 = sorted((y_of(o), y_of(c)))
                cv.create_rectangle(x - bw / 2, y1, x + bw / 2, max(y2, y1 + 1), fill=color, outline=color)
        # 현재가 표시 (오른쪽 눈금 위)
        last = data[-1][4]
        yl = y_of(last)
        lc = UP if (self.prev_close and last >= self.prev_close) else DOWN if self.prev_close else FLAT
        cv.create_rectangle(W - R + 2, yl - 8, W - 2, yl + 8, fill=lc, outline="")
        cv.create_text(W - R + 6, yl, text=self.fmt(last), anchor="w", fill="white", font=(FONT, 8, "bold"))

    def on_hover(self, e):
        cv = self.chart
        cv.delete("hover")
        if not self.series or not hasattr(self, "_geom"):
            return
        L, step, n = self._geom
        i = int((e.x - L) // step)
        if not 0 <= i < n:
            return
        lab, o, h, l, c, v = self.series[i]
        x = L + step * (i + 0.5)
        cv.create_line(x, 0, x, cv.winfo_height(), fill="#4a505c", dash=(2, 2), tags="hover")
        if self.mode == "minute":
            text = f"{lab}   {self.fmt(c)}   거래량 {v:,.0f}"
        else:
            text = f"{lab}   시 {self.fmt(o)}  고 {self.fmt(h)}  저 {self.fmt(l)}  종 {self.fmt(c)}   거래량 {v:,.0f}"
        t = cv.create_text(10, 6, text=text, anchor="nw", fill=FG, font=(FONT, 8), tags="hover")
        x0, y0, x1, y1 = cv.bbox(t)
        bg = cv.create_rectangle(x0 - 4, y0 - 2, x1 + 4, y1 + 2, fill="#2c313c", outline="", tags="hover")
        cv.tag_raise(t, bg)

    # ---- 호가 (매도 5단계 위 · 매수 5단계 아래, 잔량 막대)
    def draw_book(self):
        if not self.domestic:
            return
        cv = self.book_cv
        cv.delete("all")
        W = self.BOOK_W
        rh = self.ROW_H
        cv.create_text(W * 0.18, 10, text="매도잔량", fill=FG_DIM, font=(FONT, 8))
        cv.create_text(W * 0.5, 10, text="호가", fill=FG_DIM, font=(FONT, 8))
        cv.create_text(W * 0.82, 10, text="매수잔량", fill=FG_DIM, font=(FONT, 8))
        if not self.book:
            cv.create_text(W / 2, 80, text="호가 불러오는 중…", fill=FG_DIM, font=(FONT, 9))
            return
        sells = self.book.get("sellInfo") or []
        buys = self.book.get("buyInfos") or []
        prev = self.book.get("lastClosePrice") or self.prev_close
        cur = to_num(self.quote.get("closePrice")) if self.quote else None
        y = 22
        rows = [("sell", r) for r in sells] + [("buy", r) for r in buys]
        for side, r in rows:
            price = to_num(r["price"])
            rate = min(max(r.get("rate") or 0, 0), 100) / 100
            bg = mix(BG, DOWN, 0.10) if side == "sell" else mix(BG, UP, 0.10)
            cv.create_rectangle(0, y, W, y + rh - 1, fill=bg, outline="")
            pc = UP if prev and price > prev else DOWN if prev and price < prev else FG
            if side == "sell":   # 매도 잔량은 왼쪽 칸에 오른쪽→왼쪽 막대
                bw = W * 0.34 * rate
                cv.create_rectangle(W * 0.34 - bw, y + 4, W * 0.34, y + rh - 5, fill=mix(BG, DOWN, 0.45), outline="")
                cv.create_text(W * 0.32, y + rh / 2, text=r["count"], anchor="e", fill=FG, font=(FONT, 8))
            else:                # 매수 잔량은 오른쪽 칸에 왼쪽→오른쪽 막대
                bw = W * 0.34 * rate
                cv.create_rectangle(W * 0.66, y + 4, W * 0.66 + bw, y + rh - 5, fill=mix(BG, UP, 0.45), outline="")
                cv.create_text(W * 0.68, y + rh / 2, text=r["count"], anchor="w", fill=FG, font=(FONT, 8))
            cv.create_text(W * 0.5, y + rh / 2, text=r["price"], fill=pc, font=(FONT, 9, "bold"))
            if cur and abs(price - cur) < 1e-9:  # 현재가 칸 테두리
                cv.create_rectangle(W * 0.34 + 1, y + 1, W * 0.66 - 1, y + rh - 2, outline=FG)
            y += rh
            if side == "sell" and r is sells[-1]:
                cv.create_line(0, y, W, y, fill="#4a505c")
        y += 6
        cv.create_text(W * 0.18, y + 8, text=self.book.get("totalSell", "-"), fill=DOWN, font=(FONT, 8, "bold"))
        cv.create_text(W * 0.5, y + 8, text="총잔량", fill=FG_DIM, font=(FONT, 8))
        cv.create_text(W * 0.82, y + 8, text=self.book.get("totalBuy", "-"), fill=UP, font=(FONT, 8, "bold"))

    def close(self):
        self.stop.set()
        self.wake.set()
        self.win.destroy()


if __name__ == "__main__":
    try:  # 고해상도 모니터에서 글자 선명하게
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # 작업표시줄에서 python 아이콘 대신 GT Stock 아이콘으로 표시 (GT Task Manager와 같은 방식)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GlobalTechnologies.GTStock.1")
    except Exception:
        pass
    StockWidget().run()
