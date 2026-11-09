#!/usr/bin/env python3
"""Civ V UI Scale setup: small GUI wizard (tkinter), text mode with --cli or no display.

  setup_app.py            GUI
  setup_app.py --cli      text prompts
  setup_app.py --cli --game DIR --scale 2 [--font PATH | --no-font] [--uninstall] [--yes]
"""
import argparse
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setup_core as core  # noqa: E402

TITLE = "Civ V UI Scale %s Setup" % core.VERSION


# ------------------------------------------------------------------ text mode
def _use_color(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return stream.isatty()
    except Exception:
        return False


COLOR = _use_color(sys.stdout)
if COLOR and sys.platform == "win32":
    os.system("")  # turns on ANSI escape handling in the Windows console


def c(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if COLOR else text


BOLD, DIM, RED, GREEN, YELLOW, BLUE, CYAN, GOLD = "1", "2", "31", "32", "33", "34", "36", "1;33"
# kind: (symbol, colour of the symbol, colour of the text)
KINDS = {
    "step": ("==>", "1;36", "1"),
    "info": ("  -", DIM, ""),
    "ok": ("  \u2713", "1;32", ""),
    "warn": ("  !", "1;33", "33"),
    "error": ("  \u2717", "1;31", "31"),
}


def say(msg, kind="info"):
    sym, sc, tc = KINDS.get(kind, KINDS["info"])
    if not COLOR and sym.strip() == "\u2713":
        sym = "  ok"
    elif not COLOR and sym.strip() == "\u2717":
        sym = "  !!"
    lines = str(msg).splitlines() or [""]
    print("%s %s" % (c(sc, sym), c(tc, lines[0]) if tc else lines[0]))
    for line in lines[1:]:
        print("%s %s" % (" " * len(sym), c(tc, line) if tc else line))


def ask(prompt, default=None):
    q = "%s %s%s: " % (c(GOLD, "  ?"), c(BOLD, prompt), c(DIM, " [%s]" % default) if default else "")
    try:
        v = input(q).strip()
    except EOFError:
        v = ""
    return v or default


class Bar:
    """One-line progress bar (download in MB, unlock in files)."""
    WIDTH = 30

    def __init__(self):
        self.kind = None
        self.last = -1

    def __call__(self, kind, done, total):
        if kind != self.kind:
            self.kind, self.last = kind, -1
        frac = done / max(total, 1)
        pct = int(frac * 100)
        if pct == self.last and done < total:
            return
        self.last = pct
        fill = int(frac * self.WIDTH)
        bar = c(GREEN, "\u2501" * fill) + c(DIM, "\u2501" * (self.WIDTH - fill))
        if kind == "download":
            what = "%6.1f / %.1f MB" % (done / 1e6, total / 1e6)
        else:
            what = "%d / %d files" % (done, total)
        label = {"download": "Downloading", "unlock": "Unlocking  "}.get(kind, kind)
        if sys.stdout.isatty():
            print("\r    %s %s %3d%%  %s" % (label, bar, pct, c(DIM, what)), end="", flush=True)
            if done >= total:
                print()
        elif done >= total:
            print("    %s %s" % (label, what))


def banner():
    title = " Civ V UI Scale %s " % core.VERSION
    print()
    print(c(GOLD, "\u2554" + "\u2550" * len(title) + "\u2557"))
    print(c(GOLD, "\u2551") + c("1;97", title) + c(GOLD, "\u2551"))
    print(c(GOLD, "\u255a" + "\u2550" * len(title) + "\u255d"))
    print()


def cli(a):
    banner()
    games = core.locate.find_games(a.game)
    game = a.game or (games[0][0] if games else None)
    if not a.yes:
        if games:
            say("Found Civilization V:", "step")
            for i, (g, _) in enumerate(games):
                print("    %s %s" % (c(GOLD, "%d)" % (i + 1)), g))
        v = ask("Game folder (number or path)", "1" if games else None)
        if v and v.isdigit() and games and 1 <= int(v) <= len(games):
            game = games[int(v) - 1][0]
        elif v:
            game = os.path.expanduser(v)
    if not game or not core.locate.is_game_dir(game):
        say("Civilization V not found. Pass --game \"/path/to/Sid Meier's Civilization V\".",
            "error")
        sys.exit(1)
    if a.uninstall:
        core.uninstall(game, log=say)
        return
    scale = a.scale or 2
    font = None if a.no_font else (a.font or core.find_font())
    if font and not a.yes and not a.font:
        if (ask("Use your installed Tw Cen MT (%s)? y/n" % font, "y") or "y").lower() != "y":
            font = None
    elif not font and not a.no_font:
        say("Tw Cen MT is not installed. " + core.FONT_HINT, "warn")
    print()
    notes = core.install(game, scale, font, log=say, progress=Bar())
    for n in notes:
        print()
        if "WINEDLLOVERRIDES" in n:
            head, opt = n.split("\n", 1)
            say("One more step. " + head, "step")
            print("    " + c("1;97;44", " " + opt.strip() + " "))
        else:
            say(n, "warn")
    print()


# ------------------------------------------------------------------ GUI
def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(TITLE)
    root.minsize(640, 600)
    pad = {"padx": 10, "pady": 4}
    # Tk may only be touched from the main thread: the worker hands UI updates over here
    import queue
    ui_q = queue.Queue()

    def ui(fn):
        ui_q.put(fn)

    def pump():
        try:
            for _ in range(200):
                ui_q.get_nowait()()
        except queue.Empty:
            pass
        try:
            root.after(40, pump)
        except tk.TclError:
            pass
    root.after(40, pump)

    games = core.locate.find_games()
    game_var = tk.StringVar(value=games[0][0] if games else "")
    font_path = tk.StringVar(value="")
    use_font = tk.BooleanVar(value=False)

    ttk.Label(root, text=TITLE, font=("TkDefaultFont", 14, "bold")).pack(anchor="w", **pad)
    def wrapping(parent, **kw):
        lab = ttk.Label(parent, justify="left", **kw)
        lab.bind("<Configure>", lambda e: lab.config(wraplength=max(200, e.width - 4)))
        return lab

    # game folder
    f = ttk.LabelFrame(root, text="Game folder")
    f.pack(fill="x", **pad)
    combo = ttk.Combobox(f, textvariable=game_var, values=[g for g, _ in games])
    combo.pack(side="left", fill="x", expand=True, padx=6, pady=6)

    def browse_game():
        d = filedialog.askdirectory(title="Select the Sid Meier's Civilization V folder")
        if d:
            game_var.set(d)
    ttk.Button(f, text="Browse...", command=browse_game).pack(side="left", padx=6)
    game_status = ttk.Label(root, text="")
    game_status.pack(anchor="w", padx=16)

    # font
    f = ttk.LabelFrame(root, text="Font")
    f.pack(fill="x", **pad)
    font_check = ttk.Checkbutton(f, text="Use the real Tw Cen MT font (best quality)",
                                 variable=use_font)
    font_check.pack(anchor="w", padx=6, pady=(6, 0))
    font_info = wrapping(f, text="")
    font_info.pack(anchor="w", fill="x", padx=24, pady=4)
    fb = ttk.Frame(f)
    fb.pack(anchor="w", padx=20, pady=(0, 6))

    def refresh_font(path=None):
        p = path or core.find_font()
        if p:
            font_path.set(p)
            use_font.set(True)
            font_check.state(["!disabled"])
            font_info.config(text="Found: %s" % p)
        else:
            font_path.set("")
            use_font.set(False)
            font_check.state(["disabled"])
            font_info.config(text=core.FONT_HINT)

    def browse_font():
        p = filedialog.askopenfilename(title="Select Tw Cen MT (.ttf)",
                                       filetypes=[("TrueType font", "*.ttf *.TTF"), ("All", "*")])
        if p:
            if core._is_tw_cen(p):
                refresh_font(p)
            else:
                messagebox.showwarning(TITLE, "That file does not look like Tw Cen MT (regular).")
    ttk.Button(fb, text="Check again", command=refresh_font).pack(side="left")
    ttk.Button(fb, text="Select .ttf file...", command=browse_font).pack(side="left", padx=6)
    refresh_font()

    # buttons stay at the bottom; progress + log take the remaining space
    btns = ttk.Frame(root)
    btns.pack(side="bottom", fill="x", **pad)
    bar = ttk.Progressbar(root, mode="determinate")
    bar.pack(fill="x", **pad)
    logbox = tk.Text(root, height=6, wrap="word", state="disabled")
    logbox.pack(fill="both", expand=True, **pad)

    def log(msg, kind=None):
        def put():
            logbox.config(state="normal")
            logbox.insert("end", msg + "\n")
            logbox.see("end")
            logbox.config(state="disabled")
        ui(put)

    def progress(kind, d, t):
        ui(lambda: bar.config(maximum=max(t, 1), value=d))

    def check_game(*_):
        g = game_var.get()
        if not core.locate.is_game_dir(g):
            game_status.config(text="Select the folder that contains CivilizationV_DX11.exe.")
            return False
        st = core.dll_state(g)
        game_status.config(text={"none": "Ready to install.",
                                 "ours": "Installed. Install again to update or change options.",
                                 "foreign": "Another %s is present (a different mod?). " % core.DLL_NAME +
                                            "Remove it first."}[st])
        return st != "foreign"
    game_var.trace_add("write", check_game)
    check_game()

    def run(task):
        for b in btns.winfo_children():
            b.state(["disabled"])

        def work():
            try:
                notes = task() or []
                for n in notes:
                    log("\n" + n)
                if notes and sys.platform != "win32":
                    ui(lambda: messagebox.showinfo(TITLE, "\n\n".join(notes)))
            except Exception as e:
                log("Error: %s" % e)
                ui(lambda m=str(e): messagebox.showerror(TITLE, m))
            finally:
                ui(lambda: [b.state(["!disabled"]) for b in btns.winfo_children()])
                ui(check_game)
        threading.Thread(target=work, daemon=True).start()

    def do_install():
        if not check_game():
            return
        run(lambda: core.install(game_var.get(), 2,
                                 font_path.get() if use_font.get() else None,
                                 log=log, progress=progress))

    def do_uninstall():
        if check_game() and messagebox.askyesno(TITLE, "Remove Civ V UI Scale from this game?"):
            run(lambda: core.uninstall(game_var.get(), log=log))

    ttk.Button(btns, text="Install", command=do_install).pack(side="right", padx=4)
    ttk.Button(btns, text="Uninstall", command=do_uninstall).pack(side="right", padx=4)
    ttk.Button(btns, text="Close", command=root.destroy).pack(side="left", padx=4)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument("--cli", action="store_true", help="text mode")
    ap.add_argument("--game")
    ap.add_argument("--scale", type=int, choices=[2, 3], help=argparse.SUPPRESS)
    ap.add_argument("--font", help="path of your Tw Cen MT .ttf")
    ap.add_argument("--no-font", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--yes", action="store_true", help="no questions (with --cli)")
    # used when the window restarts itself as administrator (Windows)
    ap.add_argument("--install", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--uninstall-gui", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--write-icon", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.write_icon:  # build step: the exe icon
        import setup_gui
        return setup_gui.write_icon(a.write_icon)
    if a.cli or a.uninstall or (sys.platform != "win32" and not os.environ.get("DISPLAY")
                                and not os.environ.get("WAYLAND_DISPLAY")):
        return run_cli(a)
    # Civ V styled window (tkinter + Pillow), else the plain Tk window, else text mode
    try:
        import setup_gui
        return setup_gui.run(a)
    except Exception as e:
        styled_err = e
    try:
        say("Styled window unavailable (%s); using the plain window." % styled_err, "warn")
        return gui()
    except Exception as e:
        say("No window available (%s); using text mode." % e, "warn")
    run_cli(a)


def run_cli(a):
    try:
        cli(a)
    except (ValueError, OSError) as e:
        say("Error: %s" % e, "error")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        say("Cancelled.", "warn")
        sys.exit(130)


if __name__ == "__main__":
    main()
