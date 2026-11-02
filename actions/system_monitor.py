"""
System Monitor — background metric checks with voice alert support.
Zero subprocess calls on all platforms — uses ctypes/pynvml/psutil/wmi only.
"""
import ctypes
import math
import platform
import threading
import time

import psutil

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

DEFAULT_THRESHOLDS = {
    "cpu":  90.0,
    "ram":  90.0,
    "temp": 85.0,
    "gpu":  95.0,
}

_COOLDOWN   = 300
_CPU_STREAK = 3

# ── NVML DLL cache (Windows: nvml.dll, Linux: libnvidia-ml.so.1) ─────────────
_nvml_lib: object = None
_nvml_ok:  object = None   # None=untested  True=works  False=unavailable
_nvml_device: object = None
_pynvml: object = None
_pynvml_device: object = None
_pynvml_ok: object = None
_nvml_retry_at = 0.0
_pynvml_retry_at = 0.0
_PROVIDER_RETRY_SECONDS = 30.0
_temperature_cache = threading.local()


def _nvml_gpu() -> float:
    """GPU utilisation via NVML — zero subprocess on all platforms."""
    global _nvml_lib, _nvml_ok, _nvml_device, _nvml_retry_at
    if _nvml_ok is False:
        if time.monotonic() < _nvml_retry_at:
            return -1.0
        _nvml_ok = None
        _nvml_device = None
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            if _OS == "Windows":
                candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
                _load = ctypes.WinDLL
            else:
                candidates = (
                    "libnvidia-ml.so.1",
                    "libnvidia-ml.so",
                    "libnvidia-ml.dylib",
                )
                _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    if lib.nvmlInit_v2() != 0:
                        continue
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            _nvml_retry_at = time.monotonic() + _PROVIDER_RETRY_SECONDS
            return -1.0

        if _nvml_device is None:
            dev = ctypes.c_void_p()
            if _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev)) != 0:
                _nvml_ok = False
                _nvml_retry_at = time.monotonic() + _PROVIDER_RETRY_SECONDS
                return -1.0
            _nvml_device = dev
        u = _Util()
        if _nvml_lib.nvmlDeviceGetUtilizationRates(_nvml_device, ctypes.byref(u)) != 0:
            _nvml_ok = False
            _nvml_retry_at = time.monotonic() + _PROVIDER_RETRY_SECONDS
            return -1.0
        _nvml_ok = True
        return float(u.gpu) if 0 <= u.gpu <= 100 else -1.0
    except Exception:
        _nvml_ok = False
        _nvml_retry_at = time.monotonic() + _PROVIDER_RETRY_SECONDS
        return -1.0


def _get_gpu_usage() -> float:
    # Initialise once; unavailable providers are not reprobed on every tick.
    global _pynvml, _pynvml_device, _pynvml_ok, _pynvml_retry_at
    if _pynvml_ok is False and time.monotonic() >= _pynvml_retry_at:
        _pynvml_ok = None
        _pynvml_device = None
    if _pynvml_ok is not False:
        try:
            if _pynvml_device is None:
                if _pynvml is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    _pynvml = pynvml
                _pynvml_device = _pynvml.nvmlDeviceGetHandleByIndex(0)
            value = float(_pynvml.nvmlDeviceGetUtilizationRates(_pynvml_device).gpu)
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Invalid GPU reading")
            _pynvml_ok = True
            return value
        except Exception:
            _pynvml_ok = False
            _pynvml_retry_at = time.monotonic() + _PROVIDER_RETRY_SECONDS

    return _nvml_gpu()


def _get_cpu_temp() -> float:
    # psutil — works on Linux; occasionally Windows with proper drivers
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                     "cpu-thermal", "zenpower", "it8688"]:
            if name in temps and temps[name]:
                return temps[name][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass

    # Windows: wmi module (pure Python COM, zero subprocess)
    if _OS == "Windows" and getattr(_temperature_cache, "available", None) is not False:
        try:
            if not hasattr(_temperature_cache, "connection"):
                import pythoncom  # type: ignore
                import wmi  # type: ignore
                pythoncom.CoInitialize()
                _temperature_cache.connection = wmi.WMI(namespace="root/wmi")
            tz = _temperature_cache.connection.MSAcpi_ThermalZoneTemperature()
            if tz:
                return (tz[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            _temperature_cache.available = False

    return -1.0


class SystemMetrics:
    """Shared sampler with independent validity and expiry for each sensor.

    Values in the legacy top-level fields are always current or unavailable.
    ``sensors`` adds validity, last successful timestamp and a sample identifier
    so consumers cannot mistake an old reading for a fresh observation.
    """

    _TTLS = {"cpu": 6.0, "mem": 6.0, "net": 6.0,
             "gpu": 18.0, "tmp": 18.0, "process_count": 18.0}

    def __init__(self):
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._data = dict(cpu=-1.0, mem=-1.0, net=-1.0, gpu=-1.0, tmp=-1.0,
                          ram_used_gb=None, ram_total_gb=None, process_count=None)
        self._sensors = {
            key: {"status": "unavailable", "collected_at": None,
                  "monotonic_at": None, "sample_id": 0}
            for key in self._TTLS
        }
        self._cpu_primed = False
        self._slow_tick = 0
        self._last_net = None
        self._last_net_t = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CHLOE metrics")
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> bool:
        """Request shutdown and wait briefly, without blocking on a hung driver."""
        self._stop_event.set()
        if self._thread is not threading.current_thread() and self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))
        return not self._thread.is_alive()

    def _publish(self, key: str, value, *, valid: bool, **related):
        with self._lock:
            self._data[key] = value
            self._data.update(related)
            sensor = self._sensors[key]
            sensor["status"] = "valid" if valid else "unavailable"
            if valid:
                sensor["collected_at"] = time.time()
                sensor["monotonic_at"] = time.monotonic()
                sensor["sample_id"] += 1
            if all(self._sensors[name]["status"] == "valid" for name in ("cpu", "mem")):
                self._ready.set()
            else:
                self._ready.clear()

    @staticmethod
    def _number(value, *, maximum=None) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Invalid sensor reading")
        value = float(value)
        if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
            raise ValueError("Invalid sensor reading")
        return value

    def _sample(self, key: str, provider, *, maximum=None):
        try:
            value = self._number(provider(), maximum=maximum)
        except Exception:
            self._publish(key, -1.0, valid=False)
        else:
            self._publish(key, value, valid=True)

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._update()
            except Exception:
                # Each provider is isolated below. An unexpected sampler failure
                # invalidates the entire batch instead of retaining old values.
                for key in self._TTLS:
                    self._publish(key, None if key == "process_count" else -1.0, valid=False,
                                  **({"ram_used_gb": None, "ram_total_gb": None} if key == "mem" else {}))
            self._stop_event.wait(2.0)

    def _sample_ram(self):
        try:
            ram = psutil.virtual_memory()
            percent = self._number(ram.percent, maximum=100)
            used = self._number(ram.used)
            total = self._number(ram.total)
            if total <= 0 or used > total:
                raise ValueError("Invalid RAM reading")
        except Exception:
            self._publish("mem", -1.0, valid=False, ram_used_gb=None, ram_total_gb=None)
        else:
            self._publish("mem", percent, valid=True,
                          ram_used_gb=round(used / 1024 ** 3, 1),
                          ram_total_gb=round(total / 1024 ** 3, 1))

    def _sample_network(self):
        try:
            counters = psutil.net_io_counters()
            count = self._number(counters.bytes_sent) + self._number(counters.bytes_recv)
            now = time.monotonic()
        except Exception:
            self._last_net, self._last_net_t = None, None
            self._publish("net", -1.0, valid=False)
            return
        previous, previous_at = self._last_net, self._last_net_t
        self._last_net, self._last_net_t = count, now
        if previous is None or previous_at is None or now <= previous_at or count < previous:
            self._publish("net", -1.0, valid=False)
            return
        rate = self._number((count - previous) / (now - previous_at) / (1024 * 1024))
        self._publish("net", rate, valid=True)

    def _sample_processes(self):
        try:
            count = len(psutil.pids())
        except Exception:
            self._publish("process_count", None, valid=False)
        else:
            self._publish("process_count", count, valid=True)

    def _update(self):
        def cpu():
            value = psutil.cpu_percent(interval=None if self._cpu_primed else 0.1)
            value = self._number(value, maximum=100)
            self._cpu_primed = True
            return value

        self._sample("cpu", cpu, maximum=100)
        self._sample_ram()
        self._sample_network()
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            self._sample("gpu", _get_gpu_usage, maximum=100)
            self._sample("tmp", _get_cpu_temp, maximum=200)
            self._sample_processes()

    def snapshot(self) -> dict:
        now = time.monotonic()
        with self._lock:
            result = dict(self._data)
            sensors = {}
            for key, source in self._sensors.items():
                sensor = dict(source)
                sampled_at = sensor.pop("monotonic_at")
                age = None if sampled_at is None else max(0.0, now - sampled_at)
                sensor["age_seconds"] = age
                if sensor["status"] == "valid" and (age is None or age > self._TTLS[key]):
                    sensor["status"] = "stale"
                if sensor["status"] != "valid":
                    result[key] = None if key == "process_count" else -1.0
                    if key == "mem":
                        result.update(ram_used_gb=None, ram_total_gb=None)
                sensors[key] = sensor
            result["sensors"] = sensors
            result["ready"] = all(sensors[key]["status"] == "valid" for key in ("cpu", "mem"))
            return result



_metrics: SystemMetrics | None = None
_metrics_lock = threading.Lock()


def get_metrics() -> SystemMetrics:
    global _metrics
    with _metrics_lock:
        if _metrics is None:
            _metrics = SystemMetrics()
        return _metrics


def current_reading(snapshot: dict, key: str, *, maximum=None):
    """Defend consumers too, including snapshots supplied by other adapters."""
    sensors = snapshot.get("sensors")
    if isinstance(sensors, dict):
        sensor = sensors.get(key, {})
        if not isinstance(sensor, dict) or sensor.get("status") != "valid":
            return None
        age = sensor.get("age_seconds")
        if (not isinstance(age, (int, float)) or not math.isfinite(age)
                or age < 0 or age > SystemMetrics._TTLS[key]):
            return None
    value = snapshot.get(key)
    try:
        SystemMetrics._number(value, maximum=maximum)
    except (TypeError, ValueError):
        return None
    return value


def get_system_status() -> dict:
    """Read the shared real measurements, without another polling loop."""
    snap = get_metrics().snapshot()
    try:
        uptime_secs = time.time() - psutil.boot_time()
        uptime_h = int(uptime_secs // 3600)
        uptime_m = int((uptime_secs % 3600) // 60)
        uptime = f"{uptime_h}h {uptime_m}m"
    except Exception:
        uptime = "Unavailable"
    cpu = current_reading(snap, "cpu", maximum=100)
    ram = current_reading(snap, "mem", maximum=100)
    temp = current_reading(snap, "tmp", maximum=200)
    gpu = current_reading(snap, "gpu", maximum=100)

    return {
        "cpu_percent":   round(cpu, 1) if cpu is not None else None,
        "ram_percent":   round(ram, 1) if ram is not None else None,
        "ram_used_gb":   snap.get("ram_used_gb") if ram is not None else None,
        "ram_total_gb":  snap.get("ram_total_gb") if ram is not None else None,
        "cpu_temp_c":    round(temp, 1) if temp is not None else None,
        "gpu_percent":   round(gpu, 1) if gpu is not None else None,
        "uptime":        uptime,
        "process_count": current_reading(snap, "process_count"),
    }


class SystemMonitor:
    """
    Stateful monitor — cooldown state persists across session reconnections.
    Call check() periodically; returns a [SYSTEM_ALERT] string or None.
    """

    def __init__(self, thresholds: dict | None = None):
        self.thresholds   = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: dict[str, float] = {}
        self._cpu_streak  = 0
        self._last_cpu_sample = None

    def _can_alert(self, key: str) -> bool:
        previous = self._last_alert.get(key)
        return previous is None or time.monotonic() - previous > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> str | None:
        try:
            snap = get_metrics().snapshot()
            cpu = current_reading(snap, "cpu", maximum=100)
            ram = current_reading(snap, "mem", maximum=100)
            temp = current_reading(snap, "tmp", maximum=200)
            gpu = current_reading(snap, "gpu", maximum=100)
        except Exception:
            return None

        alerts: list[str] = []

        sensor_metadata = snap.get("sensors")
        cpu_sensor = sensor_metadata.get("cpu", {}) if isinstance(sensor_metadata, dict) else {}
        cpu_sensor = cpu_sensor if isinstance(cpu_sensor, dict) else {}
        sample = (cpu_sensor.get("sample_id"), cpu_sensor.get("collected_at"))
        distinct_cpu = not cpu_sensor or sample != self._last_cpu_sample
        if cpu is None:
            self._cpu_streak = 0
            self._last_cpu_sample = None
        elif not distinct_cpu:
            pass
        elif cpu >= self.thresholds["cpu"]:
            self._last_cpu_sample = sample
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(
                    f"[SYSTEM_ALERT] CPU usage has been critically high ({cpu:.0f}%) "
                    "for several seconds. Warn the user in their language and suggest "
                    "closing heavy applications."
                )
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0
            self._last_cpu_sample = sample

        if ram is not None and ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(
                f"[SYSTEM_ALERT] RAM is at {ram:.0f}% — nearly exhausted. "
                "Warn the user in their language and suggest freeing memory."
            )
            self._record("ram")

        if temp is not None and temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(
                f"[SYSTEM_ALERT] CPU temperature is {temp:.0f}°C — above the safe limit. "
                "Warn the user in their language and advise reducing system load "
                "or checking cooling."
            )
            self._record("temp")

        if gpu is not None and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(
                f"[SYSTEM_ALERT] GPU load is at {gpu:.0f}%. "
                "Briefly inform the user in their language."
            )
            self._record("gpu")

        return " ".join(alerts) if alerts else None
