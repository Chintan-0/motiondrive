# MotionDrive

Turn your hands into a steering wheel. MotionDrive watches your webcam,
tracks your two hands with on-device computer vision, and turns the angle
between them into real steering input for racing games — no extra hardware,
no cloud, no Python knowledge required to use it.

> **Privacy:** all camera processing happens locally on your PC. No video or
> hand-tracking data is ever uploaded or sent anywhere, and MotionDrive does
> not need an internet connection to run.

---

## For players: installing and using MotionDrive

1. Download **`MotionDrive-Setup.exe`** and run it.
2. Follow the installer (Start Menu shortcut is created automatically;
   check the box if you also want a Desktop shortcut).
3. Open **MotionDrive** from the Start Menu.
4. On first launch: **Get Started → System Check → pick your camera →
   Calibrate → Test Steering → pick Input Mode → Start Driving.**
5. Press **START CAMERA**, then **START CONTROLLER**, then alt-tab into your
   game and drive.

Controller is always **OFF** when the app opens — you must press
**START CONTROLLER** every time. Press **ESC** or the big **STOP CONTROLLER**
button at any time to immediately release steering input.

### Calibrating

Calibration teaches MotionDrive your comfortable steering range:

1. *Place your hands on your imaginary steering wheel.*
2. *Keep your hands centered* → press **Capture**.
3. *Rotate fully left* → press **Capture**.
4. *Rotate fully right* → press **Capture**.
5. Done — the wheel now maps your real range of motion to -1.0 … +1.0.

Recalibrate anytime from **Settings → Recalibrate**.

### Keyboard vs. Virtual Gamepad

**Settings → Input Mode:**

- **Virtual Gamepad** (recommended) — MotionDrive presents itself to Windows
  as a real Xbox 360 controller with an analog steering axis. Most racing
  games auto-detect it; if not, map its left stick to steering in the game's
  control settings. Requires the ViGEmBus driver, which the installer sets
  up automatically.
- **Keyboard** — sends `A` (left) / `D` (right) key presses instead. No
  driver required; use this if a game doesn't see the virtual controller, or
  the driver couldn't install.

Use **Test Controller** in the sidebar to confirm your steering output before
opening a game.

### Profiles

Save separate sensitivity/smoothing/range/dead-zone/input-mode settings per
game under **Profiles** (built-in templates for Assetto Corsa, GTA V, Forza,
BeamNG, or add your own). Select a profile before starting the controller.

### Troubleshooting

| Symptom | Try |
|---|---|
| "Camera couldn't be started" | Close other apps using the webcam (Zoom, Teams, other browser tabs), pick a different camera in Settings, restart MotionDrive. |
| 🔴 Hands not detected | Make sure both hands are inside the frame, improve lighting, move slightly farther from the camera. |
| Game doesn't see the controller | Confirm Input Mode is "Virtual Gamepad" and that Test Controller responds to your hands; if the driver failed to install, switch to Keyboard mode or reinstall MotionDrive. |
| Steering feels jittery | Raise **Steering smoothing** in Settings. |
| Steering feels sluggish | Raise **Steering sensitivity**, or lower **Steering range**. |
| Need the log file | **Settings → Advanced → Open Logs** (also at `%LOCALAPPDATA%\MotionDrive\logs`). |

---

## For developers: building from source

### Requirements

- Windows 10/11, 64-bit
- Python **3.10** (MediaPipe wheels aren't yet published for newer Pythons)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (only needed to build the installer; `winget install JRSoftware.InnoSetup` works too)

### Run from source

```powershell
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python assets\generate_icon.py   # needs Pillow: pip install Pillow
.venv\Scripts\python -m motiondrive.app
```

### Run the tests

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests -q
```

All steering math, calibration, settings/profile persistence, and hand-tracking
geometry tests run without a physical webcam (synthetic landmark data / a
faked-out `mediapipe` module).

### Build the standalone executable

```powershell
.\build\build.ps1
```

This creates a venv if needed, installs dependencies, runs the test suite,
generates the icon, runs PyInstaller (`build\pyinstaller.spec`, `--onedir`
build to avoid MediaPipe's onefile data-unpacking issues), and produces:

- `build\dist\MotionDrive\MotionDrive.exe` (+ its dependency folder)
- `build\MotionDrive.exe` (copy of the exe for convenience)
- `build\MotionDrive-Portable.zip` — extract and run `MotionDrive.exe`, no install needed

### Build the installer

```powershell
.\installer\download_vigembus.ps1   # once, to fetch the official ViGEmBus driver redistributable
.\build\build.ps1                    # produces build\dist\MotionDrive
.\installer\build_installer.ps1      # installs Inno Setup via winget if missing, compiles the .exe
```

Produces `installer\MotionDrive-Setup.exe`, which installs the app, creates
Start Menu/Desktop shortcuts, silently installs the ViGEmBus virtual-gamepad
driver, checks for 64-bit Windows 10+, and registers an uninstaller.

---

## Architecture

```
src/motiondrive/
├── camera/          device enumeration + threaded webcam capture
├── hand_tracking/    MediaPipe Hands wrapper -> plain HandFrame/TrackingResult dataclasses
├── steering/         pure math: hand positions -> smoothed, calibrated SteeringState
├── calibration/       calibration wizard state machine + persisted Calibration
├── input/             InputBackend interface: VirtualGamepadBackend, KeyboardBackend, NullBackend
├── profiles/           per-game Profile persistence
├── settings/            beginner-friendly Settings persistence
├── safety/               SafetyWatchdog: the one place allowed to release game input
├── logging_/              rotating file logger (%LOCALAPPDATA%\MotionDrive\logs)
├── ui/                    PySide6 dashboard, calibration/settings/profiles/test-controller pages, tray
├── engine.py              composition root wiring camera -> hand_tracking -> steering -> safety/input
└── app.py                 entry point
```

`hand_tracking`, `steering`, `input`, and `safety` never import Qt/OpenCV
outward-facing types — they exchange plain dataclasses (`HandFrame`,
`SteeringState`, floats) — so any layer can be swapped later without
rewriting the others. This is the seam future gestures (accelerator, brake,
gear shifting, head/body tracking, custom gestures — see the MVP spec) will
plug into.

### Safety

A single `SafetyWatchdog` owns the active `InputBackend` and is the only
code path allowed to call `release()`. It's triggered by: both hands
missing beyond a frame threshold, low tracking confidence, a camera read
failure, any uncaught exception in the frame-processing loop, the ESC
shortcut, and the on-screen STOP CONTROLLER button. The controller always
starts in the **OFF** state on launch.
