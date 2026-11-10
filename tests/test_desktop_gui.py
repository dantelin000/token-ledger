"""Opt-in native-window tests; no external apps or private source logs."""
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("TOKENLOG_GUI_TEST") == "1", "native GUI session required")
class DesktopGuiTests(unittest.TestCase):
    def test_small_window_can_scroll_to_controls_and_dialog_actions(self):
        import tkinter as tk
        from tkinter import ttk
        from tokenlog.adapters import AdapterRegistry
        from tokenlog.desktop import Desktop
        from tokenlog.dialogs import SourceManager, SourceEditor
        from tokenlog.i18n import Translator
        from tokenlog.price_ui import PriceEditor
        from tokenlog.project_ui import ProjectEditor
        from tokenlog.responsive import ScrollableFrame
        from tokenlog.settings import AppSettings
        from tokenlog.store import Ledger

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def assert_reachable(viewport, widget):
            viewport.canvas.xview_moveto(1)
            viewport.canvas.yview_moveto(1)
            root.update()
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            canvas = viewport.canvas
            self.assertTrue(widget.winfo_ismapped(), str(widget))
            self.assertGreaterEqual(x, canvas.winfo_rootx() - 1, str(widget))
            self.assertGreaterEqual(y, canvas.winfo_rooty() - 1, str(widget))
            self.assertLessEqual(x + widget.winfo_width(), canvas.winfo_rootx() + canvas.winfo_width() + 1, str(widget))
            self.assertLessEqual(y + widget.winfo_height(), canvas.winfo_rooty() + canvas.winfo_height() + 1, str(widget))

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            settings = AppSettings()
            ledger = Ledger(folder/'usage.db', folder/'codex', folder/'claude',
                registry=AdapterRegistry(include_builtin=False, extra_sources=[]))
            root = tk.Tk()
            try:
                app = Desktop(root, ledger, settings, folder/'settings.json')
                for timer in root.tk.call('after', 'info'):
                    root.after_cancel(timer)
                root.geometry('1024x640+0+35')
                app.report = ledger.report(include_calls=True)
                app.render()
                app.tabs.select(4)
                root.update()
                self.assertLess(app.content.canvas.yview()[1], 1)
                create = next(w for w in descendants(app.tabs.nametowidget(app.tabs.tabs()[4]))
                              if isinstance(w, ttk.Button) and w.cget('text') == 'New project')
                assert_reachable(app.content, create)
                app.content.canvas.yview_moveto(0)
                root.update()
                app.content.content.event_generate('<MouseWheel>', delta=-120)
                root.update()
                self.assertGreater(app.content.canvas.yview()[0], 0)
                for language in ('en', 'zh_CN'):
                    root.ui_language = language
                    editor = ProjectEditor(root, ledger, Translator(language))
                    manager_settings = AppSettings(language=language)
                    manager = SourceManager(root, manager_settings)
                    source = SourceEditor(root)
                    price = PriceEditor(root, ledger.pricebook)
                    for dialog, label in ((editor, '保存' if language == 'zh_CN' else 'Save'),
                                          (manager, '保存' if language == 'zh_CN' else 'Save'),
                                          (source, '保存数据源' if language == 'zh_CN' else 'Save source'),
                                          (price, '保存自定义单价' if language == 'zh_CN' else 'Save custom prices')):
                        dialog.geometry('800x500+0+35')
                        root.update()
                        viewport = next(w for w in dialog.winfo_children() if isinstance(w, ScrollableFrame))
                        button = next(w for w in descendants(viewport.content)
                                      if isinstance(w, ttk.Button) and w.cget('text') == label)
                        assert_reachable(viewport, button)
                        dialog.destroy()
                app.close()
            finally:
                try: root.destroy()
                except tk.TclError: pass

    def test_legend_call_values_and_native_dialogs(self):
        import tkinter as tk
        from tokenlog.desktop import Desktop, source_color
        from tokenlog.price_ui import PriceEditor, detail_text
        from tokenlog.settings import AppSettings
        from tokenlog.adapters import AdapterRegistry
        from tokenlog.store import Ledger, FIELDS
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            settings = AppSettings()
            for source in settings.sources:
                source.enabled = False
            ledger = Ledger(folder/"usage.db",folder/"codex",folder/"claude",registry=AdapterRegistry(include_builtin=False,extra_sources=settings.sources))
            event={"provider":"claude","event_key":"sample-call","session_id":"sample-session",
                "timestamp":"2026-11-01T00:00:00Z","model":"claude-opus-5-5","project":"sample-project",
                "input_tokens":1000,"output_tokens":100,"cached_input_tokens":400,"cache_write_tokens":200,
                "reasoning_tokens":50,"total_tokens":1100,"cache_write_short_tokens":100,
                "cache_write_long_tokens":100,"method":"message"}
            with ledger.connect() as connection:
                connection.execute(f"INSERT INTO usage({','.join(FIELDS)}) VALUES({','.join('?' for _ in FIELDS)})",[event[k] for k in FIELDS])
            root=tk.Tk()
            try:
                app=Desktop(root,ledger,settings,folder/"settings.json")
                for timer in root.tk.call('after','info'):
                    root.after_cancel(timer)
                app.report=ledger.report(include_calls=True)
                app.render()
                root.update()
                app.render_chart()
                self.assertGreaterEqual(app.chart.winfo_width(),40,
                    f"root={root.winfo_geometry()}, chart={app.chart.winfo_geometry()}, mapped={root.winfo_ismapped()}")
                self.assertIn('$',app.metric_values['api_estimate'].get())
                self.assertEqual(len(app.calls_table.get_children()),1)
                from tokenlog.project_ui import ProjectEditor
                editor = ProjectEditor(root, ledger, app.t)
                editor.name.set('Game')
                editor.widgets['paths']['available'].selection_set(0)
                editor.move('paths', True)
                editor.widgets['sessions']['available'].selection_set(0)
                editor.move('sessions', True)
                editor.save()
                app.report = ledger.report(include_calls=True)
                app.render()
                self.assertEqual(len(app.projects_table.get_children()), 1)
                self.assertEqual(app.report['tracked_projects'][0]['total_tokens'], 1100)
                app.projects_table.selection_set(app.projects_table.get_children()[0])
                app.edit_project()
                editor = next(w for w in root.winfo_children() if isinstance(w, ProjectEditor))
                from tokenlog.responsive import ScrollableFrame
                viewport = next(w for w in editor.winfo_children() if isinstance(w, ScrollableFrame))
                notebook = next(w for w in viewport.content.winfo_children() if isinstance(w, tk.ttk.Notebook))
                included = notebook.nametowidget(notebook.tabs()[-1])
                tree = next(w for frame in included.winfo_children() for w in frame.winfo_children() if isinstance(w, tk.ttk.Treeview))
                self.assertEqual(tree.item(tree.get_children()[0], 'values')[-1], '1,100')
                editor.destroy()
                self.assertTrue(app.chart_legend.winfo_children())
                actual_colors={app.chart.itemcget(i,'fill') for i in app.chart.find_all() if app.chart.type(i)=='rectangle'}
                self.assertIn(source_color('claude'),actual_colors)
                left,top,width,height=app.chart_bounds
                app.chart_motion(SimpleNamespace(x=left+width/2,y=top+height/2))
                self.assertIn('Claude Code',app.chart_hover.get())
                self.assertIn('Cache write',detail_text(app.report['calls'][0]))
                self.assertIn('Price source',detail_text(app.report['calls'][0]))
                self.assertEqual(app.period.get(),'All time')
                self.assertNotRegex(app.coverage_text.get('1.0','end'),r'[\u3400-\u9fff]')
                price=PriceEditor(root,ledger.pricebook,['claude-opus-5-5'])
                self.assertEqual(price.values['input'].get(),'4')
                self.assertNotRegex(price.source.get(),r'[\u3400-\u9fff]')
                price.destroy()
                app.sources()
                root.update_idletasks()
                dialogs=[w for w in root.winfo_children() if isinstance(w,tk.Toplevel)]
                self.assertEqual(len(dialogs),1)
                self.assertLessEqual(dialogs[0].winfo_height(), root.winfo_screenheight() - 50)
                self.assertEqual(dialogs[0].language.get(),'English')
                original_csv=ledger.export_csv()
                app.period.set('Custom')
                app.start.set('2026-11-01')
                app.end.set('2026-11-01')
                app.provider.set('Claude Code (claude)')
                app.search.set('sample')
                app.tabs.select(1)
                filters=app.filters()
                price=PriceEditor(root,ledger.pricebook,['claude-opus-5-5'])
                price.values['input'].set('4.5')
                dialogs[0].language.set('简体中文')
                with patch.object(app,'refresh'):
                    dialogs[0].accept()
                    root.update_idletasks()
                self.assertEqual(app.settings.language,'zh_CN')
                self.assertEqual(price.t.language,'zh_CN')
                self.assertEqual(price.values['input'].get(),'4.5')
                self.assertEqual(app.period.get(),'自定义')
                self.assertEqual(app.filters(),filters)
                self.assertEqual(app.search.get(),'sample')
                self.assertEqual(app.tabs.index(app.tabs.select()),1)
                self.assertEqual(ledger.export_csv(),original_csv)
                from tokenlog.settings import load_settings
                self.assertEqual(load_settings(folder/'settings.json').language,'zh_CN')
                self.assertIn('缓存写入',detail_text(app.report['calls'][0],language='zh_CN'))
                self.assertIn('数据覆盖',app.tabs.tab(3,'text'))
                # Reopen settings and switch back through the actual Save callback.
                app.sources()
                from tokenlog.dialogs import SourceManager
                dialog=next(w for w in root.winfo_children() if isinstance(w,SourceManager))
                self.assertEqual(dialog.language.get(),'简体中文')
                dialog.language.set('English')
                with patch.object(app,'refresh'):
                    dialog.accept()
                    root.update_idletasks()
                self.assertEqual(app.period.get(),'Custom')
                self.assertEqual(app.filters(),filters)
                self.assertEqual(app.settings.language,'en')
                self.assertEqual(price.t.language,'en')
                self.assertEqual(price.values['input'].get(),'4.5')
                self.assertNotRegex(app.coverage_text.get('1.0','end'),r'[\u3400-\u9fff]')
                app.close()
            finally:
                try: root.destroy()
                except tk.TclError: pass


if __name__=='__main__':
    unittest.main()
