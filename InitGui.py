
import os
import FreeCADGui as Gui

class IPNestingWorkbench(Workbench):
    MenuText = "IP - Nesting"
    ToolTip = "Optimal parts nesting on a sheet"
    
    # Using your color #CF3519 for the placeholder icon
    Icon = """
    /* XPM */
    static char * xpm_icon[] = {
    "16 16 2 1",
    "  c None",
    ". c #CF3519",
    "                ",
    "  ............  ",
    "  .          .  ",
    "  .  ......  .  ",
    "  .  .    .  .  ",
    "  .  .    .  .  ",
    "  .  ......  .  ",
    "  .          .  ",
    "  .  ......  .  ",
    "  .  .    .  .  ",
    "  .  .    .  .  ",
    "  .  ......  .  ",
    "  .          .  ",
    "  ............  ",
    "                ",
    "                "};
    """

    def Initialize(self):
        import IPNestingGui
        self.appendToolbar("IP Nesting Tools", ["IP_RunNesting"])

    def GetClassName(self): 
        return "Gui::PythonWorkbench"

class RunNestingCommand:
    def GetResources(self):
        # Path to your custom png icon
        icon_path = os.path.join(App.getUserAppDataDir(), "Mod", "IPNesting", "nesting_icon.png")
        
        return {
            'MenuText': 'Nesting Tool',
            'ToolTip': 'Open the nesting configuration panel',
            'Pixmap': icon_path if os.path.exists(icon_path) else 'Part_Box' # Fallback to standard icon
        }

    def Activated(self):
        # Create and show the panel
        import IPNestingGui
        panel = IPNestingGui.NestingTaskPanel()
        Gui.Control.showDialog(panel)

        # Automatic add: if user already has selection in the main document (not the preview),
        # call panel.add_selected_objects() so the selected bodies are copied into the preview/table.
        try:
            sel = Gui.Selection.getSelection()
            if sel:
                # ensure selection is not already from the preview document
                try:
                    first_doc_name = sel[0].Document.Name if getattr(sel[0], "Document", None) else None
                except Exception:
                    first_doc_name = None
                if first_doc_name and first_doc_name != panel.preview_doc_name:
                    try:
                        panel.add_selected_objects()
                    except Exception:
                        App.Console.PrintError("RunNestingCommand: failed to auto-add selected objects:\n" + traceback.format_exc())
        except Exception:
            # silently continue if selection can't be read
            pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"

Gui.addCommand('IP_RunNesting', RunNestingCommand())
Gui.addWorkbench(IPNestingWorkbench())