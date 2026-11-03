"""Double-click launcher (no console window)."""
import sys

try:
    from ytindirici.app import main
except ImportError as e:
    import tkinter as tk
    from tkinter import messagebox

    tk.Tk().withdraw()
    messagebox.showerror("YT İndirici", f"{e}\n\npip install -r requirements.txt")
    sys.exit(1)

main()
