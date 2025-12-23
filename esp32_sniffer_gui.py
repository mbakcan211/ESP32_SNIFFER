import sys
import serial
import serial.tools.list_ports
import json
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QLineEdit, QPushButton, 
                             QComboBox, QLabel, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QSplitter)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QColor, QTextCursor

# --- Configuration ---
BAUD_RATE = 115200

# --- Serial Worker ---
class SerialWorker(QThread):
    data_received = pyqtSignal(str) # Raw text for terminal
    json_received = pyqtSignal(dict) # Parsed JSON for list

    def __init__(self, port_name):
        super().__init__()
        self.port_name = port_name
        self.running = True

    def run(self):
        try:
            self.serial_port = serial.Serial(self.port_name, BAUD_RATE, timeout=1)
            while self.running:
                if self.serial_port.in_waiting:
                    line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        # Send raw line to terminal
                        self.data_received.emit(line)
                        
                        # Try to parse as JSON for the list
                        try:
                            if line.startswith("{") and line.endswith("}"):
                                data = json.loads(line)
                                self.json_received.emit(data)
                        except json.JSONDecodeError:
                            pass # Not JSON? Ignore it for the list.
        except Exception as e:
            self.data_received.emit(f"[ERROR] {e}")
        finally:
            if hasattr(self, 'serial_port') and self.serial_port.is_open:
                self.serial_port.close()

    def stop(self):
        self.running = False
        self.wait()

# --- Main Window ---
class TerminalWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NORA-W106 Surveillance System")
        self.resize(1000, 700)
        
        # Central Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Top Bar
        top_bar = QHBoxLayout()
        self.port_selector = QComboBox()
        self.refresh_ports()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.btn_connect.setStyleSheet("background-color: #005500; color: white;")
        
        top_bar.addWidget(QLabel("Port:"))
        top_bar.addWidget(self.port_selector)
        top_bar.addWidget(self.btn_connect)
        main_layout.addLayout(top_bar)

        # Splitter (List on Top, Terminal on Bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # --- 1. The Device List (Table) ---
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(4)
        self.device_table.setHorizontalHeaderLabels(["MAC Address", "Type", "RSSI", "Last Seen"])
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.device_table.itemClicked.connect(self.on_device_selected)
        self.device_table.setStyleSheet("background-color: #111; color: #00FF00; gridline-color: #333;")
        splitter.addWidget(self.device_table)

        # --- 2. The Terminal (Log) ---
        self.terminal_display = QTextEdit()
        self.terminal_display.setReadOnly(True)
        self.terminal_display.setStyleSheet("background-color: #000; color: #00FF00; font-family: Monospace;")
        splitter.addWidget(self.terminal_display)

        main_layout.addWidget(splitter)
        
        # Selection Label
        self.lbl_selected = QLabel("Selected Target: None")
        self.lbl_selected.setStyleSheet("font-weight: bold; color: #FF0000; font-size: 14px;")
        main_layout.addWidget(self.lbl_selected)

        self.worker = None
        self.selected_mac = None

    def refresh_ports(self):
        self.port_selector.clear()
        for port in serial.tools.list_ports.comports():
            self.port_selector.addItem(port.device)

    def toggle_connection(self):
        if self.worker is None:
            port = self.port_selector.currentText()
            if not port: return
            self.worker = SerialWorker(port)
            self.worker.data_received.connect(self.log_to_terminal)
            self.worker.json_received.connect(self.update_table) # Connect JSON data
            self.worker.start()
            self.btn_connect.setText("DISCONNECT")
            self.btn_connect.setStyleSheet("background-color: #AA0000; color: white;")
        else:
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("CONNECT")
            self.btn_connect.setStyleSheet("background-color: #005500; color: white;")

    def log_to_terminal(self, text):
        # Only log non-JSON text to keep terminal clean (optional)
        if not text.startswith("{"):
            self.terminal_display.append(text)

    def update_table(self, data):
        # 'data' is the dictionary {"devices": [...]}
        devices = data.get("devices", [])
        
        self.device_table.setRowCount(len(devices))
        
        for row, dev in enumerate(devices):
            mac = dev.get("mac", "Unknown")
            rssi = dev.get("rssi", 0)
            dev_type = dev.get("type", "Unknown")
            seen = f"{dev.get('seen_ms', 0) / 1000:.1f}s ago"

            self.device_table.setItem(row, 0, QTableWidgetItem(mac))
            self.device_table.setItem(row, 1, QTableWidgetItem(dev_type))
            self.device_table.setItem(row, 2, QTableWidgetItem(str(rssi)))
            self.device_table.setItem(row, 3, QTableWidgetItem(seen))
            
            # Highlight selected row color
            if mac == self.selected_mac:
                for col in range(4):
                    self.device_table.item(row, col).setBackground(QColor("#330000"))

    def on_device_selected(self, item):
        row = item.row()
        mac_item = self.device_table.item(row, 0)
        self.selected_mac = mac_item.text()
        self.lbl_selected.setText(f"Selected Target: {self.selected_mac}")
        self.log_to_terminal(f">> Target Locked: {self.selected_mac}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TerminalWindow()
    window.show()
    sys.exit(app.exec())