"""Side panels: clock, weather (Open-Meteo, no key needed), diagnostics."""

from __future__ import annotations

import json
import platform
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import psutil
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .hud import Meter, Panel, Sparkline

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
    82: "Violent rain showers", 85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def _label(text="", name="value", align=None) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    if align is not None:
        lab.setAlignment(align)
    return lab


def _kv_grid(rows: list[str]) -> tuple[QGridLayout, dict[str, QLabel]]:
    grid = QGridLayout()
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(4)
    values = {}
    for i, key in enumerate(rows):
        grid.addWidget(_label(key.upper(), "key"), i, 0)
        values[key] = _label("--", "value", Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(values[key], i, 1)
    return grid, values


class ClockPanel(Panel):
    def __init__(self):
        super().__init__("Local time")
        self.clock = _label("00:00", "clock")
        self.sub = _label("", "clockSub")
        self.layout_.addWidget(self.clock)
        self.layout_.addWidget(self.sub)
        timer = QTimer(self)
        timer.timeout.connect(self.tick)
        timer.start(1000)
        self.tick()

    def tick(self):
        now = datetime.now()
        self.clock.setText(now.strftime("%H:%M"))
        self.sub.setText(f"{now:%S}   {now:%A %d %b %Y}".upper())


class _WeatherFetch(QObject):
    done = Signal(object)


class WeatherPanel(Panel):
    def __init__(self):
        super().__init__("Environment")
        row = QHBoxLayout()
        self.temp = _label("--°", "bigValue")
        self.desc = _label("Set your location in settings", "muted")
        self.desc.setWordWrap(True)
        self.place = _label("", "key")
        col = QVBoxLayout()
        col.addWidget(self.desc)
        col.addWidget(self.place)
        row.addWidget(self.temp)
        row.addSpacing(10)
        row.addLayout(col, 1)
        self.layout_.addLayout(row)
        grid, self.values = _kv_grid(["Wind", "Humidity", "High / Low"])
        self.layout_.addLayout(grid)
        self.location = ""
        self.summary = ""
        self._place = None
        self._bridge = _WeatherFetch()
        self._bridge.done.connect(self._render)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(15 * 60 * 1000)

    def set_location(self, location: str) -> None:
        if location == self.location:
            return
        self.location, self._place, self.summary = location, None, ""
        self.refresh()

    def refresh(self) -> None:
        if not self.location:
            self._render(None)
            return
        threading.Thread(target=self._fetch, args=(self.location,), daemon=True).start()

    def _fetch(self, location: str) -> None:
        try:
            if self._place is None:
                q = urllib.parse.urlencode({"name": location, "count": 1})
                geo = json.load(urllib.request.urlopen(f"https://geocoding-api.open-meteo.com/v1/search?{q}", timeout=10))
                if not geo.get("results"):
                    self._bridge.done.emit({"error": f"Couldn't find “{location}”"})
                    return
                self._place = geo["results"][0]
            q = urllib.parse.urlencode({
                "latitude": self._place["latitude"], "longitude": self._place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 1,
            })
            data = json.load(urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{q}", timeout=10))
            self._bridge.done.emit(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[weather] {exc}")
            self._bridge.done.emit({"error": "Weather feed offline"})

    def _render(self, data) -> None:
        if not data or "error" in data:
            self.temp.setText("--°")
            self.desc.setText(data["error"] if data else "Set your location in settings")
            self.place.setText("")
            for v in self.values.values():
                v.setText("--")
            return
        c, d = data["current"], data["daily"]
        desc = WEATHER_CODES.get(c["weather_code"], "Unknown conditions")
        hi, lo = round(d["temperature_2m_max"][0]), round(d["temperature_2m_min"][0])
        rain = (d.get("precipitation_probability_max") or [None])[0]
        self.temp.setText(f"{round(c['temperature_2m'])}°")
        self.desc.setText(desc)
        self.place.setText(", ".join(x for x in (self._place.get("name"), self._place.get("country_code")) if x).upper())
        self.values["Wind"].setText(f"{round(c['wind_speed_10m'])} km/h")
        self.values["Humidity"].setText(f"{c['relative_humidity_2m']}%")
        self.values["High / Low"].setText(f"{hi}° / {lo}°")
        self.summary = (
            f"{desc}, {round(c['temperature_2m'])}°C (feels like {round(c['apparent_temperature'])}°C), "
            f"wind {round(c['wind_speed_10m'])} km/h, humidity {c['relative_humidity_2m']}%, "
            f"today's high {hi}°C and low {lo}°C" + (f", {rain}% chance of rain" if rain is not None else "")
        )


class SystemPanel(Panel):
    def __init__(self):
        super().__init__("System diagnostics")
        self.cpu_label, self.cpu_meter = self._meter_row("CPU")
        self.spark = Sparkline()
        self.layout_.addWidget(self.spark)
        self.mem_label, self.mem_meter = self._meter_row("Memory")
        self.bat_row = QWidget()
        bat_layout = QVBoxLayout(self.bat_row)
        bat_layout.setContentsMargins(0, 0, 0, 0)
        self.bat_label, self.bat_meter = self._meter_row("Power", bat_layout)
        self.layout_.addWidget(self.bat_row)
        grid, self.values = _kv_grid(["Host", "Cores", "Uptime", "Platform"])
        self.layout_.addLayout(grid)
        self.values["Host"].setText(socket.gethostname())
        self.values["Cores"].setText(f"{psutil.cpu_count()} × {platform.machine()}")
        self.values["Platform"].setText(f"{platform.system()} {platform.release()}")
        psutil.cpu_percent(None)
        timer = QTimer(self)
        timer.timeout.connect(self.tick)
        timer.start(1500)
        self.tick()

    def _meter_row(self, name, layout=None):
        layout = layout or self.layout_
        row = QHBoxLayout()
        row.addWidget(_label(name.upper(), "key"))
        value = _label("--", "value", Qt.AlignRight)
        row.addWidget(value)
        layout.addLayout(row)
        meter = Meter()
        layout.addWidget(meter)
        return value, meter

    def tick(self):
        cpu = psutil.cpu_percent(None)
        self.cpu_label.setText(f"{cpu:.0f}%")
        self.cpu_meter.set_value(cpu / 100, cpu > 85)
        self.spark.push(cpu / 100)
        mem = psutil.virtual_memory()
        self.mem_label.setText(f"{mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB")
        self.mem_meter.set_value(mem.percent / 100, mem.percent > 90)
        battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        self.bat_row.setVisible(battery is not None)
        if battery is not None:
            self.bat_label.setText(f"{battery.percent:.0f}%{' ⚡' if battery.power_plugged else ''}")
            self.bat_meter.set_value(battery.percent / 100, battery.percent < 20 and not battery.power_plugged)
        up = int(time.time() - psutil.boot_time())
        d, h, m = up // 86400, up % 86400 // 3600, up % 3600 // 60
        self.values["Uptime"].setText(f"{d}d {h}h {m}m" if d else f"{h}h {m}m")
