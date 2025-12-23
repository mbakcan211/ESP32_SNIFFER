import sys
import serial
import serial.tools.list_ports
import json
import time
from collections import defaultdict

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QPushButton, QComboBox, 
                             QLabel, QTableWidget, QTableWidgetItem, QHeaderView, 
                             QSplitter, QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QColor

# --- Matplotlib Integration ---
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from datetime import datetime

# --- Configuration ---
BAUD_RATE = 115200

# --- Serial Worker ---
class SerialWorker(QThread):
    data_received = pyqtSignal(str) 
    json_received = pyqtSignal(dict) 

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
                        self.data_received.emit(line)
                        try:
                            if line.startswith("{") and line.endswith("}"):
                                data = json.loads(line)
                                self.json_received.emit(data)
                        except json.JSONDecodeError:
                            pass 
        except Exception as e:
            self.data_received.emit(f"[ERROR] {e}")
        finally:
            if hasattr(self, 'serial_port') and self.serial_port.is_open:
                self.serial_port.close()

    def stop(self):
        self.running = False
        self.wait()

# --- Graph Popup Window ---
class GraphWindow(QMainWindow):
    def __init__(self, mac_address, history_data):
        super().__init__()
        self.setWindowTitle(f"Target Analysis: {mac_address}")
        self.resize(600, 400)
        self.mac = mac_address
        self.history = history_data # Reference to the main data dict

        # Setup Plot
        self.figure = Figure(facecolor='#111111') # Dark background
        self.canvas = FigureCanvas(self.figure)
        self.setCentralWidget(self.canvas)
        
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#000000')
        self.ax.tick_params(colors='white')
        self.ax.spines['bottom'].set_color('white')
        self.ax.spines['top'].set_color('white')
        self.ax.spines['left'].set_color('white')
        self.ax.spines['right'].set_color('white')
        self.ax.set_title("Signal Strength (RSSI) over Time", color='white')
        self.ax.set_ylabel("RSSI (dBm)", color='white')
        
        # Timer to update graph
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(1000) # Update every second
        
        self.update_plot()

    def update_plot(self):
        if self.mac not in self.history:
            return

        data = self.history[self.mac]
        if not data['timestamps']:
            return

        # Prepare X and Y data
        x_data = data['timestamps']
        y_data = data['rssi']

        self.ax.clear()
        
        # Draw Line
        self.ax.plot(x_data, y_data, color='#00FF00', linewidth=2, marker='o', markersize=3)
        
        # Formatting
        self.ax.set_facecolor('#000000')
        self.ax.grid(True, color='#333333', linestyle='--')
        self.ax.set_ylim(-100, -20) # Standard WiFi range
        
        # Format Date on X Axis
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        self.figure.autofmt_xdate()
        
        self.canvas.draw()

# --- Main Window ---
class TerminalWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NORA-W106 Surveillance System (v2)")
        self.resize(1100, 750)
        
        # Data Storage: { "MAC": { "timestamps": [], "rssi": [], "type": "" } }
        self.device_history = defaultdict(lambda: {"timestamps": [], "rssi": [], "type": "Unknown"})

        # Layouts
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        top_bar = QHBoxLayout()
        
        # Controls
        self.port_selector = QComboBox()
        self.refresh_ports()
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.btn_connect.setStyleSheet("background-color: #005500; color: white;")
        
        top_bar.addWidget(QLabel("Port:"))
        top_bar.addWidget(self.port_selector)
        top_bar.addWidget(self.btn_connect)
        main_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Table
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(4)
        self.device_table.setHorizontalHeaderLabels(["MAC Address", "Type", "RSSI", "Status"])
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.device_table.doubleClicked.connect(self.open_graph_window) # Double click to open graph
        self.device_table.setStyleSheet("background-color: #111; color: #00FF00; gridline-color: #333;")
        splitter.addWidget(self.device_table)

        # Terminal
        self.terminal_display = QTextEdit()
        self.terminal_display.setReadOnly(True)
        self.terminal_display.setStyleSheet("background-color: #000; color: #00FF00; font-family: Monospace;")
        splitter.addWidget(self.terminal_display)

        main_layout.addWidget(splitter)
        
        self.lbl_info = QLabel("Double-click a row to see analysis graphs.")
        main_layout.addWidget(self.lbl_info)

        self.worker = None
        self.active_graphs = [] # Keep track of open windows

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
            self.worker.json_received.connect(self.process_json_data)
            self.worker.start()
            self.btn_connect.setText("DISCONNECT")
            self.btn_connect.setStyleSheet("background-color: #AA0000; color: white;")
        else:
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("CONNECT")
            self.btn_connect.setStyleSheet("background-color: #005500; color: white;")

    def log_to_terminal(self, text):
        if not text.startswith("{"):
            self.terminal_display.append(text)

    def process_json_data(self, data):
        # 1. Update Internal History
        timestamp = datetime.now()
        devices_list = data.get("devices", [])
        
        self.device_table.setRowCount(len(devices_list))
        
        for row, dev in enumerate(devices_list):
            mac = dev.get("mac", "Unknown")
            rssi = dev.get("rssi", 0)
            dev_type = dev.get("type", "Unknown")
            
            # Store data
            self.device_history[mac]["timestamps"].append(timestamp)
            self.device_history[mac]["rssi"].append(rssi)
            self.device_history[mac]["type"] = dev_type
            
            # Limit history to last 100 points (prevent memory overflow)
            if len(self.device_history[mac]["timestamps"]) > 100:
                self.device_history[mac]["timestamps"].pop(0)
                self.device_history[mac]["rssi"].pop(0)

            # Update GUI Table
            self.device_table.setItem(row, 0, QTableWidgetItem(mac))
            self.device_table.setItem(row, 1, QTableWidgetItem(dev_type))
            self.device_table.setItem(row, 2, QTableWidgetItem(str(rssi)))
            
            # Status Logic
            seen_ms = dev.get('seen_ms', 0)
            status = "Active" if seen_ms < 2000 else f"{seen_ms/1000:.1f}s ago"
            self.device_table.setItem(row, 3, QTableWidgetItem(status))

    def open_graph_window(self, item):
        row = item.row()
        mac = self.device_table.item(row, 0).text()
        
        # Check if already open
        for win in self.active_graphs:
            if win.mac == mac and win.isVisible():
                win.raise_()
                return

        # Create new window
        graph_win = GraphWindow(mac, self.device_history)
        graph_win.show()
        self.active_graphs.append(graph_win)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TerminalWindow()
    window.show()
    sys.exit(app.exec())