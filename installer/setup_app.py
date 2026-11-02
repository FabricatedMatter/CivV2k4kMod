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

TITLE = "Civ V UI Scale Setup"


# ------------------------------------------------------------------ text mode
def ask(prompt, default=None):
    try:
        v = input("%s%s: " % (prompt, " [%s]" % default if default else "")).strip()
    except EOFError:
        v = ""
    return v or default


def cli(a):
    games = core.locate.find_games(a.game)
    game = a.game or (games[0][0] if games else None)
    if not a.yes:
        if games:
            print("Found Civilization V:")
            for i, (g, _) in enumerate(games):
                print("  %d) %s" % (i + 1, g))
        v = ask("Game folder (number or path)", "1" if games else None)
        if v and v.isdigit() and games and 1 <= int(v) <= len(games):
            game = games[int(v) - 1][0]
        elif v:
            game = os.path.expanduser(v)
    if not game or not core.locate.is_game_dir(game):
        sys.exit("Civilization V not found. Pass --game \"/path/to/Sid Meier's Civilization V\".")
    if a.uninstall:
        core.uninstall(game)
        return
    scale = a.scale or 2
    font = None if a.no_font else (a.font or core.find_font())
    if font and not a.yes and not a.font:
        if (ask("Use your installed Tw Cen MT (%s)? y/n" % font, "y") or "y").lower() != "y":
            font = None
    elif not font and not a.no_font:
        print(core.FONT_HINT)

    def progress(kind, d, t):
        print("\r  %s %d / %d" % (kind, d, t), end="", flush=True)
        if d >= t:
            print()
    notes = core.install(game, scale, font, log=print, progress=progress)
    for n in notes:
        print("\n" + n)


# ------------------------------------------------------------------ GUI
def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(TITLE)
    root.minsize(640, 600)
    pad = {"padx": 10, "pady": 4}

    games = core.locate.find_games()
    game_var = tk.StringVar(value=games[0][0] if games else "")
    font_path = tk.StringVar(value="")
    use_font = tk.BooleanVar(value=False)

    ttk.Label(root, text=TITLE, font=("TkDefaultFont", 14, "bold")).pack(anchor="w", **pad)
    def wrapping(parent, **kw):
        lab = ttk.Label(parent, justify="left", **kw)
        lab.bind("<Configure>", lambda e: lab.config(wraplength=max(200, e.width - 4)))
        return lab
    wrapping(root, text="Makes the Civilization V interface crisp and readable at 1440p, 4K "
                        "and above.").pack(anchor="w", fill="x", **pad)

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

    def log(msg):
        def put():
            logbox.config(state="normal")
            logbox.insert("end", msg + "\n")
            logbox.see("end")
            logbox.config(state="disabled")
        root.after(0, put)

    def progress(kind, d, t):
        root.after(0, lambda: bar.config(maximum=max(t, 1), value=d))

    def check_game(*_):
        g = game_var.get()
        if not core.locate.is_game_dir(g):
            game_status.config(text="Select the folder that contains CivilizationV_DX11.exe.")
            return False
        st = core.dll_state(g)
        game_status.config(text={"none": "Ready to install.",
                                 "ours": "Installed. Install again to update or change options.",
                                 "foreign": "Another version.dll is present (a different mod?). "
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
                    root.after(0, lambda: messagebox.showinfo(TITLE, "\n\n".join(notes)))
            except Exception as e:
                log("Error: %s" % e)
                root.after(0, lambda: messagebox.showerror(TITLE, str(e)))
            finally:
                root.after(0, lambda: [b.state(["!disabled"]) for b in btns.winfo_children()])
                root.after(0, check_game)
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
    a = ap.parse_args()
    if a.cli or a.uninstall or (sys.platform != "win32" and not os.environ.get("DISPLAY")
                                and not os.environ.get("WAYLAND_DISPLAY")):
        try:
            return cli(a)
        except (ValueError, OSError) as e:
            sys.exit("Error: %s" % e)
    try:
        try:
            import setup_gui  # modern window (CustomTkinter)
        except ImportError:
            setup_gui = None
        if setup_gui:
            setup_gui.run()
        else:
            gui()
    except Exception as e:  # no Tk available: fall back to text mode
        print("GUI unavailable (%s); using text mode." % e)
        cli(a)


if __name__ == "__main__":
    main()
