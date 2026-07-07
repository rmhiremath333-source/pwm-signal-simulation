"""
Unit tests for the PWM simulation backend.

Run with:
    cd pwm_project
    python -m pytest tests/ -v

These tests cover the core signal-processing math (period/on-time/power),
the server-rendered SVG waveform, the server-rendered component markup,
and the Flask HTTP routes - independent of any browser or phone.
"""

import os
import sys
import sqlite3
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as pwm_app  # noqa: E402
from pwm_analysis import rc_ripple_estimate, classify_flicker, effective_voltage, average_voltage  # noqa: E402


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client backed by a throwaway SQLite DB."""
    db_file = tmp_path / "test_pwm_log.db"
    monkeypatch.setattr(pwm_app, "DB_PATH", str(db_file))
    pwm_app.init_db()

    pwm_app.state["duty_cycle"] = 50
    pwm_app.state["frequency"] = 10000
    pwm_app.state["connected_devices"] = {"controller": 0, "monitor": 0}

    pwm_app.app.config["TESTING"] = True
    with pwm_app.app.test_client() as c:
        yield c


@pytest.fixture
def base_state():
    return {
        "duty_cycle": 50,
        "frequency": 10000,
        "voltage": 3.3,
        "connected_devices": {"controller": 0, "monitor": 0},
    }


# ----------------------------------------------------------------------
# compute_metrics: core PWM math
# ----------------------------------------------------------------------
class TestComputeMetrics:
    def test_period_matches_frequency(self, base_state):
        metrics = pwm_app.compute_metrics(base_state)
        # period (ms) = 1000 / frequency(Hz)
        assert metrics["period_ms"] == pytest.approx(0.1, abs=1e-6)

    def test_on_time_scales_with_duty_cycle(self, base_state):
        base_state["duty_cycle"] = 25
        metrics = pwm_app.compute_metrics(base_state)
        assert metrics["on_time_ms"] == pytest.approx(metrics["period_ms"] * 0.25, abs=1e-6)

    def test_on_plus_off_equals_period(self, base_state):
        for duty in (0, 10, 50, 90, 100):
            base_state["duty_cycle"] = duty
            metrics = pwm_app.compute_metrics(base_state)
            assert metrics["on_time_ms"] + metrics["off_time_ms"] == pytest.approx(
                metrics["period_ms"], abs=1e-6
            )

    def test_zero_duty_cycle_consumes_no_power(self, base_state):
        base_state["duty_cycle"] = 0
        metrics = pwm_app.compute_metrics(base_state)
        assert metrics["power_consumed_mw"] == 0

    def test_full_duty_cycle_consumes_full_power(self, base_state):
        base_state["duty_cycle"] = 100
        metrics = pwm_app.compute_metrics(base_state)
        full_load_total = sum(c["full_load_mw"] for c in pwm_app.COMPONENTS.values())
        assert metrics["power_consumed_mw"] == pytest.approx(full_load_total, abs=0.01)

    def test_power_consumed_plus_saved_equals_full_load(self, base_state):
        for duty in (0, 33, 50, 77, 100):
            base_state["duty_cycle"] = duty
            metrics = pwm_app.compute_metrics(base_state)
            full_load_total = sum(c["full_load_mw"] for c in pwm_app.COMPONENTS.values())
            assert metrics["power_consumed_mw"] + metrics["power_saved_mw"] == pytest.approx(
                full_load_total, abs=0.01
            )

    def test_higher_frequency_shortens_period(self, base_state):
        base_state["frequency"] = 1
        low_freq_period = pwm_app.compute_metrics(base_state)["period_ms"]
        base_state["frequency"] = 10000
        high_freq_period = pwm_app.compute_metrics(base_state)["period_ms"]
        assert high_freq_period < low_freq_period

    def test_output_includes_rendered_markup(self, base_state):
        metrics = pwm_app.compute_metrics(base_state)
        assert "<svg" in metrics["waveform_svg_controller"]
        assert "<svg" in metrics["waveform_svg_monitor"]
        assert "component-row" in metrics["components_html"]


# ----------------------------------------------------------------------
# render_waveform_svg: server-side SVG generation
# ----------------------------------------------------------------------
class TestRenderWaveformSvg:
    def test_returns_valid_svg_tag(self):
        svg = pwm_app.render_waveform_svg(50, 0.1)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")

    def test_exactly_two_paths_fill_and_line(self):
        svg = pwm_app.render_waveform_svg(70, 0.05, height=300)
        assert svg.count("<path") == 2
        assert 'class="wave-fill"' in svg
        assert 'class="wave-line"' in svg
        assert svg.count("<svg") == 1

    def test_duty_cycle_zero_and_hundred_do_not_crash(self):
        for duty in (0, 100):
            svg = pwm_app.render_waveform_svg(duty, 1.0)
            assert "<svg" in svg

    def test_height_is_applied_to_viewbox(self):
        svg = pwm_app.render_waveform_svg(50, 0.1, height=500)
        assert 'viewBox="0 0 800 500"' in svg


# ----------------------------------------------------------------------
# render_components_html
# ----------------------------------------------------------------------
class TestRenderComponentsHtml:
    def test_contains_every_component_name(self, base_state):
        metrics = pwm_app.compute_metrics(base_state)
        html = pwm_app.render_components_html(metrics["components"])
        for comp in pwm_app.COMPONENTS.values():
            assert comp["name"] in html

    def test_percentage_reflects_duty_cycle(self, base_state):
        base_state["duty_cycle"] = 40
        metrics = pwm_app.compute_metrics(base_state)
        html = pwm_app.render_components_html(metrics["components"])
        assert "40%" in html


# ----------------------------------------------------------------------
# Flask HTTP routes
# ----------------------------------------------------------------------
class TestRoutes:
    def test_controller_page_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"PWM Signal Simulation" in resp.data

    def test_monitor_page_loads(self, client):
        resp = client.get("/monitor")
        assert resp.status_code == 200

    def test_qr_endpoint_returns_png(self, client):
        resp = client.get("/qr")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"

    def test_monitor_url_endpoint_returns_json(self, client):
        resp = client.get("/api/monitor-url")
        assert resp.status_code == 200
        assert "url" in resp.get_json()

    def test_history_endpoint_returns_expected_shape(self, client):
        resp = client.get("/api/history")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "rows" in body and "html" in body
        assert isinstance(body["rows"], list)


# ----------------------------------------------------------------------
# SQLite logging
# ----------------------------------------------------------------------
class TestLogging:
    def test_log_change_writes_a_row(self, tmp_path, monkeypatch, base_state):
        db_file = tmp_path / "log_test.db"
        monkeypatch.setattr(pwm_app, "DB_PATH", str(db_file))
        pwm_app.init_db()

        metrics = pwm_app.compute_metrics(base_state)
        pwm_app.log_change(metrics, source_device="controller")

        conn = sqlite3.connect(str(db_file))
        rows = conn.execute("SELECT * FROM pwm_log").fetchall()
        conn.close()

        assert len(rows) == 1

    def test_multiple_changes_accumulate(self, tmp_path, monkeypatch, base_state):
        db_file = tmp_path / "log_test2.db"
        monkeypatch.setattr(pwm_app, "DB_PATH", str(db_file))
        pwm_app.init_db()

        for duty in (10, 20, 30):
            base_state["duty_cycle"] = duty
            metrics = pwm_app.compute_metrics(base_state)
            pwm_app.log_change(metrics, source_device="controller")

        conn = sqlite3.connect(str(db_file))
        rows = conn.execute("SELECT duty_cycle FROM pwm_log ORDER BY id").fetchall()
        conn.close()

        assert [r[0] for r in rows] == [10, 20, 30]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ----------------------------------------------------------------------
# pwm_analysis.py - standalone theory helpers
# ----------------------------------------------------------------------
class TestPwmAnalysis:
    def test_classify_flicker_low_frequency_is_visible(self):
        band = classify_flicker(2)
        assert band.label == "Visible strobing"

    def test_classify_flicker_high_frequency_is_flicker_free(self):
        band = classify_flicker(10000)
        assert "switching" in band.label.lower() or "flicker-free" in band.label.lower()

    def test_average_voltage_scales_linearly(self):
        assert average_voltage(0, 3.3) == 0
        assert average_voltage(100, 3.3) == pytest.approx(3.3)
        assert average_voltage(50, 3.3) == pytest.approx(1.65)

    def test_effective_voltage_is_sqrt_scaled(self):
        # at 50% duty, RMS voltage should be higher than the simple average
        avg = average_voltage(50, 3.3)
        rms = effective_voltage(50, 3.3)
        assert rms > avg

    def test_effective_voltage_matches_supply_at_full_duty(self):
        assert effective_voltage(100, 3.3) == pytest.approx(3.3)

    def test_rc_ripple_estimate_returns_expected_keys(self):
        result = rc_ripple_estimate(10000, resistance_ohm=1000, capacitance_f=1e-6)
        assert set(result.keys()) == {"cutoff_hz", "switching_to_cutoff_ratio", "verdict"}

    def test_rc_ripple_high_switching_freq_gives_good_verdict(self):
        # 10kHz switching vs a filter cutoff around 160Hz -> ratio ~ 60, should be "Good"
        result = rc_ripple_estimate(10000, resistance_ohm=1000, capacitance_f=1e-6)
        assert "Good" in result["verdict"]

    def test_rc_ripple_low_switching_freq_gives_poor_verdict(self):
        # 50Hz switching vs the same filter -> ratio well under 2, should be "Poor"
        result = rc_ripple_estimate(50, resistance_ohm=1000, capacitance_f=1e-6)
        assert "Poor" in result["verdict"]

    def test_rc_ripple_rejects_non_positive_values(self):
        with pytest.raises(ValueError):
            rc_ripple_estimate(1000, resistance_ohm=0, capacitance_f=1e-6)
        with pytest.raises(ValueError):
            rc_ripple_estimate(1000, resistance_ohm=1000, capacitance_f=-1)


# ----------------------------------------------------------------------
# build_full_payload / real-time trend charts
# ----------------------------------------------------------------------
class TestRealTimeCharts:
    def test_build_full_payload_includes_chart_svgs(self, base_state):
        series = [{"t": 0.0, "duty": 50, "power": 33.0}, {"t": 1.0, "duty": 50, "power": 33.0}]
        payload = pwm_app.build_full_payload(base_state, series=series)
        assert "<svg" in payload["duty_history_svg"]
        assert "<svg" in payload["power_history_svg"]

    def test_render_time_series_chart_handles_empty_series(self):
        svg = pwm_app.render_time_series_chart([], "duty", color="#4f7cff")
        assert "<svg" in svg
        assert "Waiting for data" in svg

    def test_render_time_series_chart_handles_single_point(self):
        svg = pwm_app.render_time_series_chart(
            [{"t": 0.0, "duty": 50, "power": 33.0}], "duty", color="#4f7cff", y_max=100, unit="%"
        )
        assert "<svg" in svg

    def test_chart_reflects_time_series_values(self):
        series = [
            {"t": 0.0, "duty": 10, "power": 6.6},
            {"t": 1.0, "duty": 40, "power": 26.4},
            {"t": 2.0, "duty": 90, "power": 59.4},
        ]
        svg = pwm_app.render_time_series_chart(series, "duty", color="#4f7cff", y_max=100, unit="%")
        # last logged value should show up as the plotted end point / labelled max
        assert "100%" in svg  # y-axis max label

    def test_record_tick_appends_current_state(self, base_state, monkeypatch):
        monkeypatch.setattr(pwm_app, "time_series", pwm_app.deque(maxlen=120))
        pwm_app.state["duty_cycle"] = 65
        pwm_app.record_tick()
        assert len(pwm_app.time_series) == 1
        assert pwm_app.time_series[-1]["duty"] == 65

    def test_get_recent_history_still_works_for_change_log(self, tmp_path, monkeypatch, base_state):
        db_file = tmp_path / "chart_test2.db"
        monkeypatch.setattr(pwm_app, "DB_PATH", str(db_file))
        pwm_app.init_db()

        for duty in (10, 40, 90):
            base_state["duty_cycle"] = duty
            metrics = pwm_app.compute_metrics(base_state)
            pwm_app.log_change(metrics, source_device="phone")

        hist = pwm_app.get_recent_history(30)
        assert [r["duty_cycle"] for r in hist] == [10, 40, 90]
