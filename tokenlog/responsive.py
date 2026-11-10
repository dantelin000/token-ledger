"""Screen-aware windows and scrollable content for smaller displays."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


def fit_window(window, width, height, parent=None):
    """Keep initial windows inside the display, with room for OS chrome."""
    window.update_idletasks()
    screen_width, screen_height = window.winfo_screenwidth(), window.winfo_screenheight()
    width = min(width, max(320, screen_width - 48))
    height = min(height, max(240, screen_height - 110))
    if parent is None:
        x, y = max(0, (screen_width - width) // 2), 35
    else:
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        x = max(0, min(x, screen_width - width))
        y = max(35, min(y, screen_height - height - 50))
    window.geometry(f'{width}x{height}+{x}+{y}')


class ScrollableFrame(ttk.Frame):
    """Scrollable viewport; child widgets retain their own mouse-wheel behavior."""
    def __init__(self, parent, *, padding=0, background='#f3f5f8'):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0, borderwidth=0)
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.content = ttk.Frame(self.canvas, padding=padding)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', self._resize, add='+')
        self.canvas.bind('<Configure>', self._resize, add='+')
        # One dispatcher per Tcl interpreter. It finds the nearest viewport and
        # ignores lists, tables, text and other controls that handle their own wheel.
        root = self._root()
        if not getattr(root, '_tokenlog_scroll_bound', False):
            root.bind_all('<MouseWheel>', _dispatch_wheel, add='+')
            if sys.platform != 'darwin':
                root.bind_all('<Button-4>', _dispatch_wheel, add='+')
                root.bind_all('<Button-5>', _dispatch_wheel, add='+')
            root._tokenlog_scroll_bound = True

    def _resize(self, _event=None):
        width = max(self.canvas.winfo_width(), self.content.winfo_reqwidth())
        height = max(self.canvas.winfo_height(), self.content.winfo_reqheight())
        self.canvas.itemconfigure(self.item, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))


def _dispatch_wheel(event):
    widget = event.widget
    while widget is not None:
        if widget.winfo_class() in ('Treeview', 'Listbox', 'Text', 'TCombobox', 'TSpinbox', 'Spinbox'):
            return
        if isinstance(widget, ScrollableFrame):
            first, last = widget.canvas.yview()
            if first <= 0 and last >= 1:
                return
            if getattr(event, 'num', None) in (4, 5):
                units = -1 if event.num == 4 else 1
            else:
                delta = getattr(event, 'delta', 0)
                if not delta:
                    return
                units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
            widget.canvas.yview_scroll(units, 'units')
            return 'break'
        widget = widget.master
