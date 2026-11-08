"""User-defined project meters; selections contain only ledger metadata."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox


class ProjectEditor(tk.Toplevel):
    def __init__(self, parent, ledger, translate, project=None, calls=None, on_save=None):
        super().__init__(parent)
        self.ledger, self.t, self.project, self.on_save = ledger, translate, project, on_save
        self.title(self.t('编辑项目') if project else self.t('新建项目'))
        self.geometry('1000x700')
        self.minsize(760, 540)
        self.name = tk.StringVar(value=project['name'] if project else '')
        self.selected = {
            'paths': set(project['paths'] if project else []),
            'sessions': set(tuple(s) for s in project['sessions']) if project else set(),
        }
        with ledger.connect() as c:
            self.paths = [r[0] for r in c.execute("SELECT DISTINCT project FROM usage WHERE project != '' AND lower(project) != 'unknown' ORDER BY project")]
            self.sessions = [(r['provider'], r['session_id'], r['project'], r['last_seen']) for r in c.execute(
                'SELECT provider,session_id,MAX(project) project,MAX(timestamp) last_seen FROM usage GROUP BY provider,session_id ORDER BY last_seen DESC')]
        self.path_labels = {p: p for p in self.paths}
        self.session_labels = {(p, s): f'{p}  |  {s}  |  {path}  |  {seen[:10]}' for p, s, path, seen in self.sessions}
        # Rules survive even if a parser later changes or the source disappears.
        for key in self.selected['paths']:
            self.path_labels.setdefault(key, key)
        for key in self.selected['sessions']:
            self.session_labels.setdefault(key, '  |  '.join(key))
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill='both', expand=True)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Label(row, text=self.t('项目名称')).pack(side='left', padx=(0, 8))
        ttk.Entry(row, textvariable=self.name, width=42).pack(side='left', fill='x', expand=True)
        ttk.Label(frame, text=self.t('选择已有目录以自动纳入该目录及子目录的记录；也可手动加入其他会话。'), wraplength=900).pack(anchor='w', pady=(12, 4))
        ttk.Label(frame, text=self.t('未知目录不会自动归组；按会话添加时会纳入该会话全部记录。'), wraplength=900).pack(anchor='w', pady=(0, 8))
        tabs = ttk.Notebook(frame)
        tabs.pack(fill='both', expand=True)
        self.widgets = {}
        for kind, title, labels in (('paths', self.t('自动目录'), self.path_labels), ('sessions', self.t('手动会话'), self.session_labels)):
            panel = ttk.Frame(tabs, padding=8)
            tabs.add(panel, text=title)
            search = tk.StringVar()
            bar = ttk.Frame(panel)
            bar.pack(fill='x', pady=(0, 5))
            ttk.Label(bar, text=self.t('搜索')).pack(side='left')
            ttk.Entry(bar, textvariable=search).pack(side='left', fill='x', expand=True, padx=8)
            lists = ttk.Frame(panel)
            lists.pack(fill='both', expand=True)
            lists.columnconfigure(0, weight=1, uniform='lists')
            lists.columnconfigure(2, weight=1, uniform='lists')
            lists.rowconfigure(1, weight=1)
            ttk.Label(lists, text=self.t('可选记录')).grid(row=0, column=0, sticky='w')
            ttk.Label(lists, text=self.t('已加入项目')).grid(row=0, column=2, sticky='w')
            available = tk.Listbox(lists, selectmode='extended', exportselection=False)
            chosen = tk.Listbox(lists, selectmode='extended', exportselection=False)
            available.grid(row=1, column=0, sticky='nsew')
            chosen.grid(row=1, column=2, sticky='nsew')
            controls = ttk.Frame(lists, padding=8)
            controls.grid(row=1, column=1)
            ttk.Button(controls, text='→', command=lambda k=kind: self.move(k, True)).pack(pady=4)
            ttk.Button(controls, text='←', command=lambda k=kind: self.move(k, False)).pack(pady=4)
            self.widgets[kind] = dict(search=search, available=available, chosen=chosen, labels=labels)
            search.trace_add('write', lambda *_, k=kind: self.populate(k))
            self.populate(kind)
        if project is not None:
            panel = ttk.Frame(tabs, padding=8)
            tabs.add(panel, text=self.t('统计内会话'))
            ttk.Label(panel, text=self.t('以下会话按当前日期和来源筛选显示；修改规则后请保存再查看。')).pack(anchor='w', pady=(0, 8))
            tree = ttk.Treeview(panel, columns=('provider', 'session', 'tokens'), show='headings')
            for key, label, width in (('provider', self.t('来源'), 140), ('session', self.t('会话 ID'), 500), ('tokens', self.t('Token 合计'), 170)):
                tree.heading(key, text=label)
                tree.column(key, width=width, anchor='e' if key == 'tokens' else 'w')
            tree.pack(fill='both', expand=True)
            groups = {}
            for call in calls or []:
                if ledger.project_matches(project, call):
                    key = (call['provider'], call['session_id'])
                    groups[key] = groups.get(key, 0) + call['total_tokens']
            for (provider, session), amount in sorted(groups.items(), key=lambda item: -item[1]):
                tree.insert('', 'end', values=(provider, session, f'{amount:,}'))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(12, 0))
        ttk.Button(buttons, text=self.t('取消'), command=self.destroy).pack(side='right')
        ttk.Button(buttons, text=self.t('保存'), command=self.save).pack(side='right', padx=8)
        self.transient(parent)

    def populate(self, kind):
        w = self.widgets[kind]
        query = w['search'].get().strip().lower()
        labels = w['labels']
        w['available_keys'] = [key for key, label in labels.items() if key not in self.selected[kind] and query in label.lower()]
        w['chosen_keys'] = [key for key in labels if key in self.selected[kind]]
        for listname, keys in (('available', w['available_keys']), ('chosen', w['chosen_keys'])):
            box = w[listname]
            box.delete(0, 'end')
            for key in keys:
                box.insert('end', labels[key])

    def move(self, kind, adding):
        w = self.widgets[kind]
        side = 'available' if adding else 'chosen'
        keys = [w[side + '_keys'][i] for i in w[side].curselection()]
        if adding:
            self.selected[kind].update(keys)
        else:
            self.selected[kind].difference_update(keys)
        self.populate(kind)

    def save(self):
        try:
            self.ledger.save_tracked_project(self.name.get(), self.selected['paths'], self.selected['sessions'],
                self.project['id'] if self.project else None)
        except (ValueError, OSError) as exc:
            messagebox.showerror(self.t('无法保存项目'), self.t.message(str(exc)), parent=self)
            return
        self.destroy()
        if self.on_save:
            self.on_save()
