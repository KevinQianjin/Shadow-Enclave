"""Windows desktop interface for Shadow Enclave."""

import ctypes
import io
import math
import os
import queue
import struct
import threading
import time
import tkinter as tk
import wave
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import encryption_code_v4 as core
import enclave_media as media
import enclave_workflow as workflow
import enclave_crypto as crypto
import enclave_cadence as cadence
import enclave_theme as theme
import enclave_windows as windows
import enclave_broker as broker

from enclave_theme import ACCENT, BG, BORDER, FOCUS, INPUT, SELECTION, TEXT

MODES = {
    "Single Carrier": (1, False),
    "Two Carriers": (2, False),
    "Camouflage Text (Requires DeepSeek)": (1, True),
    "Split Camouflage Text (Requires DeepSeek)": (2, True),
}
COVERS = {
    "Off": "none",
    "Local Topic Match": "local",
    "AI Cover Document (Requires DeepSeek)": "ai",
}
MATCH_AUDIO = {
    "sunset": "ambient", "emerald": "forest", "cyber": "cyber", "ocean": "ocean_bells",
    "sakura": "zen", "desert": "celtic", "ink": "zen", "nebula": "space",
    "rain_city": "lofi", "ukiyo": "ocean_bells", "botanical": "forest",
    "poster": "jazz", "geometry": "percussion", "architecture": "strings",
}
DECRYPT_MODES = {"Detect Format": None, **{label: mode for mode, label in workflow.MODES.items()}}


LOCAL_TYPING_CHOICES = ("Off", *core.BIOMETRIC_LEVELS)


def _default_option(settings, key, choices, fallback):
    value = settings.get(key)
    return value if value in choices else fallback


def _sample(text, times):
    intervals = tuple(1000 * (b - a) for a, b in zip(times, times[1:]))
    if not intervals:
        return workflow.PasswordSample(text, intervals=())
    mean = sum(intervals) / len(intervals)
    std = math.sqrt(sum((value - mean) ** 2 for value in intervals) / len(intervals))
    return workflow.PasswordSample(text, mean, std, intervals)


def _metrics(sample):
    return core.format_keystroke_metrics(sample.mean, sample.std, sample.intervals)


class PasswordDialog(simpledialog.Dialog):
    def __init__(self, parent, enroll=False, title="Passphrase", cadence_level=None, *, confirm=False, portable=False):
        self.enroll = enroll
        self.confirm = confirm or enroll
        self.cadence_level = cadence_level
        self.portable = portable
        self.fields = []
        super().__init__(parent, title)

    def body(self, master):
        master.configure(bg=BG)
        self.configure(bg=BG)
        labels = ("Pass 1", "Pass 2", "Pass 3") if self.enroll else (
            ("Passphrase", "Confirm passphrase") if self.confirm else ("Passphrase",)
        )
        for row, label in enumerate(labels):
            ttk.Label(master, text=label).grid(row=row, column=0, padx=8, pady=5, sticky="w")
            entry = ttk.Entry(master, show="*", width=24)
            entry.grid(row=row, column=1, padx=8, pady=5)
            times = []

            def record(event, timestamps=times, field=entry):
                if event.keysym in {"Left", "Right", "Home", "End", "Delete"} or event.state & 0x4:
                    timestamps.clear()
                elif event.keysym == "BackSpace":
                    if field.selection_present():
                        timestamps.clear()
                    elif timestamps:
                        timestamps.pop()
                elif event.char and event.char.isprintable():
                    if field.selection_present() or field.index("insert") != field.index("end"):
                        timestamps.clear()
                    else:
                        timestamps.append(time.perf_counter())

            entry.bind("<KeyPress>", record)
            entry.bind("<<Paste>>", lambda _, timestamps=times: timestamps.clear())
            entry.bind("<<Cut>>", lambda _, timestamps=times: timestamps.clear())
            if self.enroll or self.cadence_level is not None:
                metric = ttk.Label(master, text="0 intervals measured", style="Muted.TLabel")
                metric.grid(row=row, column=2, padx=8, sticky="w")
                entry.bind("<KeyRelease>", lambda _, field=entry, stamp=times, target=metric:
                           target.configure(text=_metrics(_sample(field.get(), stamp))), add="+")
            self.fields.append((entry, times))

        next_row = len(labels)
        if self.confirm:
            entropy_label = ttk.Label(
                master,
                text=crypto.format_passphrase_strength("", portable=self.portable),
                style="Muted.TLabel",
            )
            entropy_label.grid(row=next_row, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 2))
            next_row += 1
            self.fields[0][0].bind(
                "<KeyRelease>",
                lambda _, field=self.fields[0][0], target=entropy_label: target.configure(
                    text=crypto.format_passphrase_strength(field.get(), portable=self.portable)
                ),
                add="+",
            )

        if self.confirm and not self.enroll:
            generated = tk.StringVar(master=self)
            value = ttk.Entry(master, textvariable=generated, width=40, state="readonly")
            hint = ttk.Label(
                master,
                text="Save this 128-bit passphrase somewhere safe before continuing (or type a custom passphrase above).",
                style="Muted.TLabel",
            )

            def generate():
                generated.set(crypto.generate_passphrase())
                for entry, timestamps in self.fields:
                    entry.delete(0, "end")
                    entry.insert(0, generated.get())
                    timestamps.clear()
                entropy_label.configure(
                    text=crypto.format_passphrase_strength(generated.get(), portable=self.portable)
                )
                value.grid(row=next_row + 1, column=0, columnspan=3, sticky="ew", padx=8)
                hint.grid(row=next_row + 2, column=0, columnspan=3, sticky="w", padx=8)
                value.focus_set()
                value.selection_range(0, "end")

            ttk.Button(master, text="Generate Random Passphrase (128-bit)", command=generate).grid(
                row=next_row, column=0, columnspan=3, sticky="w", padx=8, pady=5,
            )
            if self.portable:
                generate()
                return value

        if self.enroll:
            level = self.cadence_level or 1
            cutoff = cadence.BIOMETRIC_THRESHOLDS[level]
            ttk.Label(master, text=f"Match threshold: {cutoff:g}%", style="Muted.TLabel").grid(
                row=next_row, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 3)
            )
            ttk.Button(master, text="Test Typing Rhythm", command=self.practice).grid(
                row=next_row + 1, column=0, columnspan=3, sticky="w", padx=8
            )
        return self.fields[0][0]

    def samples(self):
        return [_sample(entry.get(), timestamps) for entry, timestamps in self.fields]

    def validate(self):
        values = [entry.get() for entry, _ in self.fields]
        if not values[0]:
            messagebox.showerror("Passphrase", "Enter a passphrase.", parent=self)
            return False
        if self.confirm:
            if len(set(values)) != 1:
                messagebox.showerror("Passphrase", "Enter the same passphrase in each field.", parent=self)
                return False
            try:
                crypto.validate_new_password(values[0])
            except ValueError as exc:
                messagebox.showerror("Passphrase", str(exc), parent=self)
                return False
        if self.enroll and any(len(item.intervals) < 2 for item in self.samples()):
            messagebox.showerror("Typing Rhythm", "Retype each passphrase by hand to capture its rhythm.", parent=self)
            return False
        return True

    def baseline(self):
        samples = self.samples()
        mean = sum(item.mean for item in samples) / len(samples)
        std = sum(item.std for item in samples) / len(samples)
        intervals = tuple(sum(group) / len(group) for group in zip(*(item.intervals for item in samples)))
        return workflow.PasswordSample(samples[0].text, mean, std, intervals)

    def practice(self):
        if not self.validate():
            return
        baseline = self.baseline()
        if len(baseline.intervals) < 2:
            messagebox.showinfo("Typing Rhythm", "A practice score requires at least two measured intervals per pass.", parent=self)
            return
        level = self.cadence_level or 1
        attempt = PasswordDialog(self, title="Test Typing Rhythm", cadence_level=level).result
        if attempt is None:
            return
        if attempt.text != baseline.text or len(attempt.intervals) < 2:
            messagebox.showinfo("Typing Rhythm", "Type the same password by hand to measure its rhythm.", parent=self)
            return
        signature = core.compute_cadence_sig(baseline.intervals, baseline.mean)
        passed, score = cadence.evaluate_cadence(
            baseline.mean, signature, {"intervals": attempt.intervals}, level
        )
        messagebox.showinfo(
            "Typing Rhythm",
            f"Practice sample: {_metrics(attempt)}\n"
            f"Match: {score:.1f}%  /  Threshold: {cadence.BIOMETRIC_THRESHOLDS[level]:g}%\n"
            f"{'Threshold met' if passed else 'Below threshold'}",
            parent=self,
        )

    def apply(self):
        if self.enroll:
            self.result = self.baseline()
        elif self.confirm:
            self.result = workflow.PasswordSample(self.fields[0][0].get(), intervals=())
        else:
            self.result = self.samples()[0]


class Desktop:
    def __init__(self, root, dnd_type=None, *, open_settings=False):
        self.root = root
        self.dnd_type = dnd_type
        root.title("Shadow Enclave V4")
        self.scale, self.theme_images = theme.configure(root)
        self.brand_mark = theme.load_brand_mark()
        if self.brand_mark is not None:
            icons = [self.brand_photo(size) for size in (48, 32, 16)]
            root.iconphoto(True, *icons)

        self.status = tk.StringVar()
        self.busy = False
        self.events = queue.Queue()
        self.progress = ttk.Progressbar(root, mode="indeterminate", length=175)
        self.status_label = ttk.Label(root, textvariable=self.status, style="Muted.TLabel", wraplength=790)
        self.status_label.pack(side="bottom", anchor="w", padx=26, pady=(0, 12))
        self.body = ttk.Frame(root, padding=(26, 22, 26, 8))
        self.body.pack(fill="both", expand=True)
        root.after(60, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.open_settings_on_unlock = open_settings
        self.locked(auto=True)

    def locked(self, *, auto=False):
        for widget in self.body.winfo_children():
            widget.destroy()
        self.status.set("")
        card = ttk.Frame(self.body)
        card.place(relx=0.5, rely=0.46, anchor="center", relwidth=0.76)
        theme.studio_banner(card, self.scale, self.brand_mark,
                            eyebrow="SHADOW ENCLAVE  /  V4", headline="Unlock your workspace").pack(fill="x", pady=(0, 14))
        panel = ttk.Frame(card, style="Panel.TFrame", padding=round(22 * self.scale))
        panel.pack(fill="x")
        ttk.Label(panel, text="Windows Hello", style="PanelTitle.TLabel").pack(anchor="w")
        try:
            enrolled = broker.status()["enrolled"]
            instruction = ("Verify with Windows Hello to open V4." if enrolled else
                           "Enroll Windows Hello for this Windows account to open V4.")
        except broker.BrokerError as error:
            enrolled = None
            instruction = "The V4 service is unavailable. Retry after starting it."
            self.status.set(str(error))
        ttk.Label(panel, text=instruction, style="Panel.TLabel", wraplength=round(390 * self.scale),
                  justify="left").pack(anchor="w", pady=(6, 16))
        actions = ttk.Frame(panel, style="PanelContent.TFrame")
        actions.pack(fill="x")

        def unlocked(_):
            if self.open_settings_on_unlock:
                self.open_settings_on_unlock = False
                self.settings(return_to=self.close)
            else:
                self.home()

        def verify():
            if self.busy or not primary.winfo_exists():
                return
            owner = self.hello_owner()
            primary.state(["disabled"])
            self.job("Complete the Windows Security prompt to continue.",
                     lambda: broker.open_session(enroll=not enrolled, owner_hwnd=owner), unlocked,
                     on_error=lambda _: primary.state(["!disabled"]))

        label = "Verify to Open" if enrolled else "Enroll Windows Hello" if enrolled is not None else "Retry"
        primary = self.button(actions, label, verify if enrolled is not None else lambda: self.locked(auto=True),
                              primary=True)
        primary.pack(side="left")
        self.button(actions, "Exit", self.close).pack(side="right")
        primary.focus_set()
        if auto and enrolled:
            self.root.after_idle(verify)

    def hello_owner(self):
        """Resolve the mapped native frame on Tk's UI thread before launching Hello."""
        self.root.update_idletasks()
        self.root.lift()
        hwnd = int(str(self.root.wm_frame()), 0)
        ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        return hwnd

    def close(self):
        if not self.busy:
            self.root.destroy()

    def poll(self):
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "notice":
                if not self.busy:
                    self.status.set(value)
            else:
                self.busy = False
                self.progress.stop()
                self.progress.pack_forget()
                if event == "error":
                    callback, error = value
                    if isinstance(error, broker.SessionError):
                        self.locked()
                    self.status.set(str(error) or "Unable to complete the operation.")
                    if callback and not isinstance(error, broker.SessionError):
                        callback(error)
                else:
                    callback, result = value
                    self.status.set("")
                    callback(result)
        self.root.after(60, self.poll)

    def job(self, message, work, done, *, on_error=None):
        if self.busy:
            return
        self.busy = True
        self.status.set(message)
        self.progress.pack(side="bottom", anchor="e", padx=26, pady=(0, 10), before=self.status_label)
        self.progress.start(12)

        def worker():
            try:
                result = work()
            except Exception as exc:
                self.events.put(("error", (on_error, exc)))
            else:
                self.events.put(("done", (done, result)))

        threading.Thread(target=worker, daemon=True).start()

    def page(self, title, subtitle=""):
        for widget in self.body.winfo_children():
            widget.destroy()
        self.status.set("")
        ttk.Label(self.body, text=title, style="Title.TLabel", font=("Segoe UI", 25, "bold")).pack(anchor="w")
        if subtitle:
            ttk.Label(self.body, text=subtitle, style="Muted.TLabel", wraplength=810).pack(anchor="w", pady=(4, 14))

    def button(self, parent, title, action, *, primary=False):
        return ttk.Button(parent, text=title, command=lambda: None if self.busy else action(),
                          style="Accent.TButton" if primary else "TButton")

    def text_area(self, parent, text="", *, height=6):
        widget = tk.Text(parent, height=height, wrap="word", bg=INPUT, fg=TEXT, insertbackground=TEXT,
                         highlightbackground=BORDER, highlightcolor=FOCUS, highlightthickness=1,
                         selectbackground=SELECTION, selectforeground=TEXT, cursor="xterm",
                         padx=12, pady=9, relief="flat", undo=True, takefocus=True, state="normal")
        widget.insert("1.0", text)
        widget.edit_reset()
        return widget

    def brand_photo(self, size):
        return ImageTk.PhotoImage(self.brand_mark.resize((size, size), Image.Resampling.LANCZOS),
                                  master=self.root)

    def home(self):
        self.plaintext = ""
        self.password = workflow.PasswordSample()
        self.page("Shadow Enclave V4", "Signed Windows Hello · Independent local service · V4 carriers only")
        theme.studio_banner(self.body, self.scale, self.brand_mark).pack(fill="x", pady=(0, 14))
        cards = ttk.Frame(self.body)
        cards.pack(fill="x")
        entries = (
            ("01   Encrypt", "Create one or two lossless carriers.", self.start_encrypt),
            ("02   Decrypt", "Open an image, audio file, or text armor.", self.start_decrypt),
            ("03   Settings", "Manage folders, verification, and DeepSeek access.", self.settings),
            ("04   About", "View the V4 format, author, and copyright.", self.tools),
        )
        for i, (title, desc, action) in enumerate(entries):
            card = ttk.Frame(cards, style="Panel.TFrame", padding=16)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=5, pady=5)
            cards.columnconfigure(i % 2, weight=1, uniform="cards")
            ttk.Label(card, text=title, style="PanelTitle.TLabel", font=("Segoe UI", 15, "bold")).pack(anchor="w")
            ttk.Label(card, text=desc, style="Panel.TLabel", wraplength=round(330 * self.scale)).pack(anchor="w", pady=(5, 9))
            self.button(card, "Open", action).pack(anchor="w")
        connected = core.deepseek_key_status()
        ttk.Label(self.body, text=f"Output folder: {core.get_carrier_save_dir()}\nDeepSeek: {connected}",
                  style="Muted.TLabel", wraplength=815).pack(anchor="w", pady=12)
        self.button(self.body, "Exit", self.close).pack(anchor="w")

    def start_encrypt(self):
        self.step = 0
        self.plaintext = ""
        self.password = workflow.PasswordSample()
        self.mode = tk.StringVar(value="Single Carrier")
        self.formats = [tk.StringVar(value="PNG"), tk.StringVar(value="FLAC" if media.flac_available() else "WAV")]
        self.sizes = [tk.StringVar(value="512"), tk.StringVar(value="512")]
        self.rates = [tk.StringVar(value="44100"), tk.StringVar(value="44100")]
        self.styles = [None, None]
        self.directory = tk.StringVar(value=core.get_carrier_save_dir())
        settings = core.load_enclave_config()
        bound = settings.get("default_device_bound") is True
        self.default_typing = _default_option(settings, "default_typing", LOCAL_TYPING_CHOICES, "Off")
        self.device_bound = tk.BooleanVar(value=bound)
        self.device_available = windows.tpm_available()
        self.wavelet = tk.BooleanVar(value=True)
        self.cover = tk.StringVar(value=_default_option(settings, "default_cover", COVERS, "Off"))
        self.cadence = tk.StringVar(value=self.default_typing if bound else "Off")
        self.portable_cleared = False
        if not bound:
            self.password = workflow.PasswordSample(crypto.generate_passphrase())
        self.advanced_open = False
        self.wizard()

    def set_step(self, step):
        if self.step == 0:
            self.plaintext = self.plain_entry.get("1.0", "end-1c")
            if step > 0 and not self.plaintext.strip():
                self.status.set("Enter plaintext to continue.")
                return
        self.step = step
        self.wizard()

    def wizard(self):
        self.page("Encrypt")
        progress = ttk.Frame(self.body)
        progress.pack(fill="x", pady=(7, 18))
        for i, title in enumerate(("Input", "Output", "Protection", "Review")):
            active = i == self.step
            badge = ttk.Frame(progress, style="ActiveStep.TFrame" if active else "Panel.TFrame",
                              padding=(round(12 * self.scale), 8))
            badge.grid(row=0, column=i, sticky="nsew", padx=(0, 5))
            progress.columnconfigure(i, weight=1, uniform="steps")
            ttk.Label(badge, text=f"{i + 1:02d}  {title}", style="Panel.TLabel",
                      background=ACCENT if active else theme.PANEL,
                      font=("Segoe UI", 11, "bold" if active else "normal")).pack(anchor="w")
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True)
        if self.step == 0:
            self.input_step(frame)
        elif self.step == 1:
            self.output_step(frame)
        elif self.step == 2:
            self.protection_step(frame)
        else:
            self.review_step(frame)
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(10, 0))
        self.button(bar, "Cancel", self.home).pack(side="left")
        if self.step:
            self.button(bar, "Back", lambda: self.set_step(self.step - 1)).pack(side="left", padx=8)
        if self.step < 3:
            self.button(bar, "Continue", lambda: self.set_step(self.step + 1), primary=True).pack(side="right")
        else:
            self.button(bar, "Encrypt", self.perform_encrypt, primary=True).pack(side="right")

    def input_step(self, frame):
        card = ttk.Frame(frame, style="Panel.TFrame", padding=16)
        card.pack(fill="both", expand=True)
        toolbar = ttk.Frame(card, style="PanelContent.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Plaintext", style="PanelTitle.TLabel",
                  font=("Segoe UI", 15, "bold")).pack(side="left")
        self.button(toolbar, "Paste", self.paste_plaintext).pack(side="right")
        ttk.Label(card, text="Type your message below or paste it from the clipboard.",
                  style="Panel.TLabel").pack(anchor="w", pady=(0, 8))
        editor = ttk.Frame(card, style="PanelContent.TFrame")
        editor.pack(fill="both", expand=True)
        field = self.text_area(editor, self.plaintext, height=11)
        scroll = ttk.Scrollbar(editor, orient="vertical", command=field.yview)
        field.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        field.pack(side="left", fill="both", expand=True)
        field.bind("<Button-1>", lambda _: field.focus_set(), add="+")
        field.mark_set("insert", "end-1c")
        self.plain_entry = field
        self.root.after_idle(lambda: field.focus_set() if field.winfo_exists() else None)

    def paste_plaintext(self):
        field = self.plain_entry
        try:
            clipboard = self.root.clipboard_get()
        except tk.TclError:
            self.status.set("The clipboard has no readable text.")
            field.focus_set()
            return
        if field.tag_ranges("sel"):
            field.delete("sel.first", "sel.last")
        field.insert("insert", clipboard)
        field.see("insert")
        field.focus_set()
        self.status.set("")

    def output_step(self, frame):
        mode = ttk.Combobox(frame, textvariable=self.mode, values=tuple(MODES), state="readonly", width=48)
        mode.pack(anchor="w", pady=(0, 9))
        mode.bind("<<ComboboxSelected>>", lambda _: self.wizard())
        count, online = MODES[self.mode.get()]
        self.sharing_options(frame, count, online)
        if online:
            self.ai_notice(frame)
            output_note = "Copy the two texts or save them as separate .txt files." if count == 2 else "Copy the camouflage text from the result page."
            ttk.Label(frame, text=f"{output_note}\nPlaintext limit: {crypto.MAX_CAMOUFLAGE} UTF-8 bytes.",
                      wraplength=720).pack(anchor="w", pady=12)
            return
        self.folder_row(frame, self.directory)
        for i in range(count):
            fmt = self.formats[i].get()
            kind = workflow.kind_of(fmt)
            row = ttk.Frame(frame, style="Panel.TFrame", padding=12)
            row.pack(fill="x", pady=(8, 0))
            ttk.Label(row, text="Carrier" if count == 1 else f"Part {'AB'[i]}", style="PanelTitle.TLabel").grid(
                row=0, column=0, sticky="w", padx=(0, 15)
            )
            combo = ttk.Combobox(row, textvariable=self.formats[i], values=workflow.available_formats(), state="readonly", width=13)
            combo.grid(row=0, column=1, padx=5)
            combo.bind("<<ComboboxSelected>>", lambda _, index=i: self.change_format(index))
            if kind != "text":
                variable, choices, unit = ((self.sizes[i], ("256", "512", "1024"), "px square (minimum)") if kind == "image"
                                           else (self.rates[i], ("22050", "44100", "48000"), "Hz, 16-bit mono"))
                ttk.Combobox(row, textvariable=variable, values=choices, state="readonly", width=8).grid(row=0, column=2, padx=5)
                ttk.Label(row, text=unit, style="Panel.TLabel").grid(row=0, column=3, sticky="w")
                self.button(row, "Style & Preview", lambda index=i: self.style_picker(index)).grid(row=0, column=4, padx=12)
                current = self.style_for(i)
                name = current.name or workflow.style_catalog(kind)[current.theme]["name"]
                ttk.Label(row, text=name, style="Panel.TLabel").grid(row=1, column=0, columnspan=5, sticky="w", pady=(5, 0))
            else:
                ttk.Label(row, text="ASCII text", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
        if count == 2 and all(workflow.kind_of(v.get()) != "text" for v in self.formats):
            self.button(frame, "Match Styles", self.match_styles).pack(anchor="w", pady=12)
        ttk.Label(frame, text="AI Image Styling and AI Audio Styling require DeepSeek.",
                  style="Muted.TLabel").pack(anchor="w", pady=8)

    def sharing_options(self, frame, count, camouflage):
        card = ttk.Frame(frame, style="Panel.TFrame", padding=(12, 8))
        card.pack(fill="x", pady=(0, 6))
        ttk.Label(card, text="Sharing & Device Binding", style="PanelTitle.TLabel").pack(anchor="w")

        def selected():
            if not self.device_bound.get():
                self.cadence.set("Off")
                if not self.password.text:
                    self.password = workflow.PasswordSample(crypto.generate_passphrase())
                    self.portable_cleared = False
            elif self.cadence.get() == "Off":
                self.cadence.set(self.default_typing)
            summary.set(workflow.sharing_message(self.device_bound.get(), count, camouflage=camouflage))
            policy.set(
                f"Portable Passphrase Policy: 128-bit random passphrase auto-generated ({crypto.format_passphrase_strength(self.password.text, portable=True)})"
                if not self.device_bound.get()
                else "Device-Bound Passphrase Policy: TPM 2.0 + Windows Hello + Passphrase (15+ characters)"
            )

        buttons = ttk.Frame(card, style="Panel.TFrame")
        buttons.pack(anchor="w")
        ttk.Radiobutton(buttons, text=workflow.PORTABLE_LABEL, value=False, variable=self.device_bound,
                        command=selected).pack(side="left", padx=(0, 14))
        local = ttk.Radiobutton(buttons, text=workflow.BOUND_LABEL, value=True, variable=self.device_bound,
                                command=selected)
        local.pack(side="left")
        if not self.device_available:
            local.state(["disabled"])
            ttk.Label(card, text="This PC Only requires an available TPM 2.0.",
                      style="Panel.TLabel").pack(anchor="w")
        summary = tk.StringVar(value=workflow.sharing_message(self.device_bound.get(), count, camouflage=camouflage))
        ttk.Label(card, textvariable=summary, style="Panel.TLabel", wraplength=780).pack(anchor="w", pady=(3, 0))
        policy = tk.StringVar(
            value=(
                f"Portable Passphrase Policy: 128-bit random passphrase auto-generated ({crypto.format_passphrase_strength(self.password.text, portable=True)})"
                if not self.device_bound.get()
                else "Device-Bound Passphrase Policy: TPM 2.0 + Windows Hello + Passphrase (15+ characters)"
            )
        )
        ttk.Label(card, textvariable=policy, style="PanelTitle.TLabel", wraplength=780).pack(anchor="w", pady=(2, 2))
        ttk.Label(card, text=workflow.LOCAL_FEATURES, style="Panel.TLabel", wraplength=780).pack(anchor="w")

    def change_format(self, index):
        current = self.styles[index]
        kind = workflow.kind_of(self.formats[index].get())
        if current is not None and kind != "text" and isinstance(current, media.ImageStyle) != (kind == "image"):
            self.styles[index] = None
        self.wizard()

    def style_for(self, index):
        kind = workflow.kind_of(self.formats[index].get())
        return self.styles[index] or workflow.default_style(kind, "sunset" if kind == "image" else "ambient")

    def match_styles(self):
        first = self.style_for(0)
        first_kind, second_kind = (workflow.kind_of(item.get()) for item in self.formats)
        if first_kind == second_kind:
            self.styles[1] = first
        elif first_kind == "image":
            self.styles[1] = workflow.default_style("audio", MATCH_AUDIO[first.theme])
        else:
            match = next((key for key, value in MATCH_AUDIO.items() if value == first.theme), "sunset")
            match = {"lullaby": "sakura", "rain": "rain_city"}.get(first.theme, match)
            self.styles[1] = workflow.default_style("image", match)
        self.wizard()
        self.status.set("Part B now matches Part A's style.")

    def folder_row(self, frame, variable, label="Output folder"):
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label, width=24).pack(side="left", padx=(0, 9))
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(0, 8))

        def browse():
            selected = filedialog.askdirectory(parent=self.root, initialdir=variable.get())
            if selected:
                variable.set(selected)

        self.button(row, "Browse", browse).pack(side="right")

    def ai_notice(self, frame):
        configured = core.has_deepseek_api_key()
        ttk.Label(frame, text="Requires DeepSeek  /  " + ("Configured" if configured else "Not configured"),
                  style="Muted.TLabel").pack(anchor="w", pady=(7, 4))
        if not configured:
            self.button(frame, "Open Settings", lambda: self.settings(return_to=self.wizard)).pack(anchor="w")

    def style_picker(self, index):
        kind = workflow.kind_of(self.formats[index].get())
        catalog = workflow.style_catalog(kind)
        labels = {meta["name"]: key for key, meta in catalog.items()}
        chosen = self.style_for(index)
        pop = tk.Toplevel(self.root)
        pop.title("Image Styling" if kind == "image" else "Audio Styling")
        pop.geometry("910x550")
        pop.configure(bg=BG)
        pop.transient(self.root)
        pop.grab_set()
        pane = ttk.Frame(pop, padding=17)
        pane.pack(fill="both", expand=True)
        theme = tk.StringVar(value=catalog[chosen.theme]["name"])
        prompt = tk.StringVar()
        same_theme = tk.BooleanVar(value=False)
        ttk.Label(pane, text=f"{kind.title()} Theme", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        row = ttk.Frame(pane)
        row.pack(fill="x", pady=6)
        ttk.Combobox(row, values=tuple(labels), textvariable=theme, state="readonly", width=36).pack(side="left")
        host = ttk.Frame(pane)
        ttk.Label(pane, text=f"AI {kind.title()} Styling and Custom Prompt require DeepSeek.",
                  style="Muted.TLabel").pack(anchor="w", pady=(7, 0))
        ttk.Entry(pane, textvariable=prompt).pack(fill="x", pady=5)
        ttk.Label(pane, text="Custom Prompt (optional). Enter style details only.", style="Muted.TLabel").pack(anchor="w")
        controls = ttk.Frame(pane)
        controls.pack(fill="x", pady=7)
        local_status = ttk.Label(pane, style="Muted.TLabel", wraplength=835)
        local_status.pack(anchor="w")
        host.pack(fill="both", expand=True, pady=5)

        def apply_style(style):
            self.styles[index] = style
            pop.destroy()
            self.wizard()

        def display(items):
            if not pop.winfo_exists():
                return
            local_status.configure(text="")
            for widget in host.winfo_children():
                widget.destroy()
            for col, (style, data) in enumerate(items):
                card = ttk.Frame(host, style="Panel.TFrame", padding=8)
                card.grid(row=0, column=col, sticky="nsew", padx=4)
                host.columnconfigure(col, weight=1, uniform="styles")
                title = style.name or catalog[style.theme]["name"]
                ttk.Label(card, text=title, style="Panel.TLabel", wraplength=235).pack(anchor="w", pady=4)
                if style.name:
                    ttk.Label(card, text=catalog[style.theme]["name"], style="Panel.TLabel",
                              wraplength=235).pack(anchor="w", pady=(0, 4))
                self.preview_card(card, kind, data)
                self.button(card, "Use Theme", lambda value=style: apply_style(value), primary=True).pack(anchor="w", pady=5)

        def report_error(error):
            if pop.winfo_exists():
                local_status.configure(text=str(error))

        def local():
            style = workflow.default_style(kind, labels[theme.get()])
            local_status.configure(text="Rendering preview...")
            self.job("Rendering preview...", lambda: [(style, workflow.render_preview(style))], display, on_error=report_error)

        def generate():
            if not core.has_deepseek_api_key():
                local_status.configure(text="Requires DeepSeek. Add an API key in Settings.")
                return
            selected = labels[theme.get()]
            description = prompt.get()
            keep_theme = same_theme.get()
            local_status.configure(text="Generating three previews...")
            def variations():
                key = core.get_deepseek_api_key()
                return [(item, workflow.render_preview(item)) for item in
                        media.request_variations(key, kind, selected, description, tuple(catalog), same_theme=keep_theme)]
            self.job(
                "Requesting DeepSeek style variations...",
                variations,
                display, on_error=report_error,
            )

        self.button(row, "Preview Theme", local).pack(side="left", padx=8)
        self.button(controls, "Generate Variations", generate, primary=True).pack(side="left")
        ttk.Checkbutton(controls, text="Same Theme", variable=same_theme).pack(side="left", padx=9)
        if not core.has_deepseek_api_key():
            def open_settings():
                pop.destroy()
                self.settings(return_to=lambda: (self.wizard(), self.style_picker(index)))
            self.button(controls, "Open Settings", open_settings).pack(side="left", padx=8)
        self.button(controls, "Close", pop.destroy).pack(side="right")
        self.job("Rendering preview...", lambda: [(chosen, workflow.render_preview(chosen))], display, on_error=report_error)

    def preview_card(self, card, kind, data):
        if kind == "image":
            with Image.open(io.BytesIO(data)) as image:
                image.thumbnail((250, 150))
                photo = ImageTk.PhotoImage(image)
            label = ttk.Label(card, image=photo, style="Panel.TLabel")
            label.image = photo
            label.pack(anchor="w")
            return
        canvas = tk.Canvas(card, width=235, height=100, bg=INPUT, highlightthickness=0)
        canvas.pack(anchor="w")
        with wave.open(io.BytesIO(data), "rb") as wav:
            raw = wav.readframes(wav.getnframes())
        pcm = [sample[0] for sample in struct.iter_unpack("<h", raw)]
        size = max(1, len(pcm) // 230)
        for x in range(230):
            samples = pcm[x * size:(x + 1) * size]
            if samples:
                lo, hi = min(samples), max(samples)
                canvas.create_line(x + 2, 50 - hi * 45 / 32768, x + 2, 50 - lo * 45 / 32768, fill=ACCENT)
        self.button(card, "Play Preview", lambda: self.play(data)).pack(anchor="w", pady=5)

    def play(self, data):
        try:
            import winsound
        except ImportError:
            self.status.set("Audio preview requires Windows.")
            return

        def playback():
            try:
                winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT | winsound.SND_NOSTOP)
            except RuntimeError:
                self.events.put(("notice", "Unable to play audio on the current output device."))

        threading.Thread(target=playback, daemon=True).start()

    def protection_step(self, frame):
        count, online = MODES[self.mode.get()]
        bound = self.device_bound.get()
        ttk.Label(frame, text="Carrier Protection", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(frame, text=crypto.PROTECTION_LABEL, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(frame, text=workflow.BOUND_LABEL if bound else workflow.PORTABLE_LABEL).pack(anchor="w", pady=(0, 6))
        ttk.Label(frame, text="Local App Protection", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(frame, text=workflow.LOCAL_FEATURES, style="Muted.TLabel",
                  wraplength=round(690 * self.scale)).pack(anchor="w")
        ttk.Label(frame, text=workflow.cooldown_label(), style="Muted.TLabel").pack(anchor="w", pady=(0, 7))
        if not online:
            def toggle_advanced():
                self.advanced_open = not self.advanced_open
                self.wizard()

            self.button(frame, "Hide Advanced Options" if self.advanced_open else "Advanced Options",
                        toggle_advanced).pack(anchor="w", pady=(0, 7))
        if not online and self.advanced_open:
            line = ttk.Frame(frame)
            line.pack(fill="x", pady=4)
            ttk.Label(line, text="Alternate document", width=32).pack(side="left")
            combo = ttk.Combobox(line, textvariable=self.cover, values=tuple(COVERS), state="readonly", width=43)
            combo.pack(side="left")
            combo.bind("<<ComboboxSelected>>", lambda _: self.wizard())
            if bound:
                row = ttk.Frame(frame)
                row.pack(fill="x", pady=4)
                ttk.Label(row, text="Local typing check", width=32).pack(side="left")
                selection = ttk.Combobox(row, textvariable=self.cadence, values=LOCAL_TYPING_CHOICES,
                                         state="readonly", width=43)
                selection.pack(side="left")
                selection.bind("<<ComboboxSelected>>", lambda _: self.wizard())
            else:
                ttk.Label(frame, text="Typing check: available with This PC Only.",
                          style="Muted.TLabel").pack(anchor="w")
            if any(workflow.kind_of(v.get()) == "image" for v in self.formats[:count]):
                ttk.Checkbutton(frame, text="Wavelet embedding for image carriers", variable=self.wavelet).pack(anchor="w", pady=3)
            if COVERS[self.cover.get()] == "ai":
                self.ai_notice(frame)
        level = core.BIOMETRIC_LEVELS.get(self.cadence.get(), 0) if bound and not online else 0
        if not bound and not self.password.text and not getattr(self, "portable_cleared", False):
            self.password = workflow.PasswordSample(crypto.generate_passphrase())
        pwd_card = ttk.Frame(frame, style="Panel.TFrame", padding=(12, 10))
        pwd_card.pack(fill="x", pady=(10, 6))
        ttk.Label(pwd_card, text="Passphrase & Strength", style="PanelTitle.TLabel",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        if self.password.text:
            label = f"Passphrase set · {crypto.format_passphrase_strength(self.password.text, portable=not bound)}"
        elif not bound:
            label = "Passphrase required · Portable mode recommends a 128-bit random passphrase"
        else:
            label = "Passphrase required (15+ characters)"
        ttk.Label(pwd_card, text=label, style="Panel.TLabel", wraplength=760).pack(anchor="w", pady=(2, 4))
        if self.password.text and not level:
            disp_var = tk.StringVar(master=pwd_card, value=self.password.text)
            disp_entry = ttk.Entry(pwd_card, textvariable=disp_var, width=44, state="readonly")
            disp_entry.pack(anchor="w", pady=(0, 4))
            ttk.Label(
                pwd_card,
                text="Save or copy this passphrase before encrypting — recipients need it to unlock Portable carriers.",
                style="Panel.TLabel",
                wraplength=760,
            ).pack(anchor="w", pady=(0, 6))
        if self.password.text and level:
            cutoff = cadence.BIOMETRIC_THRESHOLDS[level]
            ttk.Label(pwd_card, text=f"Baseline: {_metrics(self.password)}  /  Threshold {cutoff:g}%",
                      style="Panel.TLabel").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(pwd_card, style="Panel.TFrame")
        row.pack(anchor="w")

        def set_password():
            dialog = PasswordDialog(
                self.root, enroll=bool(level), title="Set Passphrase",
                cadence_level=level or None, confirm=True, portable=not bound,
            )
            if dialog.result is not None:
                self.password = dialog.result
                self.portable_cleared = False
                self.wizard()

        def generate_128bit():
            self.password = workflow.PasswordSample(crypto.generate_passphrase())
            self.portable_cleared = False
            self.wizard()

        def clear_password():
            self.password = workflow.PasswordSample()
            self.portable_cleared = True
            self.wizard()

        self.button(row, "Custom Passphrase..." if self.password.text else "Set Passphrase", set_password).pack(side="left", padx=(0, 8))
        if not level:
            self.button(row, "Generate 128-bit Passphrase", generate_128bit, primary=not bound).pack(side="left", padx=(0, 8))
        if self.password.text:
            self.button(row, "Copy Passphrase", lambda: self.copy(self.password.text)).pack(side="left", padx=(0, 8))
        self.button(row, "Clear", clear_password).pack(side="left")

    def review_step(self, frame):
        count, online = MODES[self.mode.get()]
        ttk.Label(frame, text=self.mode.get(), font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 10))
        bound = self.device_bound.get()
        ttk.Label(frame, text=workflow.sharing_message(bound, count, camouflage=online),
                  wraplength=790).pack(anchor="w", pady=(0, 5))
        if bound:
            ttk.Label(frame, text=workflow.BOUND_WARNING, style="Muted.TLabel",
                      wraplength=790).pack(anchor="w", pady=(0, 6))
        ttk.Label(frame, text=workflow.LOCAL_FEATURES, style="Muted.TLabel",
                  wraplength=790).pack(anchor="w", pady=(0, 8))
        if online:
            ttk.Label(frame, text=crypto.PROTECTION_LABEL, style="Muted.TLabel").pack(anchor="w")
            self.ai_notice(frame)
        else:
            cards = ttk.Frame(frame)
            cards.pack(fill="x")
            for i in range(2):
                cards.columnconfigure(i, weight=1, uniform="review")
            sections = (
                ("Output", (
                    " + ".join(v.get() for v in self.formats[:count]),
                    self.directory.get() if any(v.get() != "Text Armor" for v in self.formats[:count]) else "Displayed as text",
                )),
                ("Carrier Protection", (
                    crypto.PROTECTION_LABEL,
                    workflow.BOUND_LABEL if bound else workflow.PORTABLE_LABEL,
                )),
            )
            for index, (title, lines) in enumerate(sections):
                card = ttk.Frame(cards, style="Panel.TFrame", padding=14)
                card.grid(row=0, column=index, padx=(0, 8), sticky="nsew")
                ttk.Label(card, text=title, style="PanelTitle.TLabel", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 6))
                for line in lines:
                    ttk.Label(card, text=line, style="Panel.TLabel", wraplength=350).pack(anchor="w")
            if COVERS[self.cover.get()] == "ai" and not core.has_deepseek_api_key():
                self.ai_notice(frame)
        ttk.Label(frame, text="Local App Protection", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, text=workflow.cooldown_label(), style="Muted.TLabel").pack(anchor="w")
        if not online:
            ttk.Label(frame, text=f"Alternate document: {self.cover.get()}", style="Muted.TLabel").pack(anchor="w")
        passphrase_status = (
            f"Passphrase: Set ({crypto.format_passphrase_strength(self.password.text, portable=not bound)})"
            if self.password.text else "Passphrase: Required"
        )
        ttk.Label(frame, text=passphrase_status).pack(anchor="w", pady=8)
        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=10)
        for i, label in enumerate(("Edit Input", "Edit Output", "Edit Protection")):
            self.button(row, label, lambda index=i: self.set_step(index)).pack(side="left", padx=(0, 6))

    def perform_encrypt(self):
        count, online = MODES[self.mode.get()]
        plaintext = self.plaintext
        try:
            crypto.validate_new_password(self.password.text)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        bound = self.device_bound.get()
        if bound and not windows.tpm_available():
            self.status.set("This PC Only requires a working TPM 2.0. Select Portable in Output to change it.")
            return
        if online:
            password = self.password.text
            self.job("Generating camouflage text...",
                     lambda: workflow.CarrierOutput(
                         texts=workflow.encrypt_camouflage(plaintext, core.get_deepseek_api_key(), split=count == 2,
                                                          password=password, bound=bound),
                         bound=bound, formats=("Camouflage Text",) * count,
                     ), self.encrypted)
            return
        specs = []
        for i in range(count):
            fmt = self.formats[i].get()
            style = self.style_for(i) if workflow.kind_of(fmt) != "text" else None
            specs.append(workflow.OutputSpec(fmt, style, int(self.sizes[i].get()), int(self.rates[i].get())))
        directory = self.directory.get().strip()
        if any(spec.format != "Text Armor" for spec in specs) and not os.path.isdir(directory):
            self.status.set("Select an existing output folder.")
            return
        protection = workflow.Protection(
            self.password, wavelet=self.wavelet.get(),
            cadence_level=core.BIOMETRIC_LEVELS.get(self.cadence.get(), 0) if bound else 0,
            cover=COVERS[self.cover.get()], bound=bound,
        )
        self.job("Creating lossless carriers...", lambda: workflow.encrypt_carriers(plaintext, protection, specs, directory),
                 self.encrypted)

    def encrypted(self, output):
        self.plaintext = ""
        self.password = workflow.PasswordSample()
        details = list(output.details)
        if len(output.files) + len(output.texts) == 2:
            details.append("Keep the two parts separate.")
        sharing = workflow.sharing_message(output.bound, len(output.formats),
                                           camouflage="Camouflage Text" in output.formats)
        self.result("Encryption Complete", output.texts, output.files, self.start_encrypt,
                    details="\n".join(details), sharing=sharing, formats=output.formats)
        warning = next((line for line in output.details if line.endswith("could not be saved.")), None)
        if warning:
            self.status.set(warning)

    def start_decrypt(self):
        self.page("Decrypt", "Open carrier files or paste V4 text. A split text pair can go in one box or in two separate boxes.")
        self.decrypt_kind = tk.StringVar(value="Detect Format")
        choices = tuple(DECRYPT_MODES)
        ttk.Combobox(self.body, textvariable=self.decrypt_kind, values=choices,
                     state="readonly", width=34).pack(anchor="w", pady=3)
        self.decrypt_inputs = []
        for title in ("Carrier / Text A", "Partner / Text B"):
            panel = ttk.Frame(self.body, style="Panel.TFrame", padding=10)
            panel.pack(fill="x", pady=6)
            line = ttk.Frame(panel, style="Panel.TFrame")
            line.pack(fill="x")
            ttk.Label(line, text=title, style="PanelTitle.TLabel").pack(side="left")
            widget = self.text_area(panel, height=5)
            widget.pack(fill="x", pady=(5, 0))
            self.decrypt_inputs.append(widget)

            def fill(value, target=widget):
                if not value:
                    self.status.set("The clipboard has no readable text.")
                    return
                target.delete("1.0", "end")
                target.insert("1.0", value)

            def choose(target=widget):
                files = filedialog.askopenfilenames(parent=self.root, filetypes=[
                    ("Carriers", "*.bmp *.png *.wav *.flac *.txt"), ("All files", "*.*")
                ])
                if files:
                    self.put_carrier_paths(files, target)

            self.button(line, "Browse", choose).pack(side="right")
            self.button(line, "Paste", lambda cb=fill: cb(core.get_clipboard_text() or "")).pack(side="right", padx=6)
            if self.dnd_type:
                widget.drop_target_register(self.dnd_type)
                widget.dnd_bind("<<Drop>>", lambda event, target=widget: self.drop_carriers(event, target))
        line = ttk.Frame(self.body)
        line.pack(fill="x", pady=9)
        self.button(line, "Home", self.home).pack(side="left")
        self.button(line, "Check & Decrypt", self.inspect_decrypt, primary=True).pack(side="right")

    def put_carrier_paths(self, files, target):
        if len(files) > 2:
            self.status.set("Select at most two files.")
            return
        for widget, name in (zip(self.decrypt_inputs, files) if len(files) > 1 else [(target, files[0])]):
            widget.delete("1.0", "end")
            widget.insert("1.0", name)

    def drop_carriers(self, event, target):
        if not self.busy:
            files = self.root.tk.splitlist(event.data)
            if files:
                self.put_carrier_paths(files, target)
        return "copy"

    def inspect_decrypt(self):
        first, second = (widget.get("1.0", "end-1c").strip() for widget in self.decrypt_inputs)
        mode = DECRYPT_MODES[self.decrypt_kind.get()]

        def inspect():
            items = workflow.read_carriers(first, second)
            if mode is not None and items[0].mode != mode:
                raise ValueError(f"Select {workflow.MODES[items[0].mode]} for this V4 carrier.")
            return items

        self.job("Inspecting V4 carrier format and pairing...", inspect, self.checked_decrypt)

    def checked_decrypt(self, items):
        first = items[0]
        if first.role and len(items) == 1:
            part = "Alpha" if first.role == 1 else "Beta"
            other = "Beta" if first.role == 1 else "Alpha"
            self.status.set(f"Found {part}: {first.path or 'V4 text'}. Add the matching {other} part.")
            self.decrypt_inputs[1].focus_set()
            return
        label = workflow.MODES[first.mode]
        sharing = workflow.BOUND_LABEL if first.bound else workflow.PORTABLE_LABEL
        detected = f"Detected {label} (V4) · {sharing}."
        self.status.set(detected)
        if first.bound and not windows.tpm_available():
            self.status.set("This carrier requires its original PC and TPM key.")
            return
        dlg = PasswordDialog(self.root, title=f"{label} | Passphrase", cadence_level=1 if first.bound else None)
        if dlg.result is None:
            return
        sample = dlg.result
        start = time.perf_counter()
        hello = core.windows_hello_enabled()
        owner = self.hello_owner()

        def decrypt():
            return workflow.decrypt_carriers(items, sample, owner_hwnd=owner)

        def result(text):
            elapsed = time.perf_counter() - start
            note = f"{detected}\nOperation elapsed: {elapsed:.2f}s"
            if first.bound:
                note += f"\nTyping sample: {_metrics(sample)}"
            self.result("Decryption Complete", (text,), (), self.start_decrypt, details=note)

        self.job("Waiting for Windows Hello..." if hello else "Authenticating V4 carrier...",
                 decrypt, result)

    def result(self, title, texts, files, repeat, *, details="", sharing="", formats=()):
        self.page(title, sharing)
        textual = {"Text Armor", "Camouflage Text"}
        text_parts = [i for i, fmt in enumerate(formats) if fmt in textual]
        file_parts = [i for i, fmt in enumerate(formats) if fmt not in textual]
        for i, name in enumerate(files):
            row = ttk.Frame(self.body, style="Panel.TFrame", padding=10)
            row.pack(fill="x", pady=5)
            label = f"Part {'AB'[file_parts[i]]} · {name}" if len(formats) == 2 else name
            ttk.Label(row, text=label, style="Panel.TLabel", wraplength=650).pack(side="left", fill="x", expand=True)
            self.button(row, "Copy Path", lambda value=name: self.copy(value)).pack(side="right")
        if files:
            self.button(self.body, "Open Folder", lambda: os.startfile(os.path.dirname(files[0]))).pack(anchor="w", pady=4)
        for i, text in enumerate(texts):
            row = ttk.Frame(self.body)
            row.pack(fill="both", expand=True, pady=5)
            label = f"Part {'AB'[text_parts[i]]}" if len(formats) == 2 else ("Part " + "AB"[i] if len(texts) > 1 else "Text")
            ttk.Label(row, text=label).pack(anchor="w")
            self.button(row, "Copy", lambda value=text: self.copy(value)).pack(anchor="e")
            widget = self.text_area(row, text, height=5 if len(texts) > 1 else 10)
            widget.pack(fill="both", expand=True)
            widget.configure(state="disabled")
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(8, 0))
        self.button(bar, "Home", self.home).pack(side="left")
        self.button(bar, "Repeat", repeat).pack(side="left", padx=6)
        if formats == ("Camouflage Text", "Camouflage Text"):
            self.button(bar, "Save Text Pair", lambda: self.save_text_pair(texts), primary=True).pack(side="right")
            self.button(bar, "Copy Both", lambda: self.copy("\n\n".join(texts))).pack(side="right", padx=6)
        if details:
            self.button(bar, "Details", lambda: messagebox.showinfo("Details", details, parent=self.root)).pack(side="left")

    def save_text_pair(self, texts):
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.directory.get())
        if not folder:
            return
        try:
            workflow.save_camouflage_pair(texts, folder)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
        else:
            self.status.set(f"Saved both .txt files in {folder}")

    def copy(self, value):
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status.set("Copied to clipboard.")

    def settings(self, return_to=None):
        self.page("Settings & Windows Hello", "Every saved change is authorized by the independent V4 service.")
        try:
            service = broker.status()
            service_note = "Windows Hello enrolled" if service["enrolled"] else "Enrollment required before using V4"
        except broker.BrokerError as error:
            service = {"enrolled": False, "settings": {}}
            service_note = str(error)
        cfg = service["settings"]
        directory = tk.StringVar(value=core.get_carrier_save_dir())
        key = tk.StringVar()
        binding = tk.StringVar(value=workflow.BOUND_LABEL if cfg.get("default_device_bound", True) else workflow.PORTABLE_LABEL)
        typing = tk.StringVar(value=_default_option(cfg, "default_typing", LOCAL_TYPING_CHOICES, "Off"))
        cover = tk.StringVar(value=_default_option(cfg, "default_cover", COVERS, "Off"))
        panel = ttk.Frame(self.body, style="Panel.TFrame", padding=16)
        panel.pack(fill="x", pady=(0, 12))
        ttk.Label(panel, text=service_note, style="PanelTitle.TLabel", wraplength=780).pack(anchor="w")
        ttk.Label(panel, text="Always on: signed Hello authorization, one-use challenges, 1-second delay, and 30-second retry protection after 3 failures.",
                  style="Panel.TLabel", wraplength=780).pack(anchor="w", pady=(6, 8))
        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(anchor="w")

        def unenroll():
            if messagebox.askokcancel(
                "Unenroll Windows Hello",
                "End all V4 sessions for this account? You can re-enroll to access your existing settings and files.",
                default=messagebox.CANCEL, icon=messagebox.WARNING, parent=self.root,
            ):
                owner = self.hello_owner()
                self.job("Verify with Windows Hello to remove the V4 enrollment...",
                         lambda: broker.call("unenroll", owner_hwnd=owner), lambda _: self.locked())

        if service["enrolled"]:
            self.button(actions, "Unenroll Windows Hello", unenroll).pack(side="left", padx=(0, 7))
        else:
            self.button(actions, "Enroll Windows Hello", self.locked).pack(side="left", padx=(0, 7))
        self.button(actions, "Windows Sign-in Options", lambda: os.startfile("ms-settings:signinoptions")).pack(side="left")
        self.folder_row(self.body, directory)
        ttk.Label(self.body, text="Protected verification storage: " + str(core.get_verification_data_dir()) +
                  "\nThis location is selected by the installer and protected by Windows permissions.",
                  style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(0, 10))
        for label, variable, choices in (
            ("Sharing mode", binding, (workflow.PORTABLE_LABEL, workflow.BOUND_LABEL)),
            ("Typing check (auxiliary)", typing, LOCAL_TYPING_CHOICES),
            ("Alternate document", cover, tuple(COVERS)),
        ):
            row = ttk.Frame(self.body)
            row.pack(anchor="w", pady=4)
            ttk.Label(row, text=label, width=25).pack(side="left")
            ttk.Combobox(row, textvariable=variable, values=choices, state="readonly", width=42).pack(side="left")
        ttk.Label(self.body, text=workflow.LOCAL_FEATURES, style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=10)
        ttk.Label(self.body, text="DeepSeek: " + core.deepseek_key_status(), font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Entry(self.body, textvariable=key, show="*", width=55).pack(anchor="w", pady=5)
        ttk.Label(self.body, text="Leave blank to keep the saved key. AI cover documents, camouflage templates, and AI styling require DeepSeek.",
                  style="Muted.TLabel", wraplength=800).pack(anchor="w")

        def save():
            owner = self.hello_owner()
            payload = {"settings": {
                "carrier_save_dir": directory.get(), "default_device_bound": binding.get() == workflow.BOUND_LABEL,
                "default_typing": typing.get(), "default_cover": cover.get(),
                "require_windows_hello": True, "app_retry_cooldown": True,
            }, "api_key": key.get().strip()}
            self.job("Authorize the exact settings change with Windows Hello...",
                     lambda: broker.call("save_settings", payload, owner_hwnd=owner), lambda _: self.settings(return_to))

        def clear():
            if not messagebox.askokcancel("Clear Stored DeepSeek Key", "Remove the API key from the protected V4 service?",
                                          default=messagebox.CANCEL, icon=messagebox.WARNING, parent=self.root):
                return
            owner = self.hello_owner()
            self.job("Authorize API key removal with Windows Hello...", lambda: broker.call("clear_api_key", owner_hwnd=owner),
                     lambda _: self.settings(return_to))

        buttons = ttk.Frame(self.body)
        buttons.pack(anchor="w", pady=12)
        self.button(buttons, "Save with Windows Hello", save, primary=True).pack(side="left")
        self.button(buttons, "Clear Stored Key", clear).pack(side="left", padx=7)
        self.button(buttons, "Back", return_to or self.home).pack(side="left")

    def tools(self):
        self.page("Format & About", crypto.PROTECTION_LABEL)
        card = ttk.Frame(self.body, style="Panel.TFrame", padding=16)
        card.pack(fill="x", pady=(0, 10))
        if self.brand_mark is not None:
            mark = self.brand_photo(round(102 * self.scale))
            logo = ttk.Label(card, image=mark, style="Panel.TLabel")
            logo.image = mark
            logo.pack(side="left", padx=(0, 14))
        credit = ttk.Frame(card, style="PanelContent.TFrame")
        credit.pack(side="left", anchor="w")
        ttk.Label(credit, text="Shadow Enclave V4", style="PanelTitle.TLabel",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(credit, text=f"Author: {core.AUTHOR}", style="Panel.TLabel").pack(anchor="w", pady=(3, 0))
        ttk.Label(credit, text=core.COPYRIGHT_NOTICE, style="Panel.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="Use a long, unique passphrase. Both parts are required for split formats.",
                  wraplength=720).pack(anchor="w", pady=4)
        ttk.Label(self.body, text=workflow.LOCAL_FEATURES,
                  wraplength=720).pack(anchor="w", pady=4)
        ttk.Label(self.body, text="This release only opens Shadow Enclave V4 carriers.").pack(anchor="w", pady=4)
        self.button(self.body, "Home", self.home).pack(anchor="w", pady=14)


def create_root():
    theme.enable_high_dpi()
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        return TkinterDnD.Tk(), DND_FILES
    except ImportError:
        return tk.Tk(), None


def main(*, open_settings=False):
    root, dnd_type = create_root()
    owns_session = not broker.session_is_open()
    Desktop(root, dnd_type, open_settings=open_settings)
    try:
        root.mainloop()
    finally:
        if owns_session:
            broker.close_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
