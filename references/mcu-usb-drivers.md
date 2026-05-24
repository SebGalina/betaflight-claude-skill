# MCU & USB Drivers — Betaflight Flight Controllers

This guide covers the MCUs found on Betaflight FCs, the USB drivers required per OS, and step-by-step procedures to stop wasting time hunting for the right driver.

## Identifying your FC's MCU

### Via Betaflight Configurator
**Setup** tab → `MCU` field (shown after connecting).

### Via CLI
```
version
```
Example output: `# Betaflight / STM32F405 (S405) 4.5.1`
The code in parentheses (`S405`, `S7X2`, `H743`…) identifies the MCU.

### Without a connection (unknown FC)
- Look up the FC name at [betaflight.com/docs/wiki/boards](https://betaflight.com/docs/wiki/boards)
- Or search `[FC name] betaflight target` on Google

---

## Common MCU overview

| MCU | Manufacturer | Clock | Prevalence | Notes |
|-----|-------------|-------|------------|-------|
| STM32F405 | ST Microelectronics | 168 MHz | Very common (mid-range FCs) | Reference MCU since BF 3.x |
| STM32F411 | ST Microelectronics | 100 MHz | Common (budget STM FCs) | Less powerful than F405 |
| STM32F722 | ST Microelectronics | 216 MHz | Common (mid/high-end) | F7, better filter performance |
| STM32F745 | ST Microelectronics | 216 MHz | Less common | F7 with more RAM |
| STM32H743 | ST Microelectronics | 480 MHz | High-end | H7, supports 8kHz + RPM filter |
| STM32H750 | ST Microelectronics | 480 MHz | High-end | H7 with external flash |
| STM32G473 | ST Microelectronics | 170 MHz | Emerging | G4, good perf/price ratio |
| **AT32F435** | Artery Technology | 288 MHz | **Very common (budget Chinese FCs)** | STM32 clone — **different driver** |
| AT32F437 | Artery Technology | 288 MHz | Common | AT32 variant with more RAM |
| APM32F405 | Geehy Semiconductor | 168 MHz | Present | STM32F4-compatible clone |
| GD32F405 | GigaDevice | 168 MHz | Present | STM32F4-compatible clone |

---

## Drivers by MCU and OS

### STM32 (F4, F7, G4, H7) — The standard case

#### Windows
Two separate drivers depending on the FC mode:

**Normal mode (VCP — virtual COM port):**
- Windows 10/11: auto-detected in most cases (built-in CDC ACM driver)
- If missing: install **STM32 Virtual COM Port Driver** from [st.com](https://www.st.com/en/development-tools/stsw-stm32102.html)
- Or use **ImpulseRC Driver Fixer** (see Tools section) — fixes VCP + DFU automatically in one click

**DFU mode (bootloader — for flashing):**
- FC appears as `STM32 BOOTLOADER` in Device Manager
- USB VID/PID: `0x0483` / `0xDF11`
- Required driver: **WinUSB** (via Zadig) or **STM32 DFU** (via ImpulseRC Driver Fixer)
- Without this driver, Betaflight Configurator will not detect the FC in DFU mode

Zadig procedure for DFU:
1. Put the FC in DFU mode (hold BOOT button while resetting, or type `bl` in CLI)
2. Open Zadig → Options → List All Devices
3. Select `STM32 BOOTLOADER`
4. Choose `WinUSB` → Install Driver

#### macOS
- VCP mode: no driver needed, port `/dev/tty.usbmodem*` appears automatically
- DFU mode: no driver needed, Betaflight Configurator detects directly

#### Linux
- VCP mode: built-in `cdc_acm` kernel module, port `/dev/ttyACM0` (or `ttyACM1`…)
- Access without `sudo`: add your user to the `dialout` group
  ```bash
  sudo usermod -a -G dialout $USER
  # then log out and back in
  ```
- DFU mode: `dfu-util` + udev rule
  ```bash
  sudo apt install dfu-util
  # udev rule for STM32 DFU:
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="df11", MODE="0664", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/49-stm32-dfu.rules
  sudo udevadm control --reload-rules
  ```

---

### AT32F435 / AT32F437 — The painful case

The AT32 is made by **Artery Technology** (华芯微特), a Chinese company. It is increasingly used on budget FCs (KayouMini, etc.) because it is cheaper than STM32 at equivalent performance. **Its USB stack differs from STM32** — STM32 drivers will not work.

#### USB IDs
| Mode | VID | PID |
|------|-----|-----|
| VCP (normal mode) | `0x2E3C` | `0x5740` (typical) |
| DFU (bootloader) | `0x2E3C` | `0x4004` (typical) |

> ⚠️ These IDs may vary with the AT32 firmware version. Verify in Windows Device Manager or with `lsusb` on Linux.

#### Windows

**Option A — Official Artery driver (recommended):**
1. Go to [arterychip.com](https://www.arterychip.com/en/support/tools.jsp)
2. Find "USB VCP Driver" under Tools & Drivers
3. Install the AT32 VCP Driver package
4. The FC should appear as a COM port in Device Manager

**Option B — Zadig (if Option A fails or is unavailable):**
1. Download [Zadig 2.9](https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe)
2. Plug the FC in normal mode → Options → List All Devices
3. Find the AT32 device (VID `2E3C`)
4. Assign `WinUSB` driver
5. Repeat for DFU mode if needed

> ⚠️ **ImpulseRC Driver Fixer does not support AT32** — it is designed for STM32 only. Do not use it for AT32 FCs.

#### macOS
- Usually auto-detected as a USB CDC serial port
- If missing: check `ls /dev/cu.usbmodem*` after plugging in
- No third-party driver normally required

#### Linux
- `cdc_acm` module works
- AT32-specific udev rule:
  ```bash
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2e3c", MODE="0664", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/49-at32-usb.rules
  sudo udevadm control --reload-rules
  ```

---

### APM32F405 / GD32F405 — STM32F4 clones

These MCUs are STM32F4-compatible clones. **STM32 drivers work** (same DFU VID/PID `0x0483/0xDF11` in most cases).
- Treat as STM32F405 for driver purposes
- If ImpulseRC Driver Fixer fails, fall back to Zadig + WinUSB

---

## Essential tools

### ImpulseRC Driver Fixer (Windows)
The easiest way to fix STM32 drivers on Windows. One click fixes:
- STM32 VCP (COM port)
- STM32 DFU (bootloader mode)

Download: [impulserc.com/pages/downloads](https://impulserc.com/pages/downloads)

> ⚠️ Do not use for AT32 — designed for STM32 only.

### Zadig (Windows)
Universal tool to manually assign a USB driver (WinUSB, libusb) to any device. Essential for AT32 and for cases where Driver Fixer fails.

Download: [zadig.akeo.ie](https://zadig.akeo.ie) — [zadig-2.9.exe direct](https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe)

General procedure:
1. Plug in the FC (normal or DFU mode as needed)
2. Open Zadig → Options → **List All Devices**
3. Select the correct device from the list
4. Choose `WinUSB` as the target driver
5. Click **Install Driver** or **Replace Driver**

### dfu-util (Linux / macOS)
CLI tool for flashing via DFU without the Configurator.
```bash
# Linux
sudo apt install dfu-util

# macOS
brew install dfu-util
```

---

## Quick diagnostic

**FC not showing up at all (no COM port, no USB device):**
- Is the USB cable a data cable (not charge-only)?
- Try a different USB port
- Check FC power (some FCs need a battery for USB to work)
- Windows: open Device Manager → "Other devices" → look for an unknown device

**FC shows up but Betaflight Configurator does not see it:**
- Missing VCP driver → ImpulseRC Driver Fixer (STM32) or Zadig (AT32)
- Linux: permission denied → `sudo usermod -a -G dialout $USER`

**FC detected but flashing fails (DFU error):**
- Not in DFU mode → hold BOOT during reset or type `bl` in CLI
- Missing DFU driver → Zadig → WinUSB on `STM32 BOOTLOADER` or AT32 DFU device
- AT32 + Windows: make sure Zadig replaced the driver on the DFU device (VID `2E3C`)

**"No DFU device found" in Configurator:**
- STM32: WinUSB driver not installed for DFU mode → Zadig
- AT32: same, but with Artery VID

---

## Known FCs and their MCU

| FC | MCU | Driver notes |
|----|-----|-------------|
| KayouMini | AT32F435G | Artery driver required on Windows |
| SpeedyBee F405 V3/V4 | STM32F405 | Standard STM32 |
| SpeedyBee F7 | STM32F722 | Standard STM32 |
| Mateksys F405 | STM32F405 | Standard STM32 |
| Mateksys H743 | STM32H743 | Standard STM32 |
| BetaFPV F4 | STM32F411 | Standard STM32 |
| Foxeer F745 | STM32F745 | Standard STM32 |
| Holybro Kakute H7 | STM32H743 | Standard STM32 |

> Non-exhaustive list. Always verify with `version` in CLI or the manufacturer's documentation.
