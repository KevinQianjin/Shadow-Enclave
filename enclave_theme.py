"""Pink, DPI-scaled Tk styles and the Carrier Studio illustration."""

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


BG, PANEL, INPUT = "#FFF1F7", "#FFF9FC", "#FFFFFF"
TEXT, MUTED, ACCENT = "#512840", "#775068", "#F5B2D1"
BORDER, FOCUS, SELECTION = "#D8A3BE", "#AA4476", "#F8C8DF"


def enable_high_dpi():
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass


def configure(root):
    scale = max(1, root.winfo_fpixels("1i") / 96)
    margin = round(70 * scale)
    width = min(round(1050 * scale), root.winfo_screenwidth() - margin)
    height = min(round(770 * scale), root.winfo_screenheight() - margin)
    root.geometry(f"{width}x{height}")
    root.minsize(min(width, round(820 * scale)), min(height, round(630 * scale)))
    root.configure(bg=BG)
    root.option_add("*Font", "{Segoe UI} 10")
    root.option_add("*Listbox.background", INPUT)
    root.option_add("*Listbox.foreground", TEXT)
    root.option_add("*Listbox.selectBackground", SELECTION)
    root.option_add("*Listbox.selectForeground", TEXT)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, borderwidth=0, focuscolor=FOCUS)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"))
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
    style.configure("PanelTitle.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 15, "bold"))
    style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, insertcolor=TEXT,
                    bordercolor=BORDER, padding=6, selectbackground=SELECTION, selectforeground=TEXT)
    style.configure("TCombobox", fieldbackground=INPUT, foreground=TEXT, padding=4, arrowcolor=TEXT,
                    bordercolor=BORDER, selectbackground=SELECTION, selectforeground=TEXT)
    style.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)])
    for control in ("TCheckbutton", "TRadiobutton"):
        style.configure(control, background=BG, foreground=TEXT, indicatorcolor=INPUT)
        style.map(control, background=[("active", BG)], indicatorcolor=[("selected", ACCENT)])
    style.configure("Vertical.TScrollbar", background="#F2C6DA", troughcolor=BG, arrowcolor=MUTED)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=INPUT, bordercolor=BG)

    images = []

    def rounded_image(radius, fill, edge):
        size = (radius * 2 + 64) * 3
        canvas = Image.new("RGBA", (size, size))
        ImageDraw.Draw(canvas).rounded_rectangle(
            (0, 0, size - 1, size - 1), radius=radius * 3, fill=fill, outline=edge, width=3,
        )
        photo = ImageTk.PhotoImage(canvas.resize((size // 3, size // 3), Image.Resampling.LANCZOS), master=root)
        images.append(photo)
        return photo

    radius = round(14 * scale)
    style.element_create("PetalCard.background", "image", rounded_image(radius, PANEL, "#F0D1E1"),
                         border=radius, sticky="nsew", width=1, height=1)
    style.layout("Panel.TFrame", [("PetalCard.background", {"sticky": "nsew"})])
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("PanelContent.TFrame", background=PANEL)
    style.element_create("PetalStep.background", "image", rounded_image(radius, ACCENT, BORDER),
                         border=radius, sticky="nsew", width=1, height=1)
    style.layout("ActiveStep.TFrame", [("PetalStep.background", {"sticky": "nsew"})])
    style.configure("ActiveStep.TFrame", background=ACCENT)
    for name, normal, hover, pressed in (
        ("TButton", "#FFFDFE", "#FDE2EF", "#F8C7DF"),
        ("Accent.TButton", ACCENT, "#F8C4DC", "#EDA0C5"),
    ):
        element = f"Petal.{name}.background"
        style.element_create(
            element, "image", rounded_image(radius, normal, BORDER),
            ("disabled", rounded_image(radius, "#F7ECF2", "#E8D7E0")),
            ("pressed", rounded_image(radius, pressed, FOCUS)),
            ("focus", rounded_image(radius, normal, FOCUS)),
            ("active", rounded_image(radius, hover, BORDER)),
            border=radius, sticky="nsew", width=1, height=1,
        )
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]}),
        ]})])
        style.configure(name, foreground=TEXT, padding=(round(15 * scale), round(8 * scale)))
        style.map(name, foreground=[("disabled", MUTED)])
    return scale, images


def load_brand_mark():
    try:
        with Image.open(Path(__file__).with_name("shadow-enclave-mark.png")) as mark:
            return mark.convert("RGBA")
    except OSError:
        return None


def studio_banner(parent, scale, mark, *, eyebrow="CARRIER STUDIO", headline="Image  /  Audio  /  Text"):
    canvas = tk.Canvas(parent, height=round(118 * scale), background=BG, highlightthickness=0)

    def draw(event):
        s = scale
        width, height = event.width, event.height
        if width <= 2 or height <= 2:
            return
        canvas.delete("all")
        image = Image.new("RGBA", (width * 2, height * 2))
        art = ImageDraw.Draw(image)
        art.rounded_rectangle((0, 0, width * 2 - 1, height * 2 - 1),
                              radius=round(48 * s), fill="#FBD9EB", outline="#EBC2D6", width=2)

        def point(x, y):
            return round(2 * (width + x * s)), round(2 * y * s)

        def box(x1, y1, x2, y2):
            return (*point(x1, y1), *point(x2, y2))

        for x, y, r in ((-228, 41, 4), (-168, 83, 5), (-47, 34, 5), (-27, 75, 4), (-248, 79, 2)):
            art.polygon((point(x, y - r), point(x + r, y), point(x, y + r), point(x - r, y)), fill="#B85C87")
        if mark is not None:
            size = round(2 * 108 * s)
            image.alpha_composite(mark.resize((size, size), Image.Resampling.LANCZOS), point(-161, 5))
        for x, y in ((-193, 52), (-48, 91)):
            art.ellipse(box(x - 7, y - 4, x + 1, y + 4), fill="#DA74A5")
            art.ellipse(box(x - 1, y - 4, x + 7, y + 4), fill="#DA74A5")
            art.polygon((point(x - 7, y), point(x + 7, y), point(x, y + 9)), fill="#DA74A5")
        canvas.banner = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=canvas)
        canvas.create_image(0, 0, image=canvas.banner, anchor="nw")
        canvas.create_text(round(28 * s), round(39 * s), anchor="w", text=eyebrow,
                           fill="#88466B", font=("Segoe UI", 10, "bold"))
        canvas.create_text(round(28 * s), round(73 * s), anchor="w", text=headline,
                           fill=TEXT, font=("Segoe UI", 20, "bold"))

    canvas.bind("<Configure>", draw)
    return canvas
