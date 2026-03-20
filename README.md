# GoPro_USB_Controller
A lightweight Python GUI for controlling a GoPro Hero 13 Black over USB — designed for scientific slow-motion capture workflows. Built on top of GoPro's official [Open GoPro](https://gopro.github.io/OpenGoPro/) SDK.
 
Originally developed for femtosecond laser ablation imaging at the University of Copenhagen, but suitable for any application requiring programmatic GoPro control from a desktop.
 
---
 
## Features
 
- **Wired USB connection** — no WiFi or Bluetooth required
- **Slow-motion presets** — resolution/fps combinations optimised for high-speed capture, defaulting to 1080·240fps with Linear FOV for scientific use
- **Full FOV control** — all 13 lens modes supported (Wide, Linear, HyperView, SuperView and variants)
- **One-click record trigger** — start and stop recording from the desktop
- **SD card file browser** — list, select and download MP4 files from the camera over USB
- **Inline video preview** — play downloaded clips directly in the GUI with scrub control
- **Battery status** — polled every 5 seconds
- **Keep-alive** — prevents the camera from sleeping during a session
- **Dark UI** throughout
 
---
 
## Requirements
 
### Hardware
- GoPro Hero 13 Black
- USB-C data cable (not a charge-only cable)
 
### Software
- Python 3.11+
- [Anaconda](https://www.anaconda.com/) recommended
 
### Python packages
```bash
pip install open-gopro opencv-python pillow
```
 
---
 
## Installation
 
```bash
# Create a dedicated environment (recommended)
conda create -n gopro python=3.11
conda activate gopro
 
# Install dependencies
pip install open-gopro opencv-python pillow
 
# Clone or download gopro_controller.py, then run
python gopro_controller.py
```
 
---
 
## Camera Setup
 
Before connecting, configure the GoPro:
 
1. Power on the camera
2. Swipe down on the rear LCD
3. Tap **Connections** → **USB Connection** → select **GoPro Connect**
 
> The camera must be manually powered on before each session. USB wired mode does not support programmatic power-on.
 
---
 
## Usage
 
### Control tab
- Select a **slow-motion preset** and **field of view**, then click **Apply Settings** — the camera will switch modes immediately
- Press **Start Recording** to begin capture; press again to stop
- Files are saved to the SD card at full quality
 
### SD Card Files tab
- Click **Refresh** to list all MP4 files on the camera's SD card
- Select one or more files and click **Download** to save them locally
- Select a downloaded file (shown in green) and click **Play** to preview inline
- Use **Open local file…** to preview any MP4 already on disk
 
### Recommended settings for scientific slow-motion
 
| Setting | Value | Reason |
|---|---|---|
| Preset | 1080 · 240fps | Highest frame rate; 8× slow-mo at 30fps playback |
| FOV | Linear | No barrel distortion; spatially accurate |
| Protune | Default | Consistent exposure for frame-to-frame analysis |
 
> At 240fps, a 1-second event is stretched to 8 seconds of footage at normal playback speed. In post-processing, interpret the footage at 30fps in DaVinci Resolve or Premiere Pro to achieve slow-motion output.
 
---
 
## Known Limitations
 
- **Live preview not supported over USB** on work/managed computers — the UDP stream on port 8554 is typically blocked by corporate firewalls. Preview works on unmanaged machines if the port is open.
- **Power-on via USB is not supported** by the Open GoPro API — the camera must be switched on manually before connecting.
- **Settings apply to Video mode only** — the camera must be in Video mode for resolution/fps changes to take effect.
 
---
 
## Dependencies & Attributions
 
| Package | Purpose | Licence |
|---|---|---|
| [open-gopro](https://github.com/gopro/OpenGoPro) | Official GoPro USB/WiFi control SDK | MIT |
| [opencv-python](https://github.com/opencv/opencv-python) | Video frame decoding for inline preview | MIT / Apache 2.0 |
| [Pillow](https://python-pillow.org/) | Image conversion for tkinter canvas rendering | HPND |
| tkinter | GUI framework (Python standard library) | PSF |
 
This project uses the **Open GoPro** platform, developed and maintained by GoPro, Inc. Open GoPro is an open-source initiative providing a documented HTTP and BLE API for GoPro cameras. Full API documentation is available at [gopro.github.io/OpenGoPro](https://gopro.github.io/OpenGoPro/).
 
---
 
## Acknowledgements
 
Developed at the **Ice and Climate group, Niels Bohr Institute, University of Copenhagen** as part of the isoDEEPICE project laser ablation imaging work.
 
Camera control built on the [Open GoPro Python SDK](https://github.com/gopro/OpenGoPro/tree/main/demos/python/sdk_wireless_camera_control) (MIT licence).
 
This tool was developed with the assistance of **[Claude](https://claude.ai)** (Anthropic), which was used to write and iteratively debug the Python code throughout the development process.
 
---
 
## Licence
 
MIT — free to use, modify and distribute with attribution.
 