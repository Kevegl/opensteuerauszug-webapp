"""Kleines Statusfenster des Startprogramms (tkinter, Standardbibliothek).

Zeigt, dass der Steuerauszug-Assistent läuft, auch wenn der Browser geschlossen ist. Fenster schließen = beenden.
Fehlt tkinter (z. B. Linux ohne python3-tk) oder gibt es keinen Bildschirm, wirft der Import bzw. Tk() einen
Fehler; das Startprogramm läuft dann ohne Fenster weiter.
"""
import tkinter as tk
from tkinter import messagebox, ttk

TEXT = {
    "title": "Steuerauszug-Assistent",
    "starting": "Startet ...",
    "running": "Steuerauszug-Assistent läuft auf diesem Computer",
    "stopping": "Wird beendet ...",
    "open": "Im Browser öffnen",
    "data": "Datenordner öffnen",
    "quit": "Beenden",
    "hint": "Sie können dieses Fenster minimieren. Schließen beendet den Assistenten.",
    "confirm_title": "Beenden?",
    "confirm": "Der Steuerauszug-Assistent wird beendet. Ihre Daten bleiben gespeichert.",
}
COLOR = {"running": "#1f9d55", "stopping": "#8a8a8a"}


class StatusWindow:
    def __init__(self, launcher):
        self.l = launcher
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(TEXT["title"])
        self.root.resizable(False, False)
        frm = ttk.Frame(self.root, padding=(18, 14, 18, 16))
        frm.grid(sticky="nsew")
        ttk.Label(frm, text=TEXT["title"], font=("TkDefaultFont", 13, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        line = ttk.Frame(frm)
        line.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.dot = tk.Canvas(line, width=12, height=12, highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=0, padx=(0, 6))
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill="#d97706", outline="")
        self.status = ttk.Label(line)
        self.status.grid(row=0, column=1, sticky="w")
        ttk.Label(frm, text=launcher.url, foreground="#666666").grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Label(frm, text=TEXT["hint"], wraplength=360, foreground="#666666", justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 12))
        self.btn_open = ttk.Button(frm, text=TEXT["open"], command=launcher.open_in_browser)
        self.btn_open.grid(row=4, column=0, sticky="w")
        self.btn_data = ttk.Button(frm, text=TEXT["data"], command=launcher.open_data_folder)
        self.btn_data.grid(row=4, column=1, sticky="w", padx=(8, 0))
        self.btn_quit = ttk.Button(frm, text=TEXT["quit"], command=self.ask_quit)
        self.btn_quit.grid(row=4, column=2, sticky="e", padx=(8, 0))
        frm.columnconfigure(2, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.ask_quit)
        self.shown = None
        self.update_texts()
        self.root.deiconify()
        self.root.lift()

    def update_texts(self):
        state = self.l.status
        if state == self.shown:
            return
        self.shown = state
        self.status.configure(text=TEXT.get(state, TEXT["starting"]))
        self.dot.itemconfigure(self.dot_id, fill=COLOR.get(state, "#d97706"))
        self.btn_quit.configure(state="disabled" if state == "stopping" else "normal")

    def ask_quit(self):
        if self.l.stopping:
            return
        if messagebox.askokcancel(TEXT["confirm_title"], TEXT["confirm"], parent=self.root):
            self.l.request_stop()
            self.update_texts()

    def run(self):
        """Läuft im Hauptthread, bis die App beendet werden soll."""
        def poll():
            if self.l.should_stop():
                self.l.status = "stopping"
                self.root.destroy()
                return
            self.update_texts()
            self.root.after(300, poll)
        self.root.after(300, poll)
        self.root.mainloop()
