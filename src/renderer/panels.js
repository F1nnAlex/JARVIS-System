// Side panels: clock, weather (Open-Meteo, no key needed), system stats.

import { drawSparkline } from './hud.js';

const $ = (id) => document.getElementById(id);

const WEATHER_CODES = {
  0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
  45: 'Fog', 48: 'Freezing fog',
  51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle', 56: 'Freezing drizzle', 57: 'Freezing drizzle',
  61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 66: 'Freezing rain', 67: 'Freezing rain',
  71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains',
  80: 'Rain showers', 81: 'Rain showers', 82: 'Violent rain showers',
  85: 'Snow showers', 86: 'Heavy snow showers',
  95: 'Thunderstorm', 96: 'Thunderstorm with hail', 99: 'Thunderstorm with hail',
};

function formatBytes(bytes) {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function formatUptime(seconds) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}d ${h}h ${m}m` : `${h}h ${m}m`;
}

export function startClock() {
  const tick = () => {
    const now = new Date();
    $('clock').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    $('clock-seconds').textContent = String(now.getSeconds()).padStart(2, '0');
    $('date').textContent = now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' });
  };
  tick();
  setInterval(tick, 1000);
}

export function startSystemStats() {
  const history = [];
  const update = async () => {
    try {
      const s = await window.jarvis.systemStats();
      const cpu = Math.round(s.cpu * 100);
      $('cpu-value').textContent = `${cpu}%`;
      $('cpu-bar').style.width = `${cpu}%`;
      $('cpu-bar').classList.toggle('warn', cpu > 85);
      const used = s.memTotal - s.memFree;
      const memPct = Math.round((used / s.memTotal) * 100);
      $('mem-value').textContent = `${formatBytes(used)} / ${formatBytes(s.memTotal)}`;
      $('mem-bar').style.width = `${memPct}%`;
      $('mem-bar').classList.toggle('warn', memPct > 90);
      $('sys-host').textContent = s.hostname;
      $('sys-cores').textContent = `${s.cores} × ${s.arch}`;
      $('sys-uptime').textContent = formatUptime(s.uptime);
      $('sys-platform').textContent = s.platform;
      $('sys-platform').title = s.cpuModel;
      history.push(s.cpu);
      if (history.length > 60) history.shift();
      drawSparkline($('cpu-spark'), history);
    } catch (err) {
      console.warn('system stats unavailable', err);
    }
  };
  update();
  setInterval(update, 1500);
}

export async function startBattery() {
  if (!navigator.getBattery) return;
  try {
    const battery = await navigator.getBattery();
    const render = () => {
      // Desktops report a permanently "full, charging" battery: hide it.
      const looksLikeDesktop = battery.charging && battery.level === 1 && battery.chargingTime === 0;
      $('battery-meter').hidden = looksLikeDesktop;
      const pct = Math.round(battery.level * 100);
      $('battery-value').textContent = `${pct}%${battery.charging ? ' ⚡' : ''}`;
      $('battery-bar').style.width = `${pct}%`;
      $('battery-bar').classList.toggle('warn', pct < 20 && !battery.charging);
    };
    render();
    battery.addEventListener('levelchange', render);
    battery.addEventListener('chargingchange', render);
  } catch {
    // Battery info isn't available on this machine.
  }
}

export class Weather {
  constructor() {
    this.location = '';
    this.place = null;
    this.summary = '';
    this.timer = null;
  }

  async setLocation(location) {
    if (location === this.location && this.place) return;
    this.location = location;
    this.place = null;
    this.summary = '';
    clearInterval(this.timer);
    if (!location) {
      this.#render(null);
      return;
    }
    await this.refresh();
    this.timer = setInterval(() => this.refresh(), 15 * 60 * 1000);
  }

  async refresh() {
    try {
      if (!this.place) {
        const url = `https://geocoding-api.open-meteo.com/v1/search?count=1&name=${encodeURIComponent(this.location)}`;
        const geo = await (await fetch(url)).json();
        if (!geo.results || !geo.results.length) {
          $('weather-desc').textContent = `Couldn't find "${this.location}"`;
          return;
        }
        this.place = geo.results[0];
      }
      const { latitude, longitude } = this.place;
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}`
        + '&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m'
        + '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=1';
      const data = await (await fetch(url)).json();
      this.#render(data);
    } catch (err) {
      console.warn('weather unavailable', err);
      $('weather-desc').textContent = 'Weather feed offline';
    }
  }

  #render(data) {
    if (!data) {
      $('weather-temp').textContent = '--°';
      $('weather-desc').textContent = 'Set your location in settings';
      $('weather-place').textContent = '';
      ['weather-wind', 'weather-humidity', 'weather-hilo'].forEach((id) => { $(id).textContent = '--'; });
      return;
    }
    const c = data.current;
    const d = data.daily;
    const desc = WEATHER_CODES[c.weather_code] || 'Unknown conditions';
    const hi = Math.round(d.temperature_2m_max[0]);
    const lo = Math.round(d.temperature_2m_min[0]);
    const rain = d.precipitation_probability_max ? d.precipitation_probability_max[0] : null;
    $('weather-temp').textContent = `${Math.round(c.temperature_2m)}°`;
    $('weather-desc').textContent = desc;
    $('weather-place').textContent = [this.place.name, this.place.country_code].filter(Boolean).join(', ');
    $('weather-wind').textContent = `${Math.round(c.wind_speed_10m)} km/h`;
    $('weather-humidity').textContent = `${c.relative_humidity_2m}%`;
    $('weather-hilo').textContent = `${hi}° / ${lo}°`;
    this.summary = `${desc}, ${Math.round(c.temperature_2m)}°C (feels like ${Math.round(c.apparent_temperature)}°C), `
      + `wind ${Math.round(c.wind_speed_10m)} km/h, humidity ${c.relative_humidity_2m}%, `
      + `today's high ${hi}°C and low ${lo}°C${rain !== null ? `, ${rain}% chance of rain` : ''}`;
  }
}
