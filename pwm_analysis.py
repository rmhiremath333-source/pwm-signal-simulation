"""
pwm_analysis.py
----------------
Small standalone module of PWM theory helpers, kept separate from app.py
so it can also be imported/demonstrated on its own (e.g. in a Jupyter
notebook or a viva walkthrough) without needing Flask running.

Covers:
  - human flicker-perception classification for a given switching frequency
  - effective (RMS-equivalent) voltage delivered to a resistive load
  - a plain-English description of a given duty cycle / frequency pair,
    useful for auto-generating commentary in a report
"""

from dataclasses import dataclass


@dataclass
class FlickerBand:
    max_hz: float
    label: str
    description: str


# Rough, commonly-cited bands for human flicker perception vs PWM frequency.
FLICKER_BANDS = [
    FlickerBand(5, "Visible strobing", "Distinct on/off blinking, visible to the naked eye."),
    FlickerBand(60, "Noticeable flicker", "Flicker is visible, especially in peripheral vision."),
    FlickerBand(200, "Slight flicker", "Flicker mostly imperceptible when looking directly at it."),
    FlickerBand(2000, "Flicker-free (typical)", "Generally perceived as a steady, dimmed output."),
    float("inf"), 
]


def classify_flicker(frequency_hz: float) -> FlickerBand:
    """Returns the FlickerBand a given switching frequency falls into."""
    for band in FLICKER_BANDS:
        if isinstance(band, FlickerBand) and frequency_hz <= band.max_hz:
            return band
    return FlickerBand(float("inf"), "Very fast switching", "Well above the range where flicker is perceivable.")


def effective_voltage(duty_cycle_pct: float, supply_voltage: float) -> float:
    """
    RMS-equivalent voltage delivered to a resistive load by a PWM signal.

    For a resistive load, effective (RMS) voltage scales with the square
    root of the duty cycle fraction:
        V_rms = V_supply * sqrt(duty_cycle_fraction)

    This is different from the simple *average* voltage (which scales
    linearly with duty cycle) and is worth mentioning in a report to show
    understanding of why PWM dimming isn't perfectly linear in perceived
    brightness for some loads.
    """
    fraction = max(0.0, min(1.0, duty_cycle_pct / 100.0))
    return supply_voltage * (fraction ** 0.5)


def average_voltage(duty_cycle_pct: float, supply_voltage: float) -> float:
    """Simple time-average voltage: V_avg = V_supply * duty_cycle_fraction."""
    fraction = max(0.0, min(1.0, duty_cycle_pct / 100.0))
    return supply_voltage * fraction


def describe_signal(duty_cycle_pct: float, frequency_hz: float, supply_voltage: float = 3.3) -> str:
    """Builds a one-paragraph plain-English description of a PWM signal,
    useful for auto-filling commentary in a lab report."""
    band = classify_flicker(frequency_hz)
    v_avg = average_voltage(duty_cycle_pct, supply_voltage)
    v_rms = effective_voltage(duty_cycle_pct, supply_voltage)
    period_ms = 1000.0 / max(frequency_hz, 1e-9)

    return (
        f"At {frequency_hz:.0f} Hz ({period_ms:.4f} ms period) with a "
        f"{duty_cycle_pct:.0f}% duty cycle, the signal switches between 0V and "
        f"{supply_voltage:.1f}V. This falls in the '{band.label}' band "
        f"({band.description}) The time-averaged voltage delivered to the "
        f"load is about {v_avg:.2f}V, while the RMS-equivalent voltage seen "
        f"by a resistive load is about {v_rms:.2f}V."
    )


def rc_ripple_estimate(frequency_hz: float, resistance_ohm: float, capacitance_f: float) -> dict:
    """
    Rough estimate of how much residual ripple a simple RC low-pass filter
    would leave on a PWM signal at the given switching frequency - useful
    if the report discusses smoothing the PWM output into a cleaner analog
    voltage (e.g. for driving something more sensitive than an LED/motor).

    Not a full transient-response simulation - just the standard first-order
    low-pass corner-frequency comparison used to sanity-check a filter choice:

        f_cutoff = 1 / (2 * pi * R * C)

    If f_cutoff << switching frequency, the filter should smooth the PWM
    signal well; if f_cutoff is close to or above the switching frequency,
    ripple will still be significant.
    """
    import math

    if resistance_ohm <= 0 or capacitance_f <= 0:
        raise ValueError("resistance_ohm and capacitance_f must be positive")

    f_cutoff = 1.0 / (2 * math.pi * resistance_ohm * capacitance_f)
    ratio = frequency_hz / f_cutoff if f_cutoff > 0 else float("inf")

    if ratio >= 10:
        verdict = "Good smoothing - switching frequency is well above the filter's cutoff."
    elif ratio >= 2:
        verdict = "Some smoothing - expect noticeable residual ripple."
    else:
        verdict = "Poor smoothing - switching frequency is too close to the filter's cutoff."

    return {
        "cutoff_hz": round(f_cutoff, 2),
        "switching_to_cutoff_ratio": round(ratio, 2),
        "verdict": verdict,
    }


if __name__ == "__main__":
    # quick manual demo: python pwm_analysis.py
    for duty, freq in [(50, 10000), (50, 50), (20, 1000), (100, 10000)]:
        print(describe_signal(duty, freq))
        print("-" * 70)
