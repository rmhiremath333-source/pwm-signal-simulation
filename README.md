# PWM Signal Simulation — Two-Device Edition

A PWM (Pulse Width Modulation) simulator with a **Controller device** and a
**Monitor device**, kept in sync in real time over WebSockets — modeling how
a real microcontroller might broadcast its PWM state to a companion app on
a phone.

## What's new vs. a single-page simulator

- **Flask + Flask-SocketIO backend** acts as the "device" holding the true
  PWM state (duty cycle, frequency). It is the single source of truth —
  neither browser page trusts its own numbers, both just render what the
  server sends.
- **Two roles, phone drives the display**:
  - `/monitor` — **Phone / Remote**: has the sliders and presets. Moving
    these is what actually changes the simulation. Also shows the live
    waveform, component readings, and two live history graphs (duty cycle
    over time, power draw over time).
  - `/` — **Display**: the "effect" screen (e.g. your laptop). Has no
    controls at all - it's purely a live readout. Shows the same waveform,
    stats, history graphs, and change log, plus two visual simulations:
    a **tube light** (brightness tracks duty cycle, visibly flickers via
    native SVG `<animate>` when the switching frequency is low enough for
    the eye to notice, and stays steady at high frequency) and a **fan**
    (blades spin via native SVG `<animateTransform>`, with rotation speed
    tracking duty cycle - stopped at 0%).
- **True real-time graphs**: a background "clock tick" runs once per
  second for the lifetime of the server, recording the current duty
  cycle/power into an in-memory buffer and broadcasting a fresh update -
  so the duty-cycle-vs-time and power-vs-time graphs keep moving even if
  you never touch a slider, exactly like a real oscilloscope/data-logger
  trend view. Moving a slider also records an instant point so the graph
  reacts immediately, not just on the next tick.
- **QR pairing**: the display page shows a QR code (and the raw URL) that
  encodes `http://<your-LAN-ip>:5000/monitor`. Scan it with your phone
  (same WiFi) to open the remote control instantly — no typing IP addresses.
- **JSON protocol layer** over the WebSocket, not raw ad-hoc events:
  - `CONTROL` — client asks the server to change duty cycle/frequency
  - `STATE_UPDATE` — server broadcasts the new computed state (+ rendered
    waveform SVG + rendered history charts) to every connected device
  - `DEVICE_STATUS` — tells the display whether a phone is currently
    connected (and vice versa)
  - `PING` / `PONG` — keep-alive / can be used to show latency
- **SQLite data logging** (`pwm_log.db`): every change is timestamped and
  stored, with which device made the change. The display page shows the
  last 8 entries in a live table; the phone's graphs are built from this
  same log.
- **`pwm_analysis.py`**: a standalone module of PWM theory helpers
  (flicker-perception classification, RMS vs. average voltage) exposed via
  `/api/analysis`, useful for report commentary or a viva talking point.
- **`tests/test_app.py`**: 21 unit tests covering the PWM math, the
  server-rendered SVG/HTML, the routes, and the SQLite logging.
- **`tools/export_log.py`**: exports the logged history to CSV for a
  report chart, from the command line.

## Project structure

```
pwm_project/
├── app.py                     Flask + SocketIO backend, PWM math, DB, QR
├── requirements.txt
├── templates/
│   ├── controller.html
│   └── monitor.html
└── static/
    ├── css/style.css
    └── js/
        ├── waveform.js        shared SVG waveform + component-list renderer
        ├── controller.js
        └── monitor.js
```

## Running it

```bash
cd pwm_project
pip install -r requirements.txt
python app.py
```

You'll see something like:

```
Controller (this device): http://192.168.1.5:5000/
Monitor    (phone):       http://192.168.1.5:5000/monitor
```

1. Open the **Display** URL on your laptop (`/`).
2. Make sure your **phone is on the same WiFi network**.
3. Scan the QR code shown on the display page (or type the URL manually)
   to open the **Remote** (`/monitor`) on your phone.
4. Move the sliders / tap presets **on your phone** — the laptop's display
   updates live: waveform, stats, component readings, tube light, fan, and
   the change log. The phone also shows its own live duty-cycle and
   power-draw graphs.

## How to explain this in the viva (talking points)

- "The Flask server is the single source of truth, like the microcontroller
  in a real embedded system — it computes the PWM period, on-time, and
  power figures, and pushes them to every connected client."
- "I designed a small message protocol with typed JSON packets
  (`CONTROL`, `STATE_UPDATE`, `DEVICE_STATUS`) instead of ad-hoc events, so
  the client-server contract is explicit."
- "WebSockets (via Socket.IO) give push-based real-time updates instead of
  polling, which is what a real IoT dashboard would use."
- "Every duty-cycle/frequency change is timestamped and logged to SQLite,
  so the project also demonstrates basic data logging, which could be
  extended into analytics/CSV export."
- "The QR code closes the loop between the two devices without manual IP
  entry — a small UX detail that mirrors how real IoT setup flows work."

## Possible easy extensions if you want to push further

- Export the log table as CSV from `/api/history`
- Add a graph of power-saved-over-time using the logged history
- Add authentication so only your two devices can control state
- Deploy on Render/Railway so it works over the internet, not just LAN
