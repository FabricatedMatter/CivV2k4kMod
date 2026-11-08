"""Setup window in the style of Civilization V's menus (tkinter + Pillow).

The window art is the game's own main-menu panel frame, buttons and meter, read from the
player's game files at start (skin.py; nothing of the game ships with the installer).
On Windows the window is borderless and shaped by that frame.
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont

from PIL import Image, ImageTk

import setup_core as core
import skin

TITLE = "Civ V UI Scale"
KEY = "#ff00fe"                       # transparent colour of the shaped window (Windows)
CREAM = "#fffbe0"                     # Civ's label colour (255,255,200-ish)
GOLD = "#e0c27a"
MUTED = "#9aa9a6"
SHADOW = "#000000"
OK = "#8fd468"
WARN = "#efb84a"
ERR = "#ef6a5a"
BODY = ((20, 38, 46), (7, 13, 17))   # panel gradient, top to bottom
OUTSIDE = (10, 12, 14)                 # behind the frame where Windows cannot cut it out

W_UNITS = 620                          # layout in game pixels (1x), scaled by the DPI
H_UNITS = 510
PAD = 40
SHAPED = sys.platform == "win32"


def xft_dpi():
    try:
        import subprocess
        out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True,
                             timeout=3).stdout
        for line in out.splitlines():
            if line.lower().startswith("xft.dpi:"):
                return float(line.split(":")[1])
    except Exception:
        pass
    return None


def dpi_scale(root):
    if sys.platform == "win32":
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    dpi = xft_dpi()
    if dpi:
        # Tk turns pixel font sizes into points at "tk scaling", fontconfig renders them
        # at Xft.dpi: make both agree so font pixel sizes are exact
        root.tk.call("tk", "scaling", dpi / 72.0)
    env = os.environ.get("UISCALE_SETUP_SCALE")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    if dpi:
        return max(1.0, round(dpi / 96 * 4) / 4)
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        try:
            return max(1.0, float(os.environ[var]))
        except (KeyError, ValueError):
            pass
    return 1.0


def font_family(root):
    fams = {f.lower(): f for f in tkfont.families(root)}
    for f in ("Tw Cen MT", "Century Gothic", "URW Gothic", "Segoe UI", "Cantarell",
              "DejaVu Sans"):
        if f.lower() in fams:
            return fams[f.lower()]
    return "TkDefaultFont"


class Setup:
    def __init__(self, args=None):
        self.args = args
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        self.root = root = tk.Tk()
        root.title(TITLE)
        root.withdraw()
        s = dpi_scale(root)
        # never larger than the screen
        s = min(s, root.winfo_screenwidth() * 0.9 / W_UNITS,
                root.winfo_screenheight() * 0.9 / H_UNITS)
        self.s = s
        fam = font_family(root)
        tw = fam == "Tw Cen MT"   # Tw Cen MT runs small: same visual size as the game
        def f(px, bold=False):
            return tkfont.Font(root, family=fam, size=-max(8, round(px * s * (1.12 if tw else 1))),
                               weight="bold" if bold else "normal")
        self.f_title = f(17, True)
        self.f_head = f(13, True)
        self.f_body = f(14)
        self.f_small = f(12.5)
        self.f_button = f(15, True)
        self.f_mono = tkfont.Font(root, family="Consolas" if sys.platform == "win32"
                                  else "DejaVu Sans Mono", size=-max(8, round(11 * s)))

        self.games = core.locate.find_games(getattr(args, "game", None))
        first = getattr(args, "game", None) or (self.games[0][0] if self.games else "")
        self.art = skin.Art(first if core.locate.is_game_dir(first) else None)
        self.font_path = None
        self.busy = False
        self.view = None
        self._before_log = "form"
        self.images = []   # keep PhotoImages alive

        self.W, self.H = round(W_UNITS * s), round(H_UNITS * s)
        self.canvas = c = tk.Canvas(root, width=self.W, height=self.H, highlightthickness=0,
                                    bd=0, bg=KEY if SHAPED else "#%02x%02x%02x" % OUTSIDE)
        c.pack()
        self.draw_background()
        self.body_bg = self.sample_body(0.55)
        self.build()
        self.place_window()
        if args is not None and getattr(args, "install", False):
            root.after(400, self.do_install)

    # ------------------------------------------------------------ helpers
    def u(self, v):
        return round(v * self.s)

    def photo(self, img):
        p = ImageTk.PhotoImage(img)
        self.images.append(p)
        return p

    def scaled(self, img2x, w, h):
        """2x art -> on-screen size (w, h in units)."""
        return img2x.resize((max(1, self.u(w)), max(1, self.u(h))), Image.LANCZOS)

    def flatten(self, img):
        """RGBA -> opaque RGB; transparent pixels become the window's cut-out colour."""
        bg = Image.new("RGBA", img.size, OUTSIDE + (255,))
        bg.alpha_composite(img)
        out = bg.convert("RGB")
        if SHAPED:
            key = Image.new("RGB", img.size, tuple(int(KEY[i:i + 2], 16) for i in (1, 3, 5)))
            out = Image.composite(out, key, img.getchannel("A").point(lambda a: 255 if a >= 110
                                                                     else 0))
        return out

    def draw_background(self):
        bg = skin.window(self.art, W_UNITS * 2, H_UNITS * 2, BODY)
        self.bg_img = bg.resize((self.W, self.H), Image.LANCZOS)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo(self.flatten(self.bg_img)))

    def sample_body(self, fy):
        r, g, b, _ = self.bg_img.getpixel((self.W // 2, int(self.H * fy)))
        return "#%02x%02x%02x" % (r, g, b)

    def text(self, x, y, text="", font=None, color=CREAM, anchor="nw", width=0, tags=()):
        """Text with Civ's soft drop shadow; returns a small handle."""
        c = self.canvas
        o = max(1, self.u(1.2))
        kw = dict(text=text, font=font or self.f_body, anchor=anchor, tags=tags)
        if width:
            kw["width"] = self.u(width)
        sh = c.create_text(self.u(x) + o, self.u(y) + o, fill=SHADOW, **kw)
        fg = c.create_text(self.u(x), self.u(y), fill=color, **kw)
        return Text(c, sh, fg)

    # ------------------------------------------------------------ layout
    def build(self):
        c, u = self.canvas, self.u
        W = W_UNITS
        L, R = PAD, W - PAD
        # title on the frame's band; the band moves the window
        self.text(W / 2, 27, TITLE.upper(), self.f_title, GOLD, anchor="center")
        self.text(W - 66, 28, "v" + core.VERSION, self.f_small, MUTED, anchor="e")
        if SHAPED:
            self.close_btn = Sprite(self, haloed(skin.get(self.art, "close")), 64, 4,
                                    W - 52, 16, 22, 22, self.root.destroy)
        c.bind("<ButtonPress-1>", self.drag_start)
        c.bind("<B1-Motion>", self.drag_move)

        # ---- form view
        form = []
        y = 66
        form.append(self.text(L, y, "GAME FOLDER", self.f_head, GOLD))
        y += 24
        bw = 104
        self.field_img = self.make_field(L, y, R - L - bw - 10, 36)
        form.append(c.create_image(u(L), u(y), anchor="nw", image=self.field_img))
        self.game_var = tk.StringVar(value=self.first_game())
        self.entry = tk.Entry(c, textvariable=self.game_var, bd=0, highlightthickness=0,
                              relief="flat", bg=self.field_bg, fg=CREAM, insertbackground=CREAM,
                              disabledbackground=self.field_bg, disabledforeground=MUTED,
                              selectbackground="#3c7a80", font=self.f_body)
        form.append(c.create_window(u(L + 12), u(y + 18), anchor="w", window=self.entry,
                                    width=u(R - L - bw - 10 - 24 - (24 if len(self.games) > 1
                                                                       else 0)),
                                    height=u(26)))
        if len(self.games) > 1:
            pick = self.text(R - bw - 10 - 18, y + 18, "▾", self.f_body, GOLD,
                             anchor="center", tags=("pick",))
            form.append(pick)
            c.tag_bind("pick", "<Button-1>", self.pick_game)
        self.browse_btn = Button(self, "Browse", R - bw, y, bw, 36, self.browse_game)
        form.append(self.browse_btn)
        y += 46
        self.game_dot = c.create_oval(u(L + 1), u(y + 3), u(L + 11), u(y + 13), fill=MUTED,
                                      outline="")
        form.append(self.game_dot)
        self.game_status = self.text(L + 20, y, "", self.f_small, MUTED)
        form.append(self.game_status)

        y += 42
        form.append(self.divider(y))
        y += 18
        form.append(self.text(L, y, "FONT", self.f_head, GOLD))
        y += 26
        self.font_check = Check(self, L, y, "Use Tw Cen MT from this computer")
        form.append(self.font_check)
        y += 32
        self.font_info = self.text(L + 34, y, "", self.f_small, MUTED, width=R - L - 34)
        form.append(self.font_info)
        y += 54
        self.again_link = Link(self, L + 34, y, "Check again", self.refresh_font)
        self.ttf_link = Link(self, L + 34 + self.again_link.width + 22, y, "Choose .ttf file…",
                             self.browse_font)
        form += [self.again_link, self.ttf_link]
        self.form = form

        # ---- progress (shared): Civ's loading meter with the status inside
        y = 334
        self.meter_y = y
        trim = skin.get(self.art, "meter_trim")
        fill = skin.get(self.art, "meter_fill")
        self.fill2x = fill
        mx = (W - 300) / 2
        self.meter_trim = c.create_image(u(mx), u(y), anchor="nw",
                                         image=self.photo(self.scaled(trim, 300, 64)))
        self.meter_fill = c.create_image(u(mx + 11.5), u(y + 14), anchor="nw")
        self.meter_text = self.text(W / 2, y + 31, "", self.f_small, CREAM, anchor="center")
        self.set_meter(None)

        # ---- message view (results, errors)
        self.msg_head = self.text(L, 70, "", self.f_title, GOLD)
        self.msg_body = self.text(L, 106, "", self.f_body, CREAM, width=R - L)
        self.msg_links = []

        # ---- details view: the log in a sunken field
        self.log_frame = c.create_image(u(L), u(66), anchor="nw",
                                        image=self.make_field(L, 66, R - L, 280), state="hidden")
        self.log_box = tk.Text(c, bd=0, highlightthickness=0, wrap="word", bg=self.field_bg,
                               fg=MUTED, font=self.f_mono, relief="flat", padx=0, pady=0)
        self.log_box.configure(state="disabled")
        self.log_win = c.create_window(u(L + 12), u(76), anchor="nw", window=self.log_box,
                                       width=u(R - L - 24), height=u(260), state="hidden")

        # ---- bottom row
        by = H_UNITS - 54 - 52
        self.primary = Button(self, "Install", R - 160, by, 160, 42, self.on_primary,
                              font=self.f_button, primary=True)
        self.secondary = Button(self, "Uninstall", R - 160 - 12 - 132, by, 132, 42,
                                self.on_secondary, font=self.f_button)
        self.details = Link(self, L, by + 21, "Details", self.toggle_details, anchor="w")

        self.game_var.trace_add("write", lambda *a: self.refresh_game())
        self.refresh_game()
        self.refresh_font()
        self.show("form")

    def first_game(self):
        g = getattr(self.args, "game", None)
        return g or (self.games[0][0] if self.games else "")

    def make_field(self, x, y, w, h):
        img = skin.nine(skin.get(self.art, "field"), (0, 0, 54, 54), 20, (w * 2, h * 2))
        img = self.scaled(img, w, h)
        X, Y = self.u(x), self.u(y)
        bg = self.bg_img.crop((X, Y, X + img.width, Y + img.height))
        bg.alpha_composite(img)
        r, g, b, _ = bg.getpixel((img.width // 2, img.height // 2))
        self.field_bg = "#%02x%02x%02x" % (r, g, b)
        return self.photo(self.flatten(bg.convert("RGBA")))

    def divider(self, y):
        w = W_UNITS - 2 * PAD
        line = Image.new("RGBA", (self.u(w), max(1, self.u(1.5))), (0, 0, 0, 0))
        n = line.width
        for x in range(n):
            a = int(200 * min(1, 2.2 * min(x, n - 1 - x) / n))
            for yy in range(line.height):
                line.putpixel((x, yy), (196, 168, 104, a))
        return self.canvas.create_image(self.u(PAD), self.u(y), anchor="nw",
                                        image=self.photo(line))

    def place_window(self):
        r = self.root
        r.resizable(False, False)
        if SHAPED:
            r.overrideredirect(True)
            r.wm_attributes("-transparentcolor", KEY)
        else:
            try:  # tiling window managers: float like a dialog
                r.attributes("-type", "dialog")
            except Exception:
                pass
        x = (r.winfo_screenwidth() - self.W) // 2
        y = (r.winfo_screenheight() - self.H) // 2
        r.geometry("%dx%d+%d+%d" % (self.W, self.H, x, max(0, y)))
        r.iconphoto(True, self.photo(app_icon(64)))
        r.deiconify()
        if SHAPED:
            r.after(10, self.win_taskbar)
        r.bind("<Escape>", lambda e: None if self.busy else r.destroy())

    def win_taskbar(self):
        """Borderless windows get no taskbar button by default; ask for one."""
        try:
            import ctypes
            u32 = ctypes.windll.user32
            hwnd = u32.GetParent(self.root.winfo_id())
            st = u32.GetWindowLongW(hwnd, -20)
            u32.SetWindowLongW(hwnd, -20, (st & ~0x80) | 0x40000)
            self.root.withdraw()
            self.root.after(10, self.root.deiconify)
            self.root.after(30, self.root.focus_force)
        except Exception:
            pass

    def drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()) \
            if SHAPED and e.y < self.u(48) else None

    def drag_move(self, e):
        if getattr(self, "_drag", None):
            self.root.geometry("+%d+%d" % (e.x_root - self._drag[0], e.y_root - self._drag[1]))

    # ------------------------------------------------------------ views
    def show(self, view):
        c = self.canvas
        self.view = view
        on = "normal"
        for it in self.form:
            set_state(c, it, on if view == "form" else "hidden")
        for t in (self.msg_head, self.msg_body):
            t.state(on if view == "msg" else "hidden")
        for l in self.msg_links:
            l.state(on if view == "msg" else "hidden")
        for it in (self.log_frame, self.log_win):
            c.itemconfigure(it, state=on if view == "log" else "hidden")
        self.details.set_text("Hide details" if view == "log" else "Details")
        if view == "form":
            self.primary.set_text("Install")
            self.secondary.set_text("Uninstall")
        elif view == "msg":
            self.primary.set_text("Close")
            self.secondary.set_text("Back")

    def message(self, head, body, color=GOLD, links=()):
        for l in self.msg_links:
            l.destroy()
        self.msg_head.set(head, color)
        self.msg_body.set(body)
        self.msg_links = []
        self.show("msg")
        bbox = self.canvas.bbox(self.msg_body.fg)
        y = (bbox[3] / self.s if bbox else 140) + 16
        x = PAD
        for label, fn in links:
            l = Link(self, x, y, label, fn)
            self.msg_links.append(l)
            x += l.width + 24
        self.show("msg")

    def toggle_details(self):
        if self.view == "log":
            self.show(self._before_log or "form")
        else:
            self._before_log = self.view
            self.show("log")

    # ------------------------------------------------------------ state
    def refresh_game(self):
        g = self.game_var.get().strip()
        if not core.locate.is_game_dir(g):
            self.set_game_status("Choose the folder that contains CivilizationV_DX11.exe", WARN)
            return False
        st = core.dll_state(g)
        text, color = {
            "none": ("Civilization V found. Ready to install.", OK),
            "ours": ("Installed. Install again to update or change options.", OK),
            "foreign": ("Another version.dll is in the game folder (a different mod?). "
                        "Remove it first.", ERR)}[st]
        self.set_game_status(text, color)
        return st != "foreign"

    def set_game_status(self, text, color):
        self.game_status.set(text, color)
        self.canvas.itemconfigure(self.game_dot, fill=color)

    def refresh_font(self, path=None):
        p = path or core.find_font()
        self.font_path = p
        if p:
            self.font_check.set(True, enabled=True)
            self.font_info.set("Found: %s" % p, MUTED)
        else:
            self.font_check.set(False, enabled=False)
            self.font_info.set(core.FONT_HINT, WARN)

    def browse_game(self):
        d = filedialog.askdirectory(parent=self.root,
                                    title="Select the Sid Meier's Civilization V folder")
        if d:
            self.game_var.set(os.path.normpath(d))

    def pick_game(self, e=None):
        m = tk.Menu(self.root, tearoff=0, bg="#10202a", fg=CREAM, activebackground="#2c5a60",
                    activeforeground=CREAM, font=self.f_small, bd=0)
        for g, _ in self.games:
            m.add_command(label=g, command=lambda g=g: self.game_var.set(g))
        m.tk_popup(e.x_root if e else self.root.winfo_pointerx(),
                   e.y_root if e else self.root.winfo_pointery())

    def browse_font(self):
        p = filedialog.askopenfilename(parent=self.root, title="Select Tw Cen MT (.ttf)",
                                       filetypes=[("TrueType font", "*.ttf *.TTF"),
                                                  ("All files", "*")])
        if p:
            if core._is_tw_cen(p):
                self.refresh_font(p)
            else:
                self.font_info.set("That file is not Tw Cen MT (regular).", ERR)

    # ------------------------------------------------------------ actions
    def log(self, msg):
        def put():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            line = msg.strip().splitlines()[0] if msg.strip() else ""
            if self.busy and line:
                self._meter_label = line.rstrip(" .")
                self.set_meter(self._meter_frac, self._meter_label)
        self.root.after(0, put)

    def set_meter(self, frac, label=""):
        c = self.canvas
        self._meter_frac, self._meter_label = frac, label
        if frac is None:
            for it in (self.meter_trim, self.meter_fill):
                c.itemconfigure(it, state="hidden")
            self.meter_text.state("hidden")
            return
        for it in (self.meter_trim, self.meter_fill):
            c.itemconfigure(it, state="normal")
        w2 = max(1, int(self.fill2x.width * max(0.0, min(1.0, frac))))
        part = self.fill2x.crop((0, 0, w2, self.fill2x.height))
        img = part.resize((max(1, self.u(277 * w2 / self.fill2x.width)), self.u(36)),
                          Image.LANCZOS)
        self._fill_photo = ImageTk.PhotoImage(img)
        c.itemconfigure(self.meter_fill, image=self._fill_photo)
        self.meter_text.state("normal")
        self.meter_text.set(label if len(label) < 44 else label[:42] + "…")

    def progress(self, kind, done, total):
        verb = {"download": "Downloading art", "unlock": "Unlocking hi-res art"}.get(kind, kind)
        frac = done / max(total, 1)
        self.root.after(0, lambda: self.set_meter(frac, "%s  %d%%" % (verb, frac * 100)))

    def set_busy(self, busy):
        self.busy = busy
        for b in (self.primary, self.secondary, self.browse_btn):
            b.enable(not busy)
        self.entry.configure(state="disabled" if busy else "normal")
        if SHAPED:
            self.close_btn.enable(not busy)

    def run(self, task, done_head, game):
        if self.busy:
            return
        self.set_busy(True)
        self.set_meter(0.0, "Starting")

        def work():
            try:
                notes = task() or []
                self.root.after(0, lambda: self.finish(done_head, notes, None))
            except core.NeedsAdmin:
                self.root.after(0, self.elevate)
            except Exception as e:
                err = str(e) or e.__class__.__name__
                self.log("Error: " + err)
                self.root.after(0, lambda: self.finish(None, [], err))
        threading.Thread(target=work, daemon=True).start()

    def finish(self, head, notes, err):
        self.set_busy(False)
        self.set_meter(None)
        self.refresh_game()
        links = [("Details", self.toggle_details)]
        if err:
            if err == core.AV_BLOCKED:
                links.insert(0, ("Open Windows Security", core.open_protection_history))
            self.message("Something went wrong", err, ERR, links[:-1])
            return
        body = "\n\n".join(n for n in notes if "WINEDLLOVERRIDES" not in n)
        if any("WINEDLLOVERRIDES" in n for n in notes):
            body = ("One more step in Steam: right-click Civilization V › Properties › "
                    "Launch Options, and enter:\n\n" + LAUNCH_OPT + ("\n\n" + body if body else ""))
            links = [("Copy launch option", self.copy_launch)]
        else:
            links = []
        if not body:
            body = ("Start Civilization V and choose your size under Options › "
                    "Interface Options › UI Scale." if head == "Installed" else
                    "Civilization V is back to its original interface.")
        self.message(head, body, GOLD, links)

    def copy_launch(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(LAUNCH_OPT)
        self.msg_links[0].set_text("Copied")

    def elevate(self):
        self.set_busy(False)
        self.set_meter(None)
        args = ["--game", self.game_var.get().strip(), "--install"]
        args += ["--font", self.font_path] if self.font_check.value and self.font_path \
            else ["--no-font"]
        if getattr(self, "_uninstalling", False):
            args = ["--game", self.game_var.get().strip(), "--uninstall-gui"]
        if core.relaunch_as_admin(args):
            self.root.destroy()
        else:
            self.message("Administrator rights needed",
                         "Windows protects this folder. Run the installer as administrator "
                         "(right-click › Run as administrator).", ERR)

    def do_install(self):
        if not self.refresh_game():
            return
        game = self.game_var.get().strip()
        font = self.font_path if self.font_check.value else None
        self._uninstalling = False
        self.run(lambda: core.install(game, 2, font, log=self.log, progress=self.progress),
                 "Installed", game)

    def do_uninstall(self):
        if not self.refresh_game():
            return
        game = self.game_var.get().strip()
        self._uninstalling = True
        self.run(lambda: core.uninstall(game, log=self.log), "Uninstalled", game)

    def on_primary(self):
        if self.view == "msg":
            self.root.destroy()
        else:
            if self.view == "log":
                self.show("form")
            self.do_install()

    def on_secondary(self):
        if self.view == "msg":
            self.show("form")
        else:
            if self.view == "log":
                self.show("form")
            self.do_uninstall()


LAUNCH_OPT = 'WINEDLLOVERRIDES="version=n,b" %command%'


def set_state(c, item, state):
    if hasattr(item, "state"):
        item.state(state)
    else:
        c.itemconfigure(item, state=state)


# ---------------------------------------------------------------- widgets on the canvas
class Text:
    def __init__(self, canvas, sh, fg):
        self.c, self.sh, self.fg = canvas, sh, fg

    def set(self, text=None, color=None):
        if text is not None:
            self.c.itemconfigure(self.sh, text=text)
            self.c.itemconfigure(self.fg, text=text)
        if color is not None:
            self.c.itemconfigure(self.fg, fill=color)

    def state(self, st):
        self.c.itemconfigure(self.sh, state=st)
        self.c.itemconfigure(self.fg, state=st)


class Sprite:
    """Image button from a vertical state strip (normal, hover, pressed, disabled)."""

    def __init__(self, app, strip, cell, nstates, x, y, w, h, command):
        self.app, self.c, self.command = app, app.canvas, command
        self.frames = []
        for i in range(nstates):
            im = strip.crop((0, i * cell, cell, (i + 1) * cell))
            self.frames.append(app.photo(app.scaled(im, w, h)))
        self.tag = "sprite%d" % id(self)
        self.item = self.c.create_image(app.u(x), app.u(y), anchor="nw", image=self.frames[0],
                                        tags=(self.tag,))
        self.enabled, self.hover = True, False
        self.c.tag_bind(self.tag, "<Enter>", lambda e: self._set(hover=True))
        self.c.tag_bind(self.tag, "<Leave>", lambda e: self._set(hover=False))
        self.c.tag_bind(self.tag, "<ButtonPress-1>", lambda e: self._press())
        self.c.tag_bind(self.tag, "<ButtonRelease-1>", lambda e: self._release(e))

    def _frame(self, i):
        self.c.itemconfigure(self.item, image=self.frames[i])

    def _set(self, hover):
        self.hover = hover
        if self.enabled:
            self._frame(1 if hover else 0)
            self.c.configure(cursor="hand2" if hover else "")

    def _press(self):
        if self.enabled:
            self._frame(2)

    def _release(self, e):
        if not self.enabled:
            return
        self._frame(1 if self.hover else 0)
        if self.hover:
            self.command()

    def enable(self, on):
        self.enabled = on
        self._frame(0 if on else 3)


class Button(Sprite):
    """Civ's Grid9FrameButtons (BaseButton) with a label."""

    def __init__(self, app, text, x, y, w, h, command, font=None, primary=False):
        self.app, self.c, self.command = app, app.canvas, command
        strip = skin.get(app.art, "button")
        self.frames = []
        for i in range(4):
            im = skin.nine(strip, (0, i * 90, 88, 88), 16, (w * 2, h * 2))
            self.frames.append(app.photo(app.scaled(im, w, h)))
        self.tag = "btn%d" % id(self)
        self.item = self.c.create_image(app.u(x), app.u(y), anchor="nw", image=self.frames[0],
                                        tags=(self.tag,))
        self.color = GOLD if primary else CREAM
        self.label = app.text(x + w / 2, y + h / 2, text, font or app.f_body, self.color,
                              anchor="center", tags=(self.tag,))
        self.enabled, self.hover = True, False
        for ev, fn in (("<Enter>", lambda e: self._set(True)), ("<Leave>", lambda e: self._set(False)),
                       ("<ButtonPress-1>", lambda e: self._press()),
                       ("<ButtonRelease-1>", self._release)):
            self.c.tag_bind(self.tag, ev, fn)

    def _release(self, e):
        if not self.enabled:
            return
        items = self.c.find_overlapping(e.x, e.y, e.x, e.y)
        self._frame(1 if self.item in items else 0)
        if self.item in items:
            self.command()

    def set_text(self, t):
        self.label.set(t)

    def enable(self, on):
        super().enable(on)
        self.label.set(color=self.color if on else MUTED)

    def state(self, st):
        self.c.itemconfigure(self.item, state=st)
        self.label.state(st)


class Check:
    """Civ's checkbox orb with a label; clicking either toggles it."""

    def __init__(self, app, x, y, text):
        self.app, self.c = app, app.canvas
        strip = skin.get(app.art, "check")
        self.frames = [app.photo(app.scaled(strip.crop((0, i * 64, 64, (i + 1) * 64)), 24, 24))
                       for i in range(4)]
        self.tag = "chk%d" % id(self)
        self.item = self.c.create_image(app.u(x), app.u(y - 2), anchor="nw",
                                        image=self.frames[0], tags=(self.tag,))
        self.label = app.text(x + 34, y + 10, text, app.f_body, CREAM, anchor="w",
                              tags=(self.tag,))
        self.value, self.enabled = False, True
        self.c.tag_bind(self.tag, "<Button-1>", lambda e: self.toggle())
        self.c.tag_bind(self.tag, "<Enter>", lambda e: self.c.configure(
            cursor="hand2" if self.enabled else ""))
        self.c.tag_bind(self.tag, "<Leave>", lambda e: self.c.configure(cursor=""))

    def toggle(self):
        if self.enabled and not self.app.busy:
            self.set(not self.value)

    def set(self, value, enabled=None):
        if enabled is not None:
            self.enabled = enabled
        self.value = bool(value) and self.enabled
        # strip: 0 off, 1 on (lit), 2 on, 3 off (dark)
        self.c.itemconfigure(self.item, image=self.frames[1 if self.value else
                                                         (0 if self.enabled else 3)])
        self.label.set(color=CREAM if self.enabled else MUTED)

    def state(self, st):
        self.c.itemconfigure(self.item, state=st)
        self.label.state(st)


class Link:
    def __init__(self, app, x, y, text, command, anchor="nw"):
        self.app, self.c, self.command = app, app.canvas, command
        self.tag = "lnk%d" % id(self)
        self.t = app.text(x, y, text, app.f_small if anchor == "nw" else app.f_body, GOLD,
                          anchor=anchor, tags=(self.tag,))
        self.c.tag_bind(self.tag, "<Enter>", lambda e: self._hover(True))
        self.c.tag_bind(self.tag, "<Leave>", lambda e: self._hover(False))
        self.c.tag_bind(self.tag, "<Button-1>", lambda e: None if app.busy else command())

    @property
    def width(self):
        b = self.c.bbox(self.t.fg)
        return (b[2] - b[0]) / self.app.s if b else 0

    def _hover(self, on):
        self.t.set(color="#fff1c0" if on else GOLD)
        self.c.configure(cursor="hand2" if on else "")

    def set_text(self, t):
        self.t.set(t)

    def state(self, st):
        self.t.state(st)

    def destroy(self):
        self.c.delete(self.tag)


def haloed(img):
    """Light rim around dark art (the close X) so it reads on the frame's dark band."""
    from PIL import ImageFilter
    a = img.getchannel("A").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1))
    rim = Image.new("RGBA", img.size, (236, 222, 196, 0))
    rim.putalpha(a.point(lambda v: v * 200 // 255))
    rim.alpha_composite(img)
    return rim


# ---------------------------------------------------------------- icon
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")


def load_logo():
    """The installer's logo (installer/logo.png), RGBA, or None."""
    for p in (LOGO, os.path.join(core.BUNDLE, "installer", "logo.png")):
        try:
            return Image.open(p).convert("RGBA")
        except OSError:
            continue
    return None


def app_icon(size):
    """Taskbar / exe icon: the logo, or a drawn emblem without it."""
    logo = load_logo()
    if logo:
        return logo.resize((size, size), Image.LANCZOS)
    from PIL import ImageDraw
    k = 4
    n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, n - 1, n - 1), fill=(150, 120, 60, 255))
    d.ellipse((n * .06, n * .06, n * .94, n * .94), fill=(236, 206, 130, 255))
    d.ellipse((n * .13, n * .13, n * .87, n * .87), fill=(24, 58, 64, 255))
    d.ellipse((n * .17, n * .17, n * .83, n * .83), fill=(34, 84, 90, 255))
    # four-step pyramid: "scale up"
    for i in range(3):
        w = n * (.42 - i * .1)
        h = n * .09
        y = n * (.62 - i * .12)
        d.rectangle((n / 2 - w / 2, y, n / 2 + w / 2, y + h), fill=(236, 206, 130, 255))
    return img.resize((size, size), Image.LANCZOS)


def write_icon(path):
    app_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                    (128, 128), (256, 256)])


def run(args=None):
    app = Setup(args)
    if args is not None and getattr(args, "uninstall_gui", False):
        app.root.after(400, app.do_uninstall)
    app.root.mainloop()
