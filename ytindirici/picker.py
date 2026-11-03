"""Window for ticking which playlist videos to download."""
import tkinter as tk
from tkinter import messagebox, ttk

from .i18n import t
from .utils import format_duration, parse_ranges

CHECKED, UNCHECKED = "☑", "☐"


class PickerWindow(tk.Toplevel):
    def __init__(self, master, title, items, selected, on_ok):
        """items: [(index, title, duration)], selected: set of indices or None (= all)."""
        super().__init__(master)
        self.title(t("picker_title", title=title))
        self.geometry("820x620")
        self.minsize(600, 400)
        self.transient(master)
        self.grab_set()

        self.items = items
        self.durations = {i: d or 0 for i, _, d in items}
        self.on_ok = on_ok
        self.checked = {i for i, _, _ in items} if selected is None else set(selected)
        self.anchor = None  # last clicked index, for shift+click
        self.query = tk.StringVar()
        self.range_text = tk.StringVar()
        self.info = tk.StringVar()

        self._build()
        self._fill()

    def _build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text=t("search")).pack(side="left")
        search = ttk.Entry(top, textvariable=self.query)
        search.pack(side="left", fill="x", expand=True, padx=(8, 0))
        search.focus()
        self.query.trace_add("write", lambda *_: self._fill())

        tools = ttk.Frame(root)
        tools.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        ttk.Button(tools, text=t("all"), command=lambda: self._set_visible(True)).pack(side="left")
        ttk.Button(tools, text=t("none"), command=lambda: self._set_visible(False)).pack(side="left", padx=6)
        ttk.Button(tools, text=t("invert"), command=self._invert).pack(side="left")
        ttk.Button(tools, text=t("mark"), command=self._mark_range).pack(side="right")
        rng = ttk.Entry(tools, textvariable=self.range_text, width=16)
        rng.pack(side="right", padx=6)
        rng.bind("<Return>", lambda e: self._mark_range())
        ttk.Label(tools, text=t("range")).pack(side="right")

        table = ttk.Frame(root)
        table.grid(row=2, column=0, sticky="nsew")
        self.tree = ttk.Treeview(table, columns=("check", "index", "title", "duration"),
                                 show="headings", selectmode="none")
        for col, text, width, stretch, anchor in (
                ("check", "", 40, False, "center"), ("index", "#", 54, False, "center"),
                ("title", t("col_title"), 520, True, "w"), ("duration", t("col_duration"), 80, False, "e")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, stretch=stretch, anchor=anchor)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Shift-Button-1>", self._on_shift_click)

        bottom = ttk.Frame(root)
        bottom.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(bottom, text=t("picker_hint"), style="Hint.TLabel").pack(side="left")
        ttk.Button(bottom, text=t("ok"), style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(bottom, text=t("close"), command=self.destroy).pack(side="right", padx=8)
        ttk.Label(bottom, textvariable=self.info, style="Strong.TLabel").pack(side="right", padx=12)

        self.bind("<Escape>", lambda e: self.destroy())

    def _visible(self):
        q = self.query.get().strip().lower()
        return [it for it in self.items if q in it[1].lower()]

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for index, title, duration in self._visible():
            mark = CHECKED if index in self.checked else UNCHECKED
            self.tree.insert("", "end", iid=str(index),
                             values=(mark, index, title, format_duration(duration)))
        self._update_info()

    def _update_info(self):
        total = sum(self.durations[i] for i in self.checked)
        text = t("selected", n=len(self.checked), total=len(self.items))
        self.info.set(f"{text}  ·  {format_duration(total)}" if total else text)

    def _set(self, index, state):
        (self.checked.add if state else self.checked.discard)(index)
        if self.tree.exists(str(index)):
            self.tree.set(str(index), "check", CHECKED if state else UNCHECKED)

    def _row_at(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        row = self.tree.identify_row(event.y)
        return int(row) if row else None

    def _on_click(self, event):
        index = self._row_at(event)
        if index is None:
            return
        self._set(index, index not in self.checked)
        self.anchor = index
        self._update_info()

    def _on_shift_click(self, event):
        index = self._row_at(event)
        if index is None:
            return "break"
        visible = [it[0] for it in self._visible()]
        if self.anchor not in visible:
            self._on_click(event)
            return "break"
        # apply the anchor's state to every visible row between the anchor and here
        state = self.anchor in self.checked
        a, b = sorted((visible.index(self.anchor), visible.index(index)))
        for i in visible[a:b + 1]:
            self._set(i, state)
        self.anchor = index
        self._update_info()
        return "break"

    def _set_visible(self, state):
        for index, _, _ in self._visible():
            self._set(index, state)
        self._update_info()

    def _invert(self):
        for index, _, _ in self._visible():
            self._set(index, index not in self.checked)
        self._update_info()

    def _mark_range(self):
        try:
            wanted = parse_ranges(self.range_text.get())
        except ValueError:
            messagebox.showwarning(t("invalid_range_title"), t("invalid_range"), parent=self)
            return
        for index in wanted & set(self.durations):
            self._set(index, True)
        self._update_info()

    def _ok(self):
        if not self.checked:
            messagebox.showwarning(t("no_selection_title"), t("no_selection"), parent=self)
            return
        self.on_ok(sorted(self.checked))
        self.destroy()
