"""Run with FreeCAD's Python: python tests/freecad_geometry_regression.py."""
import sys, unittest, math, json, tempfile
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import FreeCAD as App, Part
from PySide import QtCore
import IPNestingExport as E
import IPNestingOffcuts as O
import IPNestingResult as R
from IPNestingRelayout import NestingRelayoutManager

class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.doc=App.newDocument('Geometry_Test')
    def tearDown(self):
        for name in list(App.listDocuments()): App.closeDocument(name)
    def obj(self, name, shape):
        o=self.doc.addObject('Part::Feature',name);o.Shape=shape;self.doc.recompute();return o
    def test_ring_has_one_outer_and_one_hole(self):
        ring=self.obj('Ring',Part.makeCylinder(44,5).cut(Part.makeCylinder(31,5)))
        outer, holes, _=E._extract_part_contours(ring)
        self.assertEqual(len(holes),1)
        self.assertAlmostEqual(E._polygon_area(outer),math.pi*44**2,delta=3)
        self.assertAlmostEqual(E._polygon_area(holes[0]),math.pi*31**2,delta=3)
        self.assertEqual(len(outer),len({tuple(p) for p in outer}))
    def test_rotation_is_applied_once(self):
        obj=self.obj('Box',Part.makeBox(30,10,2))
        for angle in (0,30,90,180):
            obj.Placement=App.Placement(App.Vector(100,50,0),App.Rotation(App.Vector(0,0,1),angle))
            points=E._extract_part_points(obj);bb=obj.Shape.BoundBox
            bounds=E._polygon_bbox(points)
            self.assertAlmostEqual(bounds[2]-bounds[0],bb.XLength,places=5)
            self.assertAlmostEqual(bounds[3]-bounds[1],bb.YLength,places=5)
    def test_selected_hole_follows_rotation(self):
        obj=self.obj('Plate',Part.makeBox(40,20,2).cut(Part.makeBox(10,5,2,App.Vector(5,5,0))))
        _,holes,_=E._extract_part_contours(obj)
        panel=NS(_part_holes={})
        E.remember_hole_selection(panel,obj,holes)
        obj.Placement=App.Placement(App.Vector(100,50,0),App.Rotation(App.Vector(0,0,1),90))
        selected=E.current_selected_holes(panel,obj)
        self.assertTrue(E._polygons_same_2d(selected[0],E._extract_part_contours(obj)[1][0]))
        obj.Shape=Part.makeBox(40,20,2)
        with self.assertRaises(ValueError): E.current_selected_holes(panel,obj)
    def test_curved_offcut(self):
        arc=Part.Arc(App.Vector(10,0,0),App.Vector(math.sqrt(50),math.sqrt(50),0),App.Vector(0,10,0)).toShape()
        wire=Part.Wire([arc,Part.makeLine(App.Vector(0,10,0),App.Vector()),Part.makeLine(App.Vector(),App.Vector(10,0,0))])
        poly=O._wire_to_polyline_2d(wire,.01)
        self.assertGreater(len(poly),3)
        self.assertAlmostEqual(E._polygon_area(poly),Part.Face(wire).Area,delta=.2)
    def test_distinct_boundaries_not_deduplicated(self):
        a=[[0,0],[2,0],[0,2]];b=[[0,0],[2,0],[2,2]]
        self.assertFalse(E._polygons_same_2d(a,b));self.assertFalse(O._polygons_are_same(a,b))
        self.assertTrue(E._polygons_same_2d(a,list(reversed(a[1:]+a[:1]))))
    def test_relayout_preserves_angle(self):
        o=self.obj('Box',Part.makeBox(30,10,2));o.Placement.Rotation=App.Rotation(App.Vector(0,0,1),30)
        before=o.Shape.BoundBox
        NestingRelayoutManager().relayout_preview(self.doc)
        self.assertAlmostEqual(before.XLength,o.Shape.BoundBox.XLength)
        self.assertAlmostEqual(before.YLength,o.Shape.BoundBox.YLength)
    def test_reject_disconnected_and_nonprismatic_parts(self):
        disconnected=Part.makeCompound([Part.makeBox(10,10,2),Part.makeBox(10,10,2,App.Vector(30,0,0))])
        with self.assertRaises(ValueError): E._extract_part_points(self.obj('Disconnected',disconnected))
        blind=Part.makeBox(30,30,5).cut(Part.makeCylinder(5,2,App.Vector(15,15,3)))
        with self.assertRaises(ValueError): E._extract_part_points(self.obj('BlindPocket',blind))
    def test_export_rejects_missing_row_without_overwriting(self):
        self.obj('Box',Part.makeBox(30,10,2))
        class Item:
            def __init__(self,text): self.value=text
            def text(self): return self.value
            def data(self,role): return self.value if role==QtCore.Qt.UserRole else None
        class Table:
            def rowCount(self): return 2
            def item(self,row,col): return Item(['Box','Missing'][row] if col==0 else '1')
            def cellWidget(self,*args): return None
        panel=NS(preview_doc_name=self.doc.Name,table=Table(),control_rows=0,spacing=0,sheet_margin=0,
                 get_dimension_value_mm=lambda value,default:value,get_boundary_resolution_mm=lambda:.01,
                 offcuts=[dict(type='rectangular',outer=[[0,0],[500,0],[500,500],[0,500]],quantity=1)])
        with tempfile.TemporaryDirectory() as tmp:
            original=E.__file__; E.__file__=str(Path(tmp)/'export.py')
            path=Path(tmp)/'input.json';path.write_text('previous input')
            try: self.assertFalse(E.execute_nesting(panel))
            finally: E.__file__=original
            self.assertEqual(path.read_text(),'previous input')
    def test_nested_cli_import_preserves_3d_and_absolute_2d(self):
        box=self.obj('Box',Part.makeBox(30,10,2))
        box.Placement=App.Placement(App.Vector(100,50,0),App.Rotation(App.Vector(0,0,1),90))
        outer=[[0,0],[100,0],[100,100],[0,100]]
        result=dict(placed=2,sheets=[dict(points=outer,parts=[
            dict(id=1,x=5,y=7,rotation=0,source=0,_ip_nesting=dict(source_part_index=0,preview_object_name='Box',source_type='3d')),
            dict(id=2,x=40,y=20,rotation=90,source=1,points=[[40,20],[40,30],[35,30]],holes=[],
                 _ip_nesting=dict(source_part_index=1,source_type='2d'))])])
        importer=R.NestingResultImporter(NS(preview_doc_name=self.doc.Name))
        self.assertTrue(importer.import_result(result,show_summary=False))
        self.assertEqual(len([o for o in importer.result_doc.Objects if o.TypeId == "Part::Feature"]),3)
        bb=importer.result_doc.getObject('Nesting_1').Shape.BoundBox
        for got,want in [(bb.XMin,5),(bb.YMin,7),(bb.XLength,10),(bb.YLength,30)]: self.assertAlmostEqual(got,want)
        bb2=importer.result_doc.getObject('Nesting_2').Shape.BoundBox
        self.assertAlmostEqual(bb2.XMin,35);self.assertAlmostEqual(bb2.YMin,20)
        previous=importer.result_doc.Name
        result['sheets'][0]['parts'][0]['_ip_nesting']['preview_object_name']='Missing'
        self.assertFalse(importer.import_result(result,show_summary=False))
        self.assertIn(previous,App.listDocuments())
    def test_multiple_sheets_and_result_replacement(self):
        existing=App.newDocument("Nesting_Result")
        result=dict(placed=0,sheets=[dict(points=[[0,0],[10,0],[10,10],[0,10]],parts=[]) for _ in range(2)])
        importer=R.NestingResultImporter(NS(preview_doc_name=self.doc.Name))
        self.assertTrue(importer.import_result(result,show_summary=False))
        previous=importer.result_doc.Name
        groups=[o for o in importer.result_doc.Objects if o.TypeId=="App::Part"]
        self.assertEqual(len(groups),2)
        self.assertGreater(groups[1].Placement.Base.x,groups[0].Placement.Base.x+10)
        self.assertTrue(importer.import_result(result,show_summary=False))
        self.assertNotIn(previous,App.listDocuments())
        self.assertIn(existing.Name,App.listDocuments())

    def test_continuous_snapshot_keeps_polling_until_exit(self):
        class AcceptImporter:
            def import_result(self,**kw): return True
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json';path.write_text(json.dumps(dict(continuous=True,sheets=[])))
            session=Path(tmp)/'session.json';session.write_text('{}')
            stopped=[];state=[None]
            mgr=R.NestingProcessManager(NS(run_btn=NS(setEnabled=lambda x:None),stop_btn=NS(setEnabled=lambda x:None)))
            mgr.process=NS(poll=lambda:state[0],returncode=0)
            mgr.result_timer=NS(stop=lambda:stopped.append(True))
            mgr.result_path=str(path);mgr.session_path=str(session);mgr.importer=AcceptImporter()
            for _ in range(3): mgr._check_result()
            self.assertFalse(mgr._finished);self.assertFalse(stopped)
            state[0]=0;mgr._check_result()
            self.assertTrue(mgr._finished);self.assertTrue(stopped)

if __name__=='__main__': unittest.main(verbosity=2)
