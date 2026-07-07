"""
PWM Signal Simulation - Two-Device Edition
--------------------------------------------
Flask + Flask-SocketIO backend acting as the "microcontroller" that
holds the true PWM state. Two front-end clients connect to it:

  1. CONTROLLER device (e.g. laptop)  -> /            (can change settings)
  2. MONITOR device    (e.g. phone)   -> /monitor      (read-only live view)

Communication uses a tiny JSON "protocol" over WebSockets so the
project can be explained as client-server / IoT style messaging
rather than "just a Flask app":

    { "type": "CONTROL",      "payload": {...} }   client -> server
    { "type": "STATE_UPDATE", "payload": {...} }   server -> all clients
    { "type": "PING"/"PONG" }                      keep-alive / latency
    { "type": "DEVICE_STATUS","payload": {...} }    connect/disconnect events

All duty-cycle / frequency changes are logged with a timestamp to a
local SQLite database (pwm_log.db) so the report can include a data
logging / analytics section.
"""

import io
import json
import socket
import sqlite3
import time
from collections import deque
from datetime import datetime

import qrcode
from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO, emit

from pwm_analysis import describe_signal, classify_flicker

app = Flask(__name__)
app.config["SECRET_KEY"] = "pwm-pbl-secret"
socketio = SocketIO(app, cors_allowed_origins="*")

DB_PATH = "pwm_log.db"

# ----------------------------------------------------------------------
# Component full-load power ratings (mW) - used for power calculations
# ----------------------------------------------------------------------
COMPONENTS = {
    "led": {"name": "LED Bulb", "full_load_mw": 33.0, "icon": "led"},
    "fan": {"name": "DC Fan Motor", "full_load_mw": 33.0, "icon": "fan"},
}

# ----------------------------------------------------------------------
# Live PWM state (single source of truth, held on the "device")
# ----------------------------------------------------------------------
state = {
    "duty_cycle": 50,      # percent 0-100
    "frequency": 10000,    # Hz
    "voltage": 3.3,        # volts, fixed supply
    "connected_devices": {"controller": 0, "monitor": 0},
}

# ----------------------------------------------------------------------
# Real-time (time, duty_cycle, power) trend buffer for the live graphs.
# Unlike the SQLite change log (only written when a setting changes),
# this is appended to once per second by a background "clock tick" so
# the graph keeps moving even while duty cycle/frequency stay constant -
# a genuine time-axis chart, not just a plot of discrete edit events.
# ----------------------------------------------------------------------
_server_start = time.time()
time_series = deque(maxlen=120)  # ~2 minutes of history at 1 tick/sec


def record_tick():
    """Appends the current state to the real-time trend buffer."""
    full_load_total = sum(c["full_load_mw"] for c in COMPONENTS.values())
    power = full_load_total * (state["duty_cycle"] / 100.0)
    time_series.append(
        {
            "t": round(time.time() - _server_start, 1),
            "duty": state["duty_cycle"],
            "power": round(power, 2),
        }
    )


def render_waveform_svg(duty_cycle, period_ms, height=340):
    """Builds the full 3-cycle PWM square-wave SVG markup server-side.

    Doing this in Python (rather than in the browser with JS) keeps all
    the actual signal-generation logic - the thing this project is really
    about - on the backend, where it belongs.
    """
    W, H = 800, height
    pad_l, pad_r, pad_t, pad_b = 60, 20, 30, 40
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b

    cycles = 3
    total_time = period_ms * cycles
    on_frac = duty_cycle / 100.0

    def x_for_t(t):
        return pad_l + (t / total_time) * plot_w

    y_high = pad_t
    y_low = pad_t + plot_h

    points = [f"M {x_for_t(0):.2f} {y_low:.2f}"]
    for c in range(cycles):
        t0 = c * period_ms
        t_on_end = t0 + period_ms * on_frac
        t1 = t0 + period_ms
        points.append(f"L {x_for_t(t0):.2f} {y_high:.2f}")
        points.append(f"L {x_for_t(t_on_end):.2f} {y_high:.2f}")
        points.append(f"L {x_for_t(t_on_end):.2f} {y_low:.2f}")
        points.append(f"L {x_for_t(t1):.2f} {y_low:.2f}")

    line_path = " ".join(points)
    fill_path = (
        line_path
        + f" L {x_for_t(total_time):.2f} {y_low + 40:.2f}"
        + f" L {x_for_t(0):.2f} {y_low + 40:.2f} Z"
    )

    svg_parts = [
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t - 10}" x2="{pad_l}" y2="{y_low:.2f}"/>',
        f'<line class="axis-line" x1="{pad_l}" y1="{y_low:.2f}" x2="{W - pad_r}" y2="{y_low:.2f}"/>',
        f'<text class="axis-text" x="{pad_l - 45}" y="{y_high + 5:.2f}">3.3V</text>',
        f'<text class="axis-text" x="{pad_l - 30}" y="{y_low + 5:.2f}">0V</text>',
    ]

    for i in range(cycles + 1):
        t = i * period_ms
        x = x_for_t(t)
        svg_parts.append(f'<line class="axis-line" x1="{x:.2f}" y1="{y_low:.2f}" x2="{x:.2f}" y2="{y_low + 6:.2f}"/>')
        svg_parts.append(f'<text class="axis-text" x="{x - 10:.2f}" y="{y_low + 22:.2f}">{t:.1f}</text>')

    svg_parts.append(f'<text class="axis-text" x="{pad_l + plot_w / 2 - 30:.2f}" y="{H - 5}">Time (ms)</text>')
    svg_parts.append(f'<path class="wave-fill" d="{fill_path}"/>')
    svg_parts.append(f'<path class="wave-line" d="{line_path}"/>')

    inner = "".join(svg_parts)
    return f'<svg id="wave" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{inner}</svg>'


COMPONENT_ICONS = {"led": "\U0001F4A1", "fan": "\U0001F300"}


def render_components_html(components):
    """Builds the component power-reading list markup server-side."""
    rows = []
    for key, c in components.items():
        pct = round((c["consumed_mw"] / c["full_load_mw"]) * 100) if c["full_load_mw"] else 0
        icon = COMPONENT_ICONS.get(key, "\u2699\ufe0f")
        rows.append(
            f'''<div class="component-row">
              <div class="component-left">
                <div class="component-icon">{icon}</div>
                <div>
                  <div class="component-name">{c["name"]}</div>
                  <div class="component-sub">Full load: {c["full_load_mw"]:.1f} mW</div>
                </div>
              </div>
              <div class="component-right">
                <div class="component-power">{c["consumed_mw"]:.1f} mW</div>
                <div style="font-size:11px;color:var(--text-dim);">{pct}%</div>
                <div class="mini-bar"><div class="mini-bar-fill" style="width:{pct}%"></div></div>
              </div>
            </div>'''
        )
    return "".join(rows)


def render_log_rows_html(rows):
    """Builds the <tr> rows for the change-log table server-side."""
    out = []
    for r in rows[:8]:
        ts = r["timestamp"].split("T")[1] if "T" in r["timestamp"] else r["timestamp"]
        out.append(
            f'''<tr>
              <td>{ts}</td>
              <td>{r["duty_cycle"]}%</td>
              <td>{r["frequency"]} Hz</td>
              <td>{r["power_consumed_mw"]} mW</td>
              <td><span class="src-badge">{r["source_device"]}</span></td>
            </tr>'''
        )
    return "".join(out)


def get_recent_history(limit=30):
    """Chronological (oldest->newest) list of the last `limit` logged rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT * FROM (SELECT * FROM pwm_log ORDER BY id DESC LIMIT {int(limit)}) ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def render_time_series_chart(series, key, color, height=140, y_max=None, unit=""):
    """
    A true time-axis line chart: x is elapsed wall-clock seconds, y is the
    requested field (duty cycle % or power mW). Built from `time_series`,
    which is appended to once per second regardless of whether anything
    changed, so a flat duty cycle still draws a flat (but moving) line -
    exactly what a real oscilloscope/data-logger trend view looks like.
    """
    W, H = 760, height
    pad_l, pad_r, pad_t, pad_b = 45, 15, 16, 24

    if not series:
        return (
            f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">'
            f'<text class="axis-text" x="{pad_l}" y="{H / 2:.0f}">Waiting for data…</text></svg>'
        )

    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b

    times = [e["t"] for e in series]
    values = [e[key] for e in series]
    t_min, t_max = times[0], times[-1]
    if t_max == t_min:
        t_max = t_min + 1  # avoid divide-by-zero for a single sample

    vmax = y_max if y_max is not None else (max(values) if max(values) > 0 else 1)

    def x_for_t(t):
        return pad_l + (t - t_min) / (t_max - t_min) * plot_w

    def y_for_v(v):
        frac = v / vmax if vmax > 0 else 0
        return pad_t + plot_h - frac * plot_h

    line_points = " ".join(f"{x_for_t(t):.1f},{y_for_v(v):.1f}" for t, v in zip(times, values))
    fill_points = (
        f"{x_for_t(t_min):.1f},{pad_t + plot_h:.1f} "
        + line_points
        + f" {x_for_t(t_max):.1f},{pad_t + plot_h:.1f}"
    )
    last_x, last_y = x_for_t(times[-1]), y_for_v(values[-1])
    span_s = t_max - t_min

    parts = [
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h:.1f}"/>',
        f'<line class="axis-line" x1="{pad_l}" y1="{pad_t + plot_h:.1f}" x2="{W - pad_r}" y2="{pad_t + plot_h:.1f}"/>',
        f'<text class="axis-text" x="4" y="{pad_t + 4}">{vmax:.0f}{unit}</text>',
        f'<text class="axis-text" x="4" y="{pad_t + plot_h:.0f}">0{unit}</text>',
        f'<text class="axis-text" x="{pad_l}" y="{H - 4}">-{span_s:.0f}s</text>',
        f'<text class="axis-text" x="{W - pad_r - 24}" y="{H - 4}">now</text>',
        f'<polygon points="{fill_points}" fill="{color}22" stroke="none"/>',
        f'<polyline points="{line_points}" fill="none" stroke="{color}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>',
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="{color}"/>',
    ]
    inner = "".join(parts)
    return f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{inner}</svg>'


def build_full_payload(s, series=None):
    """compute_metrics() plus the real-time duty/power-vs-time graphs,
    all rendered server-side and ready to drop straight into the DOM."""
    metrics = compute_metrics(s)
    ts = series if series is not None else list(time_series)

    metrics["duty_history_svg"] = render_time_series_chart(
        ts, "duty", color="#4f7cff", y_max=100, unit="%"
    )
    metrics["power_history_svg"] = render_time_series_chart(
        ts, "power", color="#34d399", y_max=None, unit="mW"
    )
    return metrics


def render_tube_light_svg(duty_cycle, frequency, width=360, height=220):
    """
    A small tube-light (fluorescent tube) fixture whose brightness tracks
    duty cycle, and which visibly flickers using native SVG <animate> when
    the switching frequency is low enough for the human eye to perceive it
    (below ~80Hz) - otherwise it renders as a steady, evenly-lit tube,
    which is the whole point of PWM dimming at high frequency.
    """
    brightness = max(0.0, min(1.0, duty_cycle / 100.0))
    glow_opacity = round(0.15 + 0.65 * brightness, 3)
    tube_opacity = round(0.12 + 0.88 * brightness, 3)

    flickers = frequency < 80 and duty_cycle > 0
    # flicker period exaggerated for visibility (real flicker at these
    # frequencies is faster than a browser can usefully animate/eye can see
    # framed at 30-60fps, so we scale it into a perceptible 0.15s-1.2s range)
    flicker_dur = max(0.15, min(1.2, 40.0 / max(frequency, 1)))

    anim = ""
    if flickers:
        low = round(tube_opacity * 0.35, 3)
        anim = (
            f'<animate attributeName="fill-opacity" '
            f'values="{tube_opacity};{low};{tube_opacity}" '
            f'dur="{flicker_dur}s" repeatCount="indefinite"/>'
        )
        glow_anim = (
            f'<animate attributeName="fill-opacity" '
            f'values="{glow_opacity};{round(glow_opacity*0.3,3)};{glow_opacity}" '
            f'dur="{flicker_dur}s" repeatCount="indefinite"/>'
        )
    else:
        glow_anim = ""

    cx, cy = width / 2, height / 2 + 10
    tube_w, tube_h = 200, 34

    svg = f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
      <rect x="{cx - tube_w/2 - 30:.1f}" y="{cy - 12:.1f}" width="20" height="24" rx="4" fill="#3a4258"/>
      <rect x="{cx + tube_w/2 + 10:.1f}" y="{cy - 12:.1f}" width="20" height="24" rx="4" fill="#3a4258"/>
      <ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{tube_w/2 + 40:.1f}" ry="{tube_h/2 + 40:.1f}"
               fill="#fff6d8" fill-opacity="{glow_opacity}">{glow_anim}</ellipse>
      <rect x="{cx - tube_w/2:.1f}" y="{cy - tube_h/2:.1f}" width="{tube_w}" height="{tube_h}" rx="{tube_h/2:.1f}"
            fill="#fff9e6" fill-opacity="{tube_opacity}" stroke="#cbd0dd" stroke-width="1.5">{anim}</rect>
      <text class="axis-text" x="{cx - 40:.1f}" y="{height - 14}">{"Flickering" if flickers else "Steady"} · {round(brightness*100)}% brightness</text>
    </svg>'''
    return svg


def render_fan_svg(duty_cycle, frequency, width=260, height=260):
    """
    A simple 3-blade DC fan whose rotation speed tracks duty cycle, using
    a native SVG <animateTransform> rotation - no JS needed, the browser's
    SVG renderer drives the animation on its own once the markup is set.
    """
    speed_frac = max(0.0, min(1.0, duty_cycle / 100.0))
    cx, cy, r = width / 2, height / 2, 70

    if speed_frac <= 0:
        rotation = ""
        status = "Stopped"
    else:
        # full-speed spin takes ~0.5s/rotation, idle-speed spin ~4s/rotation
        spin_seconds = round(4.0 - 3.5 * speed_frac, 2)
        rotation = (
            f'<animateTransform attributeName="transform" type="rotate" '
            f'from="0 {cx} {cy}" to="360 {cx} {cy}" dur="{spin_seconds}s" repeatCount="indefinite"/>'
        )
        status = f"Spinning · {round(speed_frac*100)}% speed"

    blades = []
    for i in range(3):
        angle = i * 120
        blades.append(
            f'<ellipse cx="{cx:.1f}" cy="{cy - r*0.55:.1f}" rx="18" ry="{r*0.6:.1f}" '
            f'fill="#4f7cff" fill-opacity="0.85" transform="rotate({angle} {cx} {cy})"/>'
        )
    blades_svg = "".join(blades)

    svg = f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
      <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r + 20:.1f}" fill="#161d2e" stroke="#2a3450" stroke-width="2"/>
      <g>{rotation}{blades_svg}</g>
      <circle cx="{cx:.1f}" cy="{cy:.1f}" r="14" fill="#e7ebf5"/>
      <text class="axis-text" x="{cx - 45:.1f}" y="{height - 6}">{status}</text>
    </svg>'''
    return svg


def compute_metrics(s):
    """Derive period/on-time/power figures from duty cycle + frequency."""
    freq = max(s["frequency"], 1)
    period_ms = 1000.0 / freq
    on_time_ms = period_ms * (s["duty_cycle"] / 100.0)
    off_time_ms = period_ms - on_time_ms

    components = {}
    for key, comp in COMPONENTS.items():
        consumed = comp["full_load_mw"] * (s["duty_cycle"] / 100.0)
        components[key] = {
            "name": comp["name"],
            "full_load_mw": comp["full_load_mw"],
            "consumed_mw": round(consumed, 2),
            "duty_cycle": s["duty_cycle"],
        }

    total_full_load = sum(c["full_load_mw"] for c in COMPONENTS.values())
    total_consumed = sum(c["consumed_mw"] for c in components.values())
    total_saved = total_full_load - total_consumed

    period_ms_r = round(period_ms, 4)

    return {
        "duty_cycle": s["duty_cycle"],
        "frequency": s["frequency"],
        "voltage": s["voltage"],
        "period_ms": period_ms_r,
        "on_time_ms": round(on_time_ms, 4),
        "off_time_ms": round(off_time_ms, 4),
        "power_consumed_mw": round(total_consumed, 2),
        "power_saved_mw": round(total_saved, 2),
        "components": components,
        "connected_devices": s["connected_devices"],
        "server_time": datetime.now().strftime("%H:%M:%S"),
        # server-rendered markup - the frontend just injects these as-is
        "waveform_svg_controller": render_waveform_svg(s["duty_cycle"], period_ms_r, height=340),
        "waveform_svg_monitor": render_waveform_svg(s["duty_cycle"], period_ms_r, height=300),
        "components_html": render_components_html(components),
        "tube_light_svg": render_tube_light_svg(s["duty_cycle"], s["frequency"]),
        "fan_svg": render_fan_svg(s["duty_cycle"], s["frequency"]),
    }


# ----------------------------------------------------------------------
# SQLite logging
# ----------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pwm_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            duty_cycle INTEGER NOT NULL,
            frequency INTEGER NOT NULL,
            power_consumed_mw REAL NOT NULL,
            power_saved_mw REAL NOT NULL,
            source_device TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


def log_change(metrics, source_device):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO pwm_log (timestamp, duty_cycle, frequency, power_consumed_mw, power_saved_mw, source_device) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            datetime.now().isoformat(timespec="seconds"),
            metrics["duty_cycle"],
            metrics["frequency"],
            metrics["power_consumed_mw"],
            metrics["power_saved_mw"],
            source_device,
        ),
    )
    conn.commit()
    conn.close()


def get_local_ip():
    """Best-effort LAN IP so the phone can reach this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# ----------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------
@app.route("/")
def controller_page():
    return render_template("controller.html", role="controller")


@app.route("/monitor")
def monitor_page():
    return render_template("monitor.html", role="monitor")


@app.route("/qr")
def qr_code():
    """Generates a QR code pointing the phone straight at /monitor."""
    ip = get_local_ip()
    port = request.host.split(":")[1] if ":" in request.host else "5000"
    url = f"http://{ip}:{port}/monitor"

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/api/monitor-url")
def monitor_url():
    ip = get_local_ip()
    port = request.host.split(":")[1] if ":" in request.host else "5000"
    return jsonify({"url": f"http://{ip}:{port}/monitor"})


@app.route("/api/analysis")
def analysis():
    """Plain-English theory commentary on the current signal, generated
    by the pwm_analysis module. Useful for report screenshots / viva."""
    band = classify_flicker(state["frequency"])
    text = describe_signal(state["duty_cycle"], state["frequency"], state["voltage"])
    return jsonify({"flicker_band": band.label, "description": text})


@app.route("/api/history")
def history():
    """Last 50 logged changes - used for a history/analytics view."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM pwm_log ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    rows = [dict(r) for r in rows]
    return jsonify({"rows": rows, "html": render_log_rows_html(rows)})


# ----------------------------------------------------------------------
# WebSocket / protocol layer
# ----------------------------------------------------------------------
@socketio.on("connect")
def on_connect():
    role = request.args.get("role", "unknown")
    if role in state["connected_devices"]:
        state["connected_devices"][role] += 1

    emit(
        "message",
        {"type": "STATE_UPDATE", "payload": build_full_payload(state)},
    )
    emit(
        "message",
        {
            "type": "DEVICE_STATUS",
            "payload": {"role": role, "status": "connected", "devices": state["connected_devices"]},
        },
        broadcast=True,
    )


@socketio.on("disconnect")
def on_disconnect():
    role = request.args.get("role", "unknown")
    if role in state["connected_devices"] and state["connected_devices"][role] > 0:
        state["connected_devices"][role] -= 1

    emit(
        "message",
        {
            "type": "DEVICE_STATUS",
            "payload": {"role": role, "status": "disconnected", "devices": state["connected_devices"]},
        },
        broadcast=True,
    )


@socketio.on("message")
def on_message(data):
    """Single entry point for the JSON 'protocol' described at the top."""
    if isinstance(data, str):
        data = json.loads(data)

    msg_type = data.get("type")

    if msg_type == "CONTROL":
        payload = data.get("payload", {})
        if "duty_cycle" in payload:
            state["duty_cycle"] = max(0, min(100, int(payload["duty_cycle"])))
        if "frequency" in payload:
            state["frequency"] = max(1, min(20000, int(payload["frequency"])))

        record_tick()  # so the real-time graph reflects the change immediately
        metrics = build_full_payload(state)
        log_change(metrics, source_device=payload.get("source", "controller"))

        # broadcast new state to controller + monitor
        emit("message", {"type": "STATE_UPDATE", "payload": metrics}, broadcast=True)

    elif msg_type == "PING":
        emit("message", {"type": "PONG", "payload": {"t": time.time()}})


def background_ticker():
    """Runs for the lifetime of the server: once per second, records the
    current state into the real-time trend buffer and broadcasts a fresh
    STATE_UPDATE, so the duty-cycle/power-vs-time graphs keep advancing
    even when nobody is touching the sliders."""
    record_tick()  # seed one data point immediately at startup
    while True:
        socketio.sleep(1)
        record_tick()
        socketio.emit("message", {"type": "STATE_UPDATE", "payload": build_full_payload(state)})


if __name__ == "__main__":
    init_db()
    ip = get_local_ip()
    print("=" * 60)
    print(" PWM Signal Simulation - Two Device Edition")
    print("=" * 60)
    print(f" Controller (this device): http://{ip}:5000/")
    print(f" Monitor    (phone):       http://{ip}:5000/monitor")
    print(" Scan the QR code shown on the controller page with your phone")
    print(" (phone must be on the SAME WiFi network as this computer).")
    print("=" * 60)
    socketio.start_background_task(background_ticker)
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
