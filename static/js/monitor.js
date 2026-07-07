// Phone / remote-control client. Unlike a plain read-only monitor, this
// page can ALSO send CONTROL messages - moving these sliders is what
// drives the display device in real time. All rendering (waveform SVG,
// history charts, component list) is still done server-side in Python;
// this file only wires up inputs and injects the markup it receives.

const socket = io({ query: { role: "monitor" } });

const dutySlider = document.getElementById("dutySlider");
const freqSlider = document.getElementById("freqSlider");
const dutyLabel = document.getElementById("dutyLabel");
const freqLabel = document.getElementById("freqLabel");
const connDot = document.getElementById("connDot");
const displayStatus = document.getElementById("displayStatus");

function sendControl() {
  socket.emit("message", {
    type: "CONTROL",
    payload: {
      duty_cycle: parseInt(dutySlider.value),
      frequency: parseInt(freqSlider.value),
      source: "phone",
    },
  });
}

function clearActive(sel) {
  document.querySelectorAll(sel).forEach((b) => b.classList.remove("active"));
}

dutySlider.addEventListener("input", () => {
  dutyLabel.textContent = dutySlider.value + "%";
  clearActive("[data-duty]");
  sendControl();
});
freqSlider.addEventListener("input", () => {
  freqLabel.textContent = freqSlider.value + " Hz";
  clearActive("[data-freq]");
  sendControl();
});

document.querySelectorAll("[data-duty]").forEach((btn) => {
  btn.addEventListener("click", () => {
    dutySlider.value = btn.dataset.duty;
    dutyLabel.textContent = btn.dataset.duty + "%";
    clearActive("[data-duty]");
    btn.classList.add("active");
    sendControl();
  });
});
document.querySelectorAll("[data-freq]").forEach((btn) => {
  btn.addEventListener("click", () => {
    freqSlider.value = btn.dataset.freq;
    freqLabel.textContent = btn.dataset.freq + " Hz";
    clearActive("[data-freq]");
    btn.classList.add("active");
    sendControl();
  });
});

socket.on("connect", () => connDot.classList.add("online"));
socket.on("disconnect", () => connDot.classList.remove("online"));

socket.on("message", (msg) => {
  if (msg.type === "STATE_UPDATE") applyState(msg.payload);
  else if (msg.type === "DEVICE_STATUS") {
    const online = (msg.payload.devices.controller || 0) > 0;
    displayStatus.textContent = "DISPLAY: " + (online ? "ONLINE" : "OFFLINE");
    displayStatus.style.color = online ? "#a6b8ff" : "";
  }
});

function applyState(s) {
  document.getElementById("bigDuty").textContent = s.duty_cycle + "%";
  document.getElementById("statFreq").textContent = s.frequency + " Hz";
  document.getElementById("statPeriod").textContent = s.period_ms + " ms";
  document.getElementById("statOn").textContent = s.on_time_ms + " ms";
  document.getElementById("statConsumed").textContent = s.power_consumed_mw + " mW";
  document.getElementById("statSaved").textContent = s.power_saved_mw + " mW";
  document.getElementById("statTime").textContent = s.server_time;
  document.getElementById("waveFreq").textContent = s.frequency;

  // server-rendered markup, injected as-is
  document.getElementById("waveHolder").innerHTML = s.waveform_svg_monitor;
  document.getElementById("componentList").innerHTML = s.components_html;
  document.getElementById("dutyChartHolder").innerHTML = s.duty_history_svg;
  document.getElementById("powerChartHolder").innerHTML = s.power_history_svg;

  // keep sliders in sync if the change came from the display device
  if (parseInt(dutySlider.value) !== s.duty_cycle) {
    dutySlider.value = s.duty_cycle;
    dutyLabel.textContent = s.duty_cycle + "%";
  }
  if (parseInt(freqSlider.value) !== s.frequency) {
    freqSlider.value = s.frequency;
    freqLabel.textContent = s.frequency + " Hz";
  }
}
