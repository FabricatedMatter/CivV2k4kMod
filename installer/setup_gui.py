"""Modern setup window (CustomTkinter): dark, card layout, Civ-inspired teal and gold."""
import os
import sys
import threading

import customtkinter as ctk
from tkinter import filedialog

import setup_core as core

BG = "#0d1615"
CARD = "#15211f"
CARD_BORDER = "#22332f"
FIELD = "#0f1b19"
GOLD = "#d6a94e"
GOLD_HOVER = "#e4bb63"
GOLD_TEXT = "#1a1306"
TEXT = "#ece8dd"
MUTED = "#8e9a95"
OK = "#4cc38a"
WARN = "#e5a23a"
ERR = "#e5534b"



def linux_dpi_scale():
    """CustomTkinter scales for DPI on Windows / macOS only; on Linux take the desktop's
    scale from Xft.dpi (set by GNOME / KDE / most Wayland compositors for X11 apps), or
    GDK_SCALE / QT_SCALE_FACTOR."""
    try:
        import subprocess
        out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True,
                             timeout=3).stdout
        for line in out.splitlines():
            if line.lower().startswith("xft.dpi:"):
                return max(1.0, round(float(line.split(":")[1]) / 96 * 4) / 4)
    except Exception:
        pass
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        try:
            return max(1.0, float(os.environ[var]))
        except (KeyError, ValueError):
            pass
    return 1.0


class SetupApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        if sys.platform.startswith("linux"):
            # rounded corners: CustomTkinter's default needs its shape font registered with
            # fontconfig, which is unreliable on Linux; canvas circles need no font
            from customtkinter.windows.widgets.core_rendering import DrawEngine
            DrawEngine.preferred_drawing_method = "circle_shapes"
            sc = float(os.environ.get("UISCALE_SETUP_SCALE", 0)) or linux_dpi_scale()
            ctk.set_widget_scaling(sc)
            ctk.set_window_scaling(sc)
        super().__init__(fg_color=BG)
        self.title("Civ V UI Scale")
        if sys.platform != "win32":
            try:  # tiling window managers float dialogs: keep our own size
                self.attributes("-type", "dialog")
            except Exception:
                pass
        self.geometry("760x780")
        self.f_title = ctk.CTkFont(size=24, weight="bold")
        self.f_head = ctk.CTkFont(size=15, weight="bold")
        self.f_body = ctk.CTkFont(size=13)
        self.f_small = ctk.CTkFont(size=12)
        self.f_button = ctk.CTkFont(size=15, weight="bold")
        self.busy = False

        self.games = core.locate.find_games()
        self.font_path = None

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=(24, 0))

        # header
        ctk.CTkLabel(body, text="CIVILIZATION V", font=self.f_small, text_color=GOLD,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(body, text="UI Scale", font=self.f_title, text_color=TEXT,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(body, text="A crisp, readable interface at 1440p, 4K and beyond.",
                     font=self.f_body, text_color=MUTED, anchor="w").pack(fill="x", pady=(2, 18))

        # game card
        card = self.card(body, "Game")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18)
        self.game = ctk.CTkComboBox(row, values=[g for g, _ in self.games] or [""],
                                    width=420, height=38, font=self.f_body, fg_color=FIELD,
                                    border_color=CARD_BORDER, button_color=CARD_BORDER,
                                    button_hover_color=GOLD, dropdown_fg_color=CARD,
                                    command=lambda _: self.refresh_game())
        self.game.set(self.games[0][0] if self.games else "")
        self.game.pack(side="left", fill="x", expand=True)
        self.game.bind("<KeyRelease>", lambda _: self.refresh_game())
        self.ghost(row, "Browse", self.browse_game, width=96).pack(side="left", padx=(10, 0))
        self.game_status = self.status_line(card)

        # font card
        card = self.card(body, "Font")
        self.use_font = ctk.CTkSwitch(card, text="Use the real Tw Cen MT font", font=self.f_body,
                                      text_color=TEXT, text_color_disabled=MUTED,
                                      progress_color=GOLD, fg_color=CARD_BORDER,
                                      button_color=TEXT, button_hover_color="#ffffff")
        self.use_font.pack(anchor="w", padx=18)
        self.font_info = ctk.CTkLabel(card, text="", font=self.f_small, text_color=MUTED,
                                      anchor="w", justify="left", wraplength=640)
        self.font_info.pack(fill="x", padx=18, pady=(6, 4))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(anchor="w", padx=14, pady=(0, 14))
        self.link(row, "Check again", self.refresh_font).pack(side="left")
        self.link(row, "Choose .ttf file…", self.browse_font).pack(side="left", padx=8)

        # footer: progress, details, actions
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", side="bottom", padx=28, pady=(8, 22))
        # progress bar: only visible while working
        self.progress_row = ctk.CTkFrame(foot, fg_color="transparent", height=14)
        self.progress_row.pack(fill="x")
        self.progress = ctk.CTkProgressBar(self.progress_row, height=4, progress_color=GOLD,
                                           fg_color=CARD)
        self.progress.set(0)
        bar = ctk.CTkFrame(foot, fg_color="transparent")
        bar.pack(fill="x")
        self.status = ctk.CTkLabel(bar, text="", font=self.f_small, text_color=MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.install_btn = ctk.CTkButton(bar, text="Install", width=150, height=44,
                                         corner_radius=10, font=self.f_button, fg_color=GOLD,
                                         hover_color=GOLD_HOVER, text_color=GOLD_TEXT,
                                         command=self.do_install)
        self.install_btn.pack(side="right")
        self.uninstall_btn = self.ghost(bar, "Uninstall", self.do_uninstall, width=110, height=44)
        self.uninstall_btn.pack(side="right", padx=10)
        self.details_btn = self.link(bar, "Details", self.toggle_details)
        self.details_btn.pack(side="right", padx=4)

        self.log_box = ctk.CTkTextbox(self, height=140, font=ctk.CTkFont(family="monospace",
                                                                        size=11),
                                      fg_color=FIELD, text_color=MUTED, border_color=CARD_BORDER,
                                      border_width=1, corner_radius=10)
        self.log_box.configure(state="disabled")

        self.refresh_game()
        self.refresh_font()
        self._wrap_w = 0
        self.bind("<Configure>", self._rewrap)
        self.after(30, self._fit)

    def _fit(self):
        """Grow the window to its content (fonts and DPI differ between systems)."""
        self.update_idletasks()
        sc = ctk.ScalingTracker.get_window_scaling(self)
        need_h = int(self.winfo_reqheight() / sc) + 8
        need_w = max(760, int(self.winfo_reqwidth() / sc))
        self.geometry("%dx%d" % (need_w, need_h))
        self.minsize(640, need_h)

    def _rewrap(self, e):
        if e.widget is not self:
            return
        w = int(e.width / ctk.ScalingTracker.get_widget_scaling(self)) - 2 * 28 - 2 * 18 - 8
        if abs(w - self._wrap_w) > 8:
            self._wrap_w = w
            self.font_info.configure(wraplength=max(300, w))

    # ---------------------------------------------------------------- widgets
    def card(self, parent, title):
        c = ctk.CTkFrame(parent, fg_color=CARD, border_width=0, corner_radius=14)
        c.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(c, text=title, font=self.f_head, text_color=TEXT, anchor="w").pack(
            fill="x", padx=18, pady=(14, 10))
        return c

    def ghost(self, parent, text, cmd, width=100, height=38):
        return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=height,
                             corner_radius=10, font=self.f_body, fg_color="transparent",
                             border_width=1, border_color=CARD_BORDER, hover_color=CARD_BORDER,
                             text_color=TEXT)

    def link(self, parent, text, cmd):
        return ctk.CTkButton(parent, text=text, command=cmd, width=10, height=28,
                             font=self.f_small, fg_color="transparent", hover_color=CARD_BORDER,
                             text_color=GOLD)

    def status_line(self, parent):
        lab = ctk.CTkLabel(parent, text="", font=self.f_small, text_color=MUTED, anchor="w")
        lab.pack(fill="x", padx=18, pady=(8, 14))
        return lab

    # ---------------------------------------------------------------- state
    def refresh_game(self):
        g = self.game.get().strip()
        if not core.locate.is_game_dir(g):
            self.game_status.configure(text="●  Choose the folder that contains "
                                            "CivilizationV_DX11.exe", text_color=WARN)
            return False
        st = core.dll_state(g)
        text, color = {
            "none": ("●  Civilization V found. Ready to install.", OK),
            "ours": ("●  Installed. Install again to update or change options.", OK),
            "foreign": ("●  Another version.dll is in the game folder (a different mod?). "
                        "Remove it first.", ERR)}[st]
        self.game_status.configure(text=text, text_color=color)
        return st != "foreign"

    def refresh_font(self, path=None):
        p = path or core.find_font()
        self.font_path = p
        if p:
            self.use_font.configure(state="normal", button_color=TEXT)
            self.use_font.select()
            self.font_info.configure(text="Found on this computer: %s" % p, text_color=MUTED)
        else:
            self.use_font.deselect()
            self.use_font.configure(state="disabled", button_color=MUTED)
            self.font_info.configure(text=core.FONT_HINT, text_color=MUTED)

    def browse_game(self):
        d = filedialog.askdirectory(title="Select the Sid Meier's Civilization V folder")
        if d:
            self.game.set(d)
            self.refresh_game()

    def browse_font(self):
        p = filedialog.askopenfilename(title="Select Tw Cen MT (.ttf)",
                                       filetypes=[("TrueType font", "*.ttf *.TTF"),
                                                  ("All files", "*")])
        if p:
            if core._is_tw_cen(p):
                self.refresh_font(p)
            else:
                self.font_info.configure(text="That file is not Tw Cen MT (regular).",
                                         text_color=ERR)

    def toggle_details(self):
        if self.log_box.winfo_ismapped():
            self.log_box.pack_forget()
        else:
            self.log_box.pack(fill="x", side="bottom", padx=28, pady=(0, 4))

    # ---------------------------------------------------------------- actions
    def log(self, msg):
        def put():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            self.status.configure(text=msg.strip().splitlines()[0][:90] if msg.strip() else "")
        self.after(0, put)

    def set_progress(self, kind, done, total):
        self.after(0, lambda: self.progress.set(done / max(total, 1)))

    def run(self, task, done_text):
        if self.busy:
            return
        self.busy = True
        for b in (self.install_btn, self.uninstall_btn):
            b.configure(state="disabled")
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(0, 10))

        def work():
            try:
                notes = task() or []
                self.after(0, lambda: self.finish(done_text, notes, OK))
            except Exception as e:
                err = str(e)
                self.log("Error: " + err)
                self.after(0, lambda: self.finish(err, [], ERR))
        threading.Thread(target=work, daemon=True).start()

    def finish(self, text, notes, color):
        self.busy = False
        for b in (self.install_btn, self.uninstall_btn):
            b.configure(state="normal")
        self.progress.pack_forget()
        self.status.configure(text=text, text_color=color)
        for n in notes:
            self.log("\n" + n)
        if notes:
            NotesDialog(self, notes)
        self.refresh_game()

    def do_install(self):
        if not self.refresh_game():
            return
        game = self.game.get().strip()
        scale = 2  # 2x art: 1:1 at 4K, also used for 1440p and above 4K
        font = self.font_path if self.use_font.get() else None
        self.run(lambda: core.install(game, scale, font, log=self.log,
                                      progress=self.set_progress), "Installed. Enjoy!")

    def do_uninstall(self):
        if not self.refresh_game():
            return
        game = self.game.get().strip()
        self.run(lambda: core.uninstall(game, log=self.log), "Uninstalled.")


class NotesDialog(ctk.CTkToplevel):
    """Shown after installing: launch option (Linux) and where settings live."""

    def __init__(self, master, notes):
        super().__init__(master, fg_color=BG)
        self.title("Almost done")
        self.geometry("620x360")
        self.transient(master)
        ctk.CTkLabel(self, text="Almost done", font=master.f_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=24, pady=(20, 8))
        for n in notes:
            card = ctk.CTkFrame(self, fg_color=CARD, border_color=CARD_BORDER, border_width=1,
                                corner_radius=12)
            card.pack(fill="x", padx=24, pady=6)
            lab = ctk.CTkLabel(card, text=n, font=master.f_small, text_color=TEXT, anchor="w",
                               justify="left", wraplength=540)
            lab.pack(fill="x", padx=14, pady=10)
            if "WINEDLLOVERRIDES" in n:
                opt = 'WINEDLLOVERRIDES="version=n,b" %command%'

                def copy(o=opt):
                    self.clipboard_clear()
                    self.clipboard_append(o)
                master.link(card, "Copy launch option", copy).pack(anchor="w", padx=10,
                                                                   pady=(0, 8))
        ctk.CTkButton(self, text="Done", width=120, height=40, corner_radius=10,
                      font=master.f_button, fg_color=GOLD, hover_color=GOLD_HOVER,
                      text_color=GOLD_TEXT, command=self.destroy).pack(anchor="e", padx=24,
                                                                        pady=16)
        self.after(50, self.grab_set)


def run():
    SetupApp().mainloop()
