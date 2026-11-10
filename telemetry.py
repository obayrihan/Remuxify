"""Read-only Windows physical-disk activity counters and portable I/O rates."""
import ctypes
from ctypes import wintypes
import math
import os


class _ValueUnion(ctypes.Union):
    _fields_ = [('long_value', wintypes.LONG), ('double_value', ctypes.c_double),
                ('large_value', ctypes.c_longlong), ('text_value', wintypes.LPWSTR)]


class _CounterValue(ctypes.Structure):
    _fields_ = [('status', wintypes.DWORD), ('value', _ValueUnion)]


class _CounterItem(ctypes.Structure):
    _fields_ = [('name', wintypes.LPWSTR), ('formatted', _CounterValue)]


def active_disks_from_idle(values):
    """Keep individual valid counters; never substitute an average for the busiest disk."""
    result = {}
    for name, status, idle in values:
        if name == '_Total' or status not in (0, 1) or not math.isfinite(idle):
            continue
        result[name] = round(max(0.0, min(100.0, 100.0 - idle)), 3)
    return result


class WindowsDiskActivity:
    """PDH % Idle Time counters, converted to physical-disk active time.

    Uses English counter names independently of the Windows display language.
    Reads existing counters only; never enables or changes system counters.
    """
    MORE_DATA = 0x800007D2

    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows disk counters are not available on this platform')
        self.api = ctypes.WinDLL('pdh')
        self.api.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self.api.PdhOpenQueryW.restype = wintypes.DWORD
        self.api.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
        self.api.PdhAddEnglishCounterW.restype = wintypes.DWORD
        self.api.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self.api.PdhCollectQueryData.restype = wintypes.DWORD
        self.api.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.api.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
        self.api.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self.api.PdhCloseQuery.restype = wintypes.DWORD
        self.query, self.counter = wintypes.HANDLE(), wintypes.HANDLE()
        try:
            self._check(self.api.PdhOpenQueryW(None, 0, ctypes.byref(self.query)), 'open disk query')
            self._check(self.api.PdhAddEnglishCounterW(self.query, r'\PhysicalDisk(*)\% Idle Time', 0, ctypes.byref(self.counter)), 'add disk counter')
            self._check(self.api.PdhCollectQueryData(self.query), 'prime disk counter')
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(status, operation):
        if status:
            raise OSError(f'Windows PDH could not {operation} (0x{status:08X})')

    def read(self):
        self._check(self.api.PdhCollectQueryData(self.query), 'sample disk counters')
        size, count = wintypes.DWORD(), wintypes.DWORD()
        status = self.api.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
        if status != self.MORE_DATA or not size.value:
            self._check(status, 'size disk counter array')
            raise OSError('Windows disk counter returned no data')
        buffer = ctypes.create_string_buffer(size.value)
        self._check(self.api.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count), buffer), 'read disk counter array')
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))[:count.value]
        values = active_disks_from_idle((item.name, item.formatted.status, item.formatted.value.double_value) for item in items)
        if not values:
            raise OSError('Windows physical-disk counters have no valid sampled instances')
        return values

    def close(self):
        if self.query:
            self.api.PdhCloseQuery(self.query)
            self.query = wintypes.HANDLE()


def disk_io_sample(previous, current, elapsed):
    """Return throughput and, where available, psutil's native busy-time estimate.

    Windows read_time/write_time are not used as an activity percentage: their
    granularity/units are unreliable here and overlapping requests aren't idle time.
    """
    if elapsed <= 0:
        return 0.0, None
    mbps, busiest = 0.0, None
    for name, item in current.items():
        old = previous.get(name)
        if old is None:
            continue
        mbps += max(0, item.read_bytes + item.write_bytes - old.read_bytes - old.write_bytes) / elapsed / 1e6
        if hasattr(item, 'busy_time') and hasattr(old, 'busy_time'):
            delta = item.busy_time - old.busy_time
            if delta >= 0:
                value = max(0, min(100, delta / elapsed / 10))
                busiest = max(busiest or 0, value)
    return mbps, busiest
