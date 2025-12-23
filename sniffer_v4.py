import sys
import serial
import serial.tools.list_ports
import json
import time
import numpy as np
from collections import defaultdict
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QPushButton, QComboBox, 
                             QLabel, QTableWidget, QTableWidgetItem, QHeaderView, 
                             QSplitter, QFrame)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon

# --- Matplotlib Integration ---
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates

# --- Configuration ---
BAUD_RATE = 921600
DARK_BG = "#121212"
NEON_GREEN = "#00FF00"
NEON_CYAN = "#00FFFF"
NEON_RED = "#FF0033"
TEXT_COLOR = "#E0E0E0"

# --- Serial Worker (Handles Data in Background) ---
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
                    try:
                        line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            self.data_received.emit(line)
                            if line.startswith("{") and line.endswith("}"):
                                data = json.loads(line)
                                self.json_received.emit(data)
                    except Exception:
                        pass 
        except Exception as e:
            self.data_received.emit(f"[ERROR] {e}")
        finally:
            if hasattr(self, 'serial_port') and self.serial_port.is_open:
                self.serial_port.close()

    def stop(self):
        self.running = False
        self.wait()

# --- Analysis Window (Graph + Distance) ---
class GraphWindow(QMainWindow):
    def __init__(self, mac_address, history_data):
        super().__init__()
        self.setWindowTitle(f"TARGET ANALYSIS: {mac_address}")
        self.resize(1000, 600)
        self.setStyleSheet(f"background-color: {DARK_BG}; color: {TEXT_COLOR};")
        
        self.mac = mac_address
        self.history = history_data 

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        # 1. Graph Area (Left Side - 70%)
        self.figure = Figure(facecolor=DARK_BG) 
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, stretch=7)
        
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#1e1e1e')
        
        # 2. Stats Dashboard (Right Side - 30%)
        stats_panel = QVBoxLayout()
        stats_panel.setContentsMargins(20, 20, 20, 20)
        
        # Add styled labels
        self.lbl_current = self.create_stat_card("CURRENT RSSI", "- dBm")
        self.lbl_dist = self.create_stat_card("EST. DISTANCE", "- m")
        self.lbl_avg = self.create_stat_card("AVG SIGNAL", "- dBm")
        self.lbl_max = self.create_stat_card("MAX PEAK", "- dBm")
        self.lbl_quality = self.create_stat_card("LINK QUALITY", "WAITING...")

        stats_panel.addWidget(self.lbl_current)
        stats_panel.addWidget(self.lbl_dist) # Distance added here
        stats_panel.addWidget(self.lbl_avg)
        stats_panel.addWidget(self.lbl_max)
        stats_panel.addWidget(self.lbl_quality)
        stats_panel.addStretch()
        
        # Container for stats to give it a background
        stats_container = QFrame()
        stats_container.setLayout(stats_panel)
        stats_container.setStyleSheet("background-color: #1A1A1A; border-left: 2px solid #333;")
        layout.addWidget(stats_container, stretch=3)

        # Update Timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(100) # 100ms refresh rate
        
        self.update_plot()

    def create_stat_card(self, title, value):
        container = QWidget()
        l = QVBoxLayout(container)
        l.setSpacing(2)
        
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #888; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        
        val_lbl = QLabel(value)
        val_lbl.setObjectName("val") # Tag for easy finding
        val_lbl.setStyleSheet(f"color: {NEON_CYAN}; font-size: 28px; font-family: Monospace; font-weight: bold;")
        
        l.addWidget(title_lbl)
        l.addWidget(val_lbl)
        return container

    def update_label(self, widget, text, color=None):
        lbl = widget.findChild(QLabel, "val")
        lbl.setText(text)
        if color:
            lbl.setStyleSheet(f"color: {color}; font-size: 28px; font-family: Monospace; font-weight: bold;")

    def calculate_distance(self, rssi):
        # --- CALIBRATION ---
        A = -45.0  # Signal strength at 1 meter (Calibrate this!)
        n = 2.5    # Path Loss Exponent (2.0 = Open Air, 3.0 = Indoors)
        
        if rssi == 0: return 0.0
        exponent = (A - rssi) / (10 * n)
        return 10 ** exponent

    def update_plot(self):
        if self.mac not in self.history: return
        data = self.history[self.mac]
        if not data['timestamps']: return

        x_data = data['timestamps']
        y_data = data['rssi']
        
        # --- Stats Calculations ---
        curr_rssi = y_data[-1]
        avg_rssi = np.mean(y_data)
        max_rssi = np.max(y_data)
        
        # --- Distance Calculation ---
        # We use a moving average of the last 5 points to stabilize distance jitter
        recent_avg = np.mean(y_data[-10:]) 
        est_dist = self.calculate_distance(recent_avg)

        # --- Update UI ---
        self.update_label(self.lbl_current, f"{curr_rssi} dBm", NEON_GREEN if curr_rssi > -60 else NEON_CYAN)
        self.update_label(self.lbl_avg, f"{avg_rssi:.1f} dBm")
        self.update_label(self.lbl_max, f"{max_rssi} dBm")
        
        # Update Distance with Color Coding
        dist_color = NEON_RED if est_dist < 2.0 else NEON_GREEN if est_dist < 10.0 else NEON_CYAN
        self.update_label(self.lbl_dist, f"{est_dist:.1f} m", dist_color)
        
        # Update Quality
        q_lbl = self.lbl_quality.findChild(QLabel, "val")
        if curr_rssi > -50:
            q_lbl.setText("EXCELLENT")
            q_lbl.setStyleSheet(f"color: {NEON_GREEN}; font-size: 24px; font-weight: bold;")
        elif curr_rssi > -75:
            q_lbl.setText("STABLE")
            q_lbl.setStyleSheet(f"color: #FFFF00; font-size: 24px; font-weight: bold;")
        else:
            q_lbl.setText("WEAK")
            q_lbl.setStyleSheet(f"color: {NEON_RED}; font-size: 24px; font-weight: bold;")

        # --- Draw Graph ---
        self.ax.clear()
        
        # Context Zones
        self.ax.axhspan(-50, 0, facecolor='#004400', alpha=0.3)   # Excellent
        self.ax.axhspan(-75, -50, facecolor='#444400', alpha=0.3) # Good
        self.ax.axhspan(-100, -75, facecolor='#440000', alpha=0.3) # Poor
        
        # Plot Line
        self.ax.plot(x_data, y_data, color=NEON_CYAN, linewidth=2, marker='o', markersize=3)
        self.ax.fill_between(x_data, y_data, -100, color=NEON_CYAN, alpha=0.1)
        
        # Grid & Limits
        self.ax.set_ylim(-100, -20)
        self.ax.grid(True, color='#333333', linestyle=':')
        
        # Colors
        self.ax.tick_params(axis='x', colors='#888')
        self.ax.tick_params(axis='y', colors='#888')
        for spine in self.ax.spines.values():
            spine.set_edgecolor('#444')
        
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        self.figure.autofmt_xdate()
        self.canvas.draw()

# --- Main Dashboard Window ---
class DashboardWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NORA-W106 SURVEILLANCE SYSTEM")
        self.resize(1100, 750)
        self.setStyleSheet(f"QMainWindow {{ background-color: {DARK_BG}; }}")
        
        # Data Storage
        self.device_history = defaultdict(lambda: {"timestamps": [], "rssi": [], "type": "Unknown"})
        self.active_graphs = [] 

        # Central Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 1. Top Control Bar
        top_bar = QHBoxLayout()
        self.port_selector = QComboBox()
        self.port_selector.setStyleSheet(f"background-color: #222; color: {NEON_GREEN}; padding: 5px; border: 1px solid #444;")
        self.refresh_ports()
        
        self.btn_connect = QPushButton("CONNECT SYSTEM")
        self.btn_connect.clicked.connect(self.toggle_connection)
        self.btn_connect.setStyleSheet(f"background-color: #005500; color: white; padding: 6px; font-weight: bold; border: none;")
        
        top_bar.addWidget(QLabel("INTERFACE PORT:"))
        top_bar.addWidget(self.port_selector)
        top_bar.addWidget(self.btn_connect)
        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        # 2. Main Content (Splitter)
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Device Table
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(4)
        self.device_table.setHorizontalHeaderLabels(["MAC IDENTITY", "DEVICE TYPE", "SIGNAL (RSSI)", "STATUS"])
        self.device_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.device_table.doubleClicked.connect(self.open_graph_window)
        
        # Table Style
        self.device_table.setStyleSheet(f"""
            QTableWidget {{ background-color: #1a1a1a; color: {NEON_GREEN}; gridline-color: #333; font-family: Monospace; }}
            QHeaderView::section {{ background-color: #222; color: white; padding: 5px; border: 1px solid #333; }}
            QTableWidget::item:selected {{ background-color: #330000; color: white; }}
        """)
        splitter.addWidget(self.device_table)

        # Raw Terminal Log
        self.terminal_display = QTextEdit()
        self.terminal_display.setReadOnly(True)
        self.terminal_display.setStyleSheet("background-color: #000; color: #888; font-family: Monospace; font-size: 12px; border-top: 2px solid #333;")
        splitter.addWidget(self.terminal_display)

        main_layout.addWidget(splitter)
        
        # Footer
        self.lbl_status = QLabel("SYSTEM READY. Double-click any device for detailed telemetry.")
        self.lbl_status.setStyleSheet("color: #666; font-size: 11px; margin-top: 5px;")
        main_layout.addWidget(self.lbl_status)

        self.worker = None

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
            
            self.btn_connect.setText("DISCONNECT SYSTEM")
            self.btn_connect.setStyleSheet(f"background-color: {NEON_RED}; color: white; padding: 6px; font-weight: bold;")
            self.lbl_status.setText(f"LINK ESTABLISHED ON {port}")
            self.lbl_status.setStyleSheet(f"color: {NEON_GREEN};")
        else:
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("CONNECT SYSTEM")
            self.btn_connect.setStyleSheet("background-color: #005500; color: white; padding: 6px; font-weight: bold;")
            self.lbl_status.setText("LINK TERMINATED")
            self.lbl_status.setStyleSheet("color: #666;")

    def log_to_terminal(self, text):
        if not text.startswith("{"):
            self.terminal_display.append(text)

    def process_json_data(self, data):
        timestamp = datetime.now()
        devices_list = data.get("devices", [])
        
        self.device_table.setRowCount(len(devices_list))
        
        for row, dev in enumerate(devices_list):
            mac = dev.get("mac", "Unknown")
            rssi = dev.get("rssi", 0)
            dev_type = dev.get("type", "Unknown")
            
            # 1. Store History
            self.device_history[mac]["timestamps"].append(timestamp)
            self.device_history[mac]["rssi"].append(rssi)
            self.device_history[mac]["type"] = dev_type
            
            # Keep only last 500 points
            if len(self.device_history[mac]["timestamps"]) > 500:  # <--- CHANGED from 100
                self.device_history[mac]["timestamps"].pop(0)
                self.device_history[mac]["rssi"].pop(0)

            # 2. Update Table
            self.device_table.setItem(row, 0, QTableWidgetItem(mac))
            self.device_table.setItem(row, 1, QTableWidgetItem(dev_type))
            self.device_table.setItem(row, 2, QTableWidgetItem(str(rssi)))
            
            seen_ms = dev.get('seen_ms', 0)
            status_text = "TRACKING" if seen_ms < 2000 else f"LOST {seen_ms/1000:.1f}s"
            self.device_table.setItem(row, 3, QTableWidgetItem(status_text))

    def open_graph_window(self, item):
        row = item.row()
        mac = self.device_table.item(row, 0).text()
        
        # Focus existing window if open
        for win in self.active_graphs:
            if win.mac == mac and win.isVisible():
                win.raise_()
                return

        # Open new window
        graph_win = GraphWindow(mac, self.device_history)
        graph_win.show()
        self.active_graphs.append(graph_win)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set dark fusion style for standard widgets
    app.setStyle("Fusion")
    
    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())