"""Cancellation must keep a late process owned until it has been reaped."""
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from IPNestingAsync import BackgroundCall


class BackgroundCallTests(unittest.TestCase):
    def test_worker_reports_exception(self):
        def fail(cancelled):
            raise RuntimeError('launch failed')
        task = BackgroundCall(fail)
        task.start()
        self.assertTrue(task.finished.wait(2))
        self.assertEqual(task.poll(), (None, 'launch failed'))

    def test_cancel_before_start_does_not_run_action(self):
        calls = []
        task = BackgroundCall(lambda cancelled: calls.append(1))
        task.cancel()
        task.start()
        self.assertTrue(task.finished.wait(2))
        self.assertEqual(calls, [])
        self.assertIsNone(task.poll())

    def test_cancel_pending_action_discards_returned_value(self):
        started, allow = threading.Event(), threading.Event()
        values = []
        def action(cancelled):
            started.set()
            allow.wait(2)
            return 'late process'
        task = BackgroundCall(action, discard=values.append)
        task.start()
        self.assertTrue(started.wait(2))
        task.cancel()
        self.assertFalse(task.finished.is_set())
        allow.set()
        self.assertTrue(task.finished.wait(2))
        self.assertEqual(values, ['late process'])
        self.assertIsNone(task.poll())

    def test_cancel_queued_result_waits_for_cleanup(self):
        started, allow = threading.Event(), threading.Event()
        def discard(value):
            started.set()
            allow.wait(2)
        task = BackgroundCall(lambda cancelled: 'process', discard=discard)
        task.start()
        self.assertTrue(task.finished.wait(2))
        task.cancel()
        try:
            self.assertTrue(started.wait(2))
            self.assertFalse(task.finished.is_set())
            self.assertIsNone(task.poll())
        finally:
            allow.set()
        self.assertTrue(task.finished.wait(2))

    def test_gpu_captions_available_in_all_languages(self):
        for path in (ROOT / 'lng').glob('*.json'):
            if path.name in ('index.json', 'perimeters.json'): continue
            catalog = json.loads(path.read_text(encoding='utf-8'))
            with self.subTest(language=path.stem):
                for key in ('show_gpus', 'looking_for_graphic_cards'):
                    self.assertIsInstance(catalog[key], str)
                    self.assertTrue(catalog[key])


if __name__ == '__main__':
    unittest.main()
