"""Exercise InitGui with separate loader globals and locals, as an executed script."""
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


# Check initialization and delayed command callbacks without leaked loader imports.
class InitializationTests(unittest.TestCase):
    # Register the workbench, then exercise commands after the loader locals disappear.
    def test_separate_loader_namespaces(self):
        source = (ROOT / 'InitGui.py').read_text(encoding='utf-8-sig')
        for code in ('en', 'lv'):
            with self.subTest(language=code):
                commands = {}
                workbenches = []
                gui = types.SimpleNamespace(
                    addCommand=lambda name, command: commands.update({name: command}),
                    addWorkbench=workbenches.append,
                    Control=types.SimpleNamespace(showDialog=Mock()),
                    Selection=types.SimpleNamespace(getSelection=lambda: [
                        types.SimpleNamespace(Document=types.SimpleNamespace(Name='Source'))
                    ]),
                )
                app = types.SimpleNamespace(Console=types.SimpleNamespace(PrintError=Mock()))
                panel = types.SimpleNamespace(preview_doc_name='Nesting_Preview',
                                              add_selected_objects=Mock(side_effect=RuntimeError('test')))
                modules = {
                    'FreeCAD': app,
                    'FreeCADGui': gui,
                    'IPNestingLanguages': types.SimpleNamespace(
                        tr=lambda key: code + ':' + key,
                        DIRECTORY=str(ROOT), SettingsCommand=Mock),
                    'IPNestingGui': types.SimpleNamespace(NestingTaskPanel=lambda: panel),
                }
                global_scope = {'Workbench': type('Workbench', (), {
                    'appendToolbar': Mock(), 'appendMenu': Mock()
                })}
                local_scope = {}
                with patch.dict('sys.modules', modules):
                    exec(compile(source, 'InitGui.py', 'exec'), global_scope, local_scope)
                    local_scope.clear()
                    self.assertEqual(workbenches[0].ToolTip, code + ':optimal_parts_nesting_on_a_sheet')
                    workbenches[0].Initialize()
                    self.assertEqual(set(commands), {'IP_RunNesting', 'IP_NestingSettings'})
                    run = commands['IP_RunNesting']
                    self.assertEqual(run.GetResources()['MenuText'], code + ':nesting_tool')
                    run.Activated()
                    gui.Control.showDialog.assert_called_once_with(panel)
                    panel.add_selected_objects.assert_called_once()
                    self.assertIn(code + ':runnestingcommand_failed_to_auto_add_selected_objects',
                                  app.Console.PrintError.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
