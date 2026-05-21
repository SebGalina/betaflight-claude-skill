# Betaflight Configurator — Tab Navigation Reference

Use this file to direct users to the right tab in the Betaflight App and its documentation. The app is at https://app.betaflight.com (web) or via the desktop Configurator.

> **Docs base URL**: `https://betaflight.com/docs/wiki/guides/current/`

## Tab overview

| Tab | Purpose | Key doc(s) |
|-----|---------|------------|
| Firmware Flasher | Flash new firmware to the FC | [USB Flashing] |
| Setup | Sensor check, gyro calibration, board orientation | [Flight Controller Orientation], [Supported Sensors] |
| Ports | UART function assignment (RX, GPS, Blackbox, MSP…) | [Serial] |
| Configuration | Features, ESC protocol, mixer, loop rate, board alignment | [Configuration] |
| Power & Battery | Voltage/current sensing, cell count, sag compensation | [Battery Monitoring], [Current Sensor Calibration] |
| Failsafe | Signal-loss procedure (drop, land, GPS rescue) | [Failsafe] |
| Presets | Apply community-maintained config bundles | [Community Presets], [Presets 4.3] |
| PID Tuning | P/I/D/F gains, filters, sliders, rate profiles | [PID Tuning Guide], [Freestyle Tuning Principles], [Dynamic D], [Dynamic Idle], [Feed Forward 2.0], [Iterm Relax Explained] |
| Receiver | RX protocol, channel map, stick endpoints, RSSI | [Receivers (RX)], [RSSI] |
| Modes | AUX switch → flight mode mapping | [Modes], [Controls] |
| Adjustments | In-flight parameter changes via AUX switches | [In-Flight Adjustments] |
| Servos | Servo output mapping and limits (fixed-wing/quad with tilt) | [Servos & Servo Tilt] |
| GPS | GPS provider, baud rate, GPS Rescue parameters | [GPS], [GPS Rescue], [Magnetometer] |
| Motors | Motor test, spin direction, DSHOT settings | [DSHOT], [DSHOT RPM Filtering], [ESC Telemetry], [Mixer] |
| OSD | On-screen display element layout and alarms | [Display], [OSD Profiles] |
| Video Transmitter | VTX band/channel/power and pit mode | [VTX], [SmartAudio], [IRC Tramp] |
| LED Strip | LED color and mode configuration | [LED Strip] |
| Transponder | Race transponder setup | [Transponder] |
| Sensors | Barometer, compass, sonar enable/disable | [Supported Sensors], [Barometer] |
| Blackbox | On-board logging configuration and download | [Blackbox Flight Data Recorder], [Serial Blackbox Logging] |
| CLI | Raw CLI access | [Command Line Interface (CLI)] — see also `cli-commands.md` |

## Documentation URLs by tab

### Firmware Flasher
- USB Flashing: https://betaflight.com/docs/wiki/guides/current/USB-Flashing
- DFU Hijacking (if normal flash fails): https://betaflight.com/docs/wiki/guides/current/DFU-Hijacking
- Broken USB rescue: https://betaflight.com/docs/wiki/guides/current/Broken-USB-Rescue

### Setup
- Flight Controller Orientation: https://betaflight.com/docs/wiki/guides/current/Flight-Controller-Orientation
- Supported Sensors: https://betaflight.com/docs/wiki/guides/current/Supported-Sensors
- Arming sequence & safety: https://betaflight.com/docs/wiki/guides/current/Arming-Sequence-And-Safety

### Ports
- Serial ports guide: https://betaflight.com/docs/wiki/guides/current/Serial
- SoftSerial: https://betaflight.com/docs/wiki/guides/current/SoftSerial
- Serial Passthrough: https://betaflight.com/docs/wiki/guides/current/Serial-Pass-Through

### Configuration
- Configuration overview: https://betaflight.com/docs/wiki/guides/current/Configuration
- Mixer: https://betaflight.com/docs/wiki/guides/current/Mixer
- Profiles (PID profiles): https://betaflight.com/docs/wiki/guides/current/Profiles
- Motor remapping: https://betaflight.com/docs/wiki/guides/current/Remapping-Motors-with-Resource-Command
- Resource remapping: https://betaflight.com/docs/wiki/guides/current/Resource-remapping
- 3D setup: https://betaflight.com/docs/wiki/guides/current/3D-Setup
- Safety: https://betaflight.com/docs/wiki/guides/current/Safety
- Runaway takeoff prevention: https://betaflight.com/docs/wiki/guides/current/Runaway-Takeoff-Prevention

### Power & Battery
- Battery Monitoring: https://betaflight.com/docs/wiki/guides/current/Battery
- Current Sensor Calibration: https://betaflight.com/docs/wiki/guides/current/Current-Sensor-Calibration
- Units: https://betaflight.com/docs/wiki/guides/current/Units

### Failsafe
- Failsafe: https://betaflight.com/docs/wiki/guides/current/Failsafe
- GPS Rescue: https://betaflight.com/docs/wiki/guides/current/GPS-Rescue
- Crash Recovery: https://betaflight.com/docs/wiki/guides/current/Crash-Recovery

### Presets
- Community Presets: https://betaflight.com/docs/wiki/guides/current/Community-Presets
- Presets 4.3 (how presets work): https://betaflight.com/docs/wiki/guides/current/Presets-in-BF-4-3
- Preset authoring: https://betaflight.com/docs/wiki/guides/current/Quick-Start-for-Preset-Authors

### PID Tuning
- PID Tuning Guide: https://betaflight.com/docs/wiki/guides/current/PID-Tuning-Guide
- Freestyle Tuning Principles: https://betaflight.com/docs/wiki/guides/current/Freestyle-Tuning-Principles
- Dynamic D: https://betaflight.com/docs/wiki/guides/current/Dynamic-D
- Dynamic Idle: https://betaflight.com/docs/wiki/guides/current/Dynamic-Idle
- Feed Forward 2.0: https://betaflight.com/docs/wiki/guides/current/Feed-Forward-2-0
- Iterm Relax Explained: https://betaflight.com/docs/wiki/guides/current/I-Term-Relax-Explained
- Rate Calculator: https://betaflight.com/docs/wiki/guides/current/Rate-Calculator
- Integrated Yaw: https://betaflight.com/docs/wiki/guides/current/Integrated-Yaw
- Yaw Spin Recovery: https://betaflight.com/docs/wiki/guides/current/Yaw-Spin-Recovery-and-Gyro-Overflow-Detect
- Acro Trainer: https://betaflight.com/docs/wiki/guides/current/Acro-Trainer
- Launch Control: https://betaflight.com/docs/wiki/guides/current/Launch-Control
- See also: `pid-tuning.md` in this skill

### Receiver
- Receivers (RX): https://betaflight.com/docs/wiki/guides/current/Rx
- RSSI: https://betaflight.com/docs/wiki/guides/current/Rssi
- FrSky FPORT: https://betaflight.com/docs/wiki/guides/current/FrSky-FPort-Protocol
- FrSky SPI RX: https://betaflight.com/docs/wiki/guides/current/FrSky-SPI-RX
- SBus FPort and OpenTX: https://betaflight.com/docs/wiki/guides/current/SBus-FPort-and-OpenTX
- FlySky IBUS Telemetry: https://betaflight.com/docs/wiki/guides/current/IBus-telemetry
- Spektrum Bind: https://betaflight.com/docs/wiki/guides/current/Spektrum-bind
- HID Joystick Support: https://betaflight.com/docs/wiki/guides/current/HID-Joystick-Support

### Modes
- Modes: https://betaflight.com/docs/wiki/guides/current/Modes
- Controls: https://betaflight.com/docs/wiki/guides/current/Controls
- Paralyze (team races): https://betaflight.com/docs/wiki/guides/current/Paralyze-for-Team-Races

### Adjustments
- In-Flight Adjustments: https://betaflight.com/docs/wiki/guides/current/Inflight-Adjustments

### Servos
- Servos & Servo Tilt: https://betaflight.com/docs/wiki/guides/current/Servos-And-SERVO_TILT-for-3-1
- Fixed-Wing setup: https://betaflight.com/docs/wiki/guides/current/Setup-for-a-Fixed-Wing-Aircraft

### GPS
- GPS: https://betaflight.com/docs/wiki/guides/current/Gps
- GPS Rescue: https://betaflight.com/docs/wiki/guides/current/GPS-Rescue
- Magnetometer/Compass: https://betaflight.com/docs/wiki/guides/current/Magnetometer
- Barometer: https://betaflight.com/docs/wiki/guides/current/Barometer
- Position Hold 2025.12: https://betaflight.com/docs/wiki/guides/current/Position-Hold-2025-12
- MAVLink / Mission Planner: https://betaflight.com/docs/wiki/guides/current/MAVLinkELRS

### Motors
- DSHOT: https://betaflight.com/docs/wiki/guides/current/Dshot
- DSHOT RPM Filtering: https://betaflight.com/docs/wiki/guides/current/DSHOT-RPM-Filtering
- ESC Telemetry: https://betaflight.com/docs/wiki/guides/current/ESC-Telemetry
- Mixer: https://betaflight.com/docs/wiki/guides/current/Mixer
- Reversed Motors: https://betaflight.com/docs/wiki/guides/current/Reversed-motor-direction
- Soft Mounting / Noise: https://betaflight.com/docs/wiki/guides/current/Soft-Mounting-and-Noise-Reduction

### OSD
- Display: https://betaflight.com/docs/wiki/guides/current/Display
- OSD Profiles: https://betaflight.com/docs/wiki/guides/current/OSD-Profiles
- External OSD (MWOSD): https://betaflight.com/docs/wiki/guides/current/External-OSD-MWOSD-CMS
- FPV Camera Control: https://betaflight.com/docs/wiki/guides/current/FPV-Camera-Control-Joystick-Emulation

### Video Transmitter
- VTX: https://betaflight.com/docs/wiki/guides/current/VTX
- SmartAudio: https://betaflight.com/docs/wiki/guides/current/SmartAudio
- IRC Tramp: https://betaflight.com/docs/wiki/guides/current/IRC-Tramp
- RunCam Device Protocol: https://betaflight.com/docs/wiki/guides/current/RunCam-Device-Protocol

### LED Strip
- LED Strip: https://betaflight.com/docs/wiki/guides/current/LED-Strip-Functionality
- FC LEDs: https://betaflight.com/docs/wiki/guides/current/FC-LEDs
- Buzzer: https://betaflight.com/docs/wiki/guides/current/Buzzer
- Buzzer Mute Mode: https://betaflight.com/docs/wiki/guides/current/Buzzer-Mute-Mode

### Transponder
- Transponder: https://betaflight.com/docs/wiki/guides/current/Transponder

### Sensors
- Supported Sensors: https://betaflight.com/docs/wiki/guides/current/Supported-Sensors
- Barometer: https://betaflight.com/docs/wiki/guides/current/Barometer
- Sonar: https://betaflight.com/docs/wiki/guides/current/Sonar
- Gyro Offset Yaw: https://betaflight.com/docs/wiki/guides/current/Gyro-Offset-Yaw
- Debug Modes: https://betaflight.com/docs/wiki/guides/current/Debug-Modes

### Blackbox
- Blackbox Flight Data Recorder: https://betaflight.com/docs/wiki/guides/current/Black-Box-logging-and-usage
- Serial Blackbox Logging: https://betaflight.com/docs/wiki/guides/current/Serial-BlackBox-Logging
- Blackbox Explorer (web app): https://blackbox.betaflight.com
- BBE MinMax Control: https://betaflight.com/docs/wiki/guides/current/BBE-MinMax-control-manual
- BBE Power Spectral Density: https://betaflight.com/docs/wiki/guides/current/BBE-Power-Spectral-Density-charts
- Mass Storage Device (MSC): https://betaflight.com/docs/wiki/guides/current/Mass-Storage-Device-Support

### CLI
- CLI reference: https://betaflight.com/docs/wiki/guides/current/Cli
- 2025.12 CLI Command Reference: https://betaflight.com/docs/wiki/guides/current/Betaflight-2025.12-CLI-commands
- See also: `cli-commands.md` in this skill

## General resources

- Getting Started: https://betaflight.com/docs/wiki/getting-started
- FAQ: https://betaflight.com/docs/wiki/guides/current/FAQ
- Safety: https://betaflight.com/docs/wiki/guides/current/Safety
- Flying Tips: https://betaflight.com/docs/wiki/guides/current/Flying-Tips
- Hardware Reference: https://betaflight.com/docs/wiki/guides/current/Hardware-Reference
- Telemetry: https://betaflight.com/docs/wiki/guides/current/Telemetry
- Pinio & PinioBox: https://betaflight.com/docs/wiki/guides/current/Pinio-and-PinioBox
- Deep Dive articles: https://betaflight.com/docs/wiki/guides/current/Deep-Dive
- Community: https://discord.betaflight.com/invite · https://oscarliang.com · https://www.vitroidfpv.com
