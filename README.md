# ESP32 WiFi Sniffer & Surveillance System

A low-cost, real-time WiFi traffic analysis and physical distance estimation tool. This project combines an **ESP32** (running FreeRTOS) for high-speed packet capture and a **Python/PyQt6** dashboard for visualization and target tracking.

![Dashboard Screenshot](docs/screenshot.png)

## 🚀 Features

*   **Promiscuous Mode Sniffing:** Captures IEEE 802.11 Management frames (Beacons, Probe Requests) without connecting to a network.
*   **Real-Time Analysis:** High-speed data stream from ESP32 to PC via Serial (921600 baud).
*   **Distance Estimation:** Uses RSSI and the Log-Distance Path Loss model to estimate physical distance to targets.
*   **Target Tracking:** Select specific MAC addresses to visualize signal strength over time.
*   **Deauth Detection:** Identifies and flags potential de-authentication attacks.
*   **FreeRTOS Architecture:** Uses Producer-Consumer pattern on the ESP32 for non-blocking performance.

## 🛠️ Hardware Required

*   **ESP32 Development Board** (Tested on Arduino Nano ESP32 / u-blox NORA-W106)
*   USB Cable (Data capable)
*   (Optional) RGB LED for status indication

## 📦 Installation

### 1. Firmware (ESP32)
1.  Open `firmware/esp32_sniffer_main/esp32_sniffer_main.ino` in Arduino IDE.
2.  Install the **ESP32 Board Manager** by Espressif.
3.  Select your board and port.
4.  Upload the code.

### 2. Software (PC)
1.  Install Python 3.10+.
2.  Install dependencies:
    ```bash
    pip install -r software/requirements.txt
    ```
3.  Run the dashboard:
    ```bash
    python software/main.py
    ```

## ⚙️ Usage

1.  Connect the ESP32 to your PC.
2.  Select the correct **COM Port** in the GUI and click **Connect**.
3.  The table will populate with nearby devices.
4.  **Double-click** a row to open the **Target Analysis** window.
5.  Use the **Calibration** menu to adjust Path Loss Exponent ($n$) based on your environment (e.g., Office vs. Open Space).

## 📄 Documentation

The full technical report (in Turkish) is available in the `docs/` folder: Project_Report_TR.pdf.

## ⚠️ Disclaimer

This project is for **educational and research purposes only**. Monitoring wireless traffic without permission may be illegal in your jurisdiction. The authors are not responsible for misuse.

## 📜 License

This project is licensed under the MIT License - see the LICENSE file for details.
