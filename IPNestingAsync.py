"""Background process operations; all FreeCAD and Qt work stays on the UI thread."""
import queue
import threading


class BackgroundCall:
    def __init__(self, action, discard=None):
        self.action = action
        self.discard = discard
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self._results = queue.Queue()
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        value, error = None, None
        try:
            if not self.cancelled.is_set():
                value = self.action(self.cancelled)
        except Exception as exc:
            error = str(exc)
        with self._lock:
            abandoned = self.cancelled.is_set()
            if not abandoned:
                self._results.put((value, error))
                self.finished.set()
        try:
            if abandoned and value is not None and self.discard:
                self.discard(value)
        finally:
            if abandoned:
                self.finished.set()

    def poll(self):
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def cancel(self):
        with self._lock:
            self.cancelled.set()
            pending = self.poll()
            needs_cleanup = pending and pending[0] is not None and self.discard
            if needs_cleanup:
                self.finished.clear()
        if needs_cleanup:
            def cleanup():
                try:
                    self.discard(pending[0])
                finally:
                    self.finished.set()
            threading.Thread(target=cleanup, daemon=True).start()


def discard_process(process):
    """Reap a process created after its owning panel/job was cancelled."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
            process.wait()
