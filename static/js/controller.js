// Display-only client. This page no longer has any controls - the phone
// (/monitor) is what drives the simulation. This file just listens for
// STATE_UPDATE and injects whatever HTML/SVG the server already rendered.

const socket = io({ query: { role: "controller" } });

const connDot = document.getElementById("connDot");
const monitorStatus = document.getElementById("monitorStatus");

// Track the last duty/frequency we rendered the simulations for. The
// real-time ticker broadcasts a STATE_UPDATE once per second even when
// nothing changed - if we blindly re-set innerHTML every time, the fan's
// and tube light's SVG animations get torn down and restarted from 0
// every second, so they never complete a smooth rotation/flicker cycle.
// Only touch those two elements when the underlying values actually change.
let lastDuty = null;
let lastFrequency = null;

socket.on("connect", () => connDot.classList.add("online"));
socket.on("disconnect", () => connDot.classList.remove("online"));

socket.on("message", (msg) => {
  if (msg.type === "STATE_UPDATE") {
    applyState(msg.payload);
  } else if (msg.type === "DEVICE_STATUS") {
    const online = (msg.payload.devices.monitor || 0) > 0;
    monitorStatus.textContent = "PHONE: " + (online ? "ONLINE" : "OFFLINE");
    monitorStatus.style.color = online ? "#7de9c3" : "";
  }
});

function applyState(s) {
  document.getElementById("statDuty").textContent = s.duty_cycle + "%";
  document.getElementById("statFreq").textContent = s.frequency + " Hz";
  document.getElementById("statPeriod").textContent = s.period_ms + " ms";
  document.getElementById("statOn").textContent = s.on_time_ms + " ms";
  document.getElementById("statConsumed").textContent = s.power_consumed_mw + " mW";
  document.getElementById("statSaved").textContent = s.power_saved_mw + " mW";

  document.getElementById("waveFreq").textContent = s.frequency;
  document.getElementById("wavePeriod").textContent = s.period_ms;
  document.getElementById("waveOn").textContent = s.on_time_ms;
  document.getElementById("waveOff").textContent = s.off_time_ms;

  // server-rendered markup, injected as-is
  document.getElementById("waveHolder").innerHTML = s.waveform_svg_controller;
  document.getElementById("componentList").innerHTML = s.components_html;
  document.getElementById("dutyChartHolder").innerHTML = s.duty_history_svg;
  document.getElementById("powerChartHolder").innerHTML = s.power_history_svg;

  if (s.duty_cycle !== lastDuty || s.frequency !== lastFrequency) {
    document.getElementById("tubeLightHolder").innerHTML = s.tube_light_svg;
    document.getElementById("fanHolder").innerHTML = s.fan_svg;
    lastDuty = s.duty_cycle;
    lastFrequency = s.frequency;
  }

  refreshLog();
}

function refreshLog() {
  fetch("/api/history")
    .then((r) => r.json())
    .then((d) => {
      document.getElementById("logBody").innerHTML = d.html;
    });
}

fetch("/api/monitor-url")
  .then((r) => r.json())
  .then((d) => {
    document.getElementById("monitorUrl").textContent = d.url;
  });
