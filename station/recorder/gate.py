from contextlib import contextmanager
from threading import Lock
from weakref import WeakValueDictionary


class WorkfileGate:
    _registry = WeakValueDictionary()
    _registry_lock = Lock()

    def __init__(self):
        self._lock = Lock()

    @classmethod
    def for_station(cls, root):
        with cls._registry_lock:
            gate = cls._registry.get(root)
            if gate is None:
                gate = cls()
                cls._registry[root] = gate
            return gate

    @contextmanager
    def hold(self):
        with self._lock:
            yield
