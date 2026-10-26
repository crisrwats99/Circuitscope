# Circuitscope — An Electronics Auto-grader

Circuitscope measures and grades undergraduate electronics practicals. A small measurement box (STM32 NUCLEO-G431KB) samples voltages and currents on a student's circuit, and a Python desktop app turns those samples into marks, feedback and LMS-ready exports.

Students can self-check their circuits at any time; tutors and lecturers can run formal assessments and export results.

> EEE4022S Final Year Project (JS-01), University of Cape Town, 2026. Supervisor: Jarryd Son.

---

## Features

- **5 voltage inputs (P1–P5) + 2 current inputs (I1, I2)**, sampled by the STM32's two 12-bit ADCs at up to 100 kHz, 4000 points per channel
- **Differential voltages** computed on the PC from fixed probe inputs (any test point vs. another, or vs. COM)
- **Assessment creator** — practicals built as Questions → Parts → Items, saved and shared as JSON
- **Three answer modes per item**
  - *Single* — fixed expected value with absolute or percentage tolerance
  - *Varying* — lecturer formula (e.g. `f = 1/(2πRC)`) using each student's own component values
  - *Waveform* — frequency, peak, peak-to-peak, RMS, DC offset, period, duty cycle, gain, phase, rise/fall time
- **Student mode** — locked by an admin password; students see correct/incorrect only and submit a signed file
- **Tutor grading** — fast marking of theory items, flagged mismatches, tamper-checked submissions
- **Box watermark** — every submission carries the STM32's 96-bit unique ID
- **Per-box, per-channel two-point calibration**
- **CSV/XLSX export** — mark per question, overall mark, and a detailed feedback export
- **Simulated box** for demos and development without hardware
- **16×2 I²C LCD** on the box shows status and step-by-step probe instructions

## How it works

```
 Student circuit ──► Front-end (dividers, LM358 buffers, shunts)
                          │
                          ▼
                 STM32G431KB  — sampling only (raw ADC counts)
                          │  USB serial, 115200 8N1
                          ▼
                 PC app (Python / PySide6)
                 calibration · grading · feedback · export
```

The firmware does no scaling or grading. All conversion, tolerances and formulas live on the PC, so a rubric can be changed without re-flashing the box.

## Repository layout (PC app)

| Folder / file | Purpose |
| --- | --- |
| `core/` | Assessment model, formulas, grading, waveform features, submission/signing. No UI or hardware code. |
| `hardware/` | Serial protocol, calibration, device driver and simulated box |
| `ui/` | PySide6 screens: creator, student, tutor grading, debug, calibration |
| `tests/` | pytest suite |
| `main.py` | Application entry point |

## Getting started

**Requirements:** Python 3.12+, Windows (tested), a NUCLEO-G431KB running the Circuitscope firmware (optional — the simulator works without it).

```bash
git clone https://github.com/<your-username>/circuitscope.git
cd circuitscope
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install PySide6 pyqtgraph pyserial pytest
python main.py
```

> On Windows, keep the project in a short path outside OneDrive (e.g. `C:\dev\Circuitscope_UI`) — PySide6 can fail to install in deep paths due to the 260-character limit.

**Try it without hardware:** select *Simulated box (demo)* → **Connect** → **Measure voltage**.

**With the box:** select *Real box (USB cable)*, choose the COM port, **Connect**, then calibrate each channel from the calibration screen before grading.

## Serial protocol (v1)

| PC sends | Box replies |
| --- | --- |
| `PING` | `PONG` |
| `INFO?` | `INFO 1 4000 100000 12 3300` |
| `ID?` | `ID <w0> <w1> <w2>` (96-bit UID, hex) |
| `SAMPLE <ch>[,<ch2>] <rate> <n>` | `DATA <n> c0,c1,…` per channel, then `DONE <actual_rate_hz>` |
| *invalid input* | `ERR <CODE> <message>` (`BADCMD`, `BADARGS`, `BADCH`, `BADRATE`, `BADCOUNT`, `HWFAIL`) |

Example: `SAMPLE P1,P2 20000 400` — a DC reading of P1 against P2.

## Running the tests

```bash
python -m pytest
```

## Limitations

- Voltage inputs: ±12 V specified (front-end designed for ±15 V)
- Current inputs: 1 Ω shunt, 50 mA slow-blow fuse; shunt common-mode range 0–3.4 V
- Submission signing stops casual editing, not a determined attacker — replace `core/signing_key.py` with your own random key before real use, and don't commit it
- Hardware is built on veroboard; no PCB layout

## License

<!-- e.g. MIT -->
