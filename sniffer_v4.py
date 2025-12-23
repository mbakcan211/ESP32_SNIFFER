import sys
import serial
import serial.tools.list_ports
import json
import time
import numpy as np
from collections import defaultdict
from datetime import datetime
import csv

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QPushButton, QComboBox, 
                             QLabel, QTableWidget, QTableWidgetItem, QHeaderView, 
                             QSplitter, QFrame, QLineEdit, QCompleter)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QTextCursor, QShortcut, QKeySequence

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

# --- Custom Input for Tab Autocomplete ---
class CommandInput(QLineEdit):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab:
            completer = self.completer()
            if completer and completer.popup().isVisible():
                if not completer.popup().currentIndex().isValid():
                    completer.popup().setCurrentIndex(completer.completionModel().index(0, 0))
                
                index = completer.popup().currentIndex()
                if index.isValid():
                    completer.popup().activated.emit(index)
                return
        super().keyPressEvent(event)

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
        
        # Recording Indicator
        self.lbl_rec = QLabel("● REC")
        self.lbl_rec.setStyleSheet(f"color: {NEON_RED}; font-weight: bold; font-size: 14px; margin-right: 10px;")
        self.lbl_rec.setVisible(False)
        top_bar.addWidget(self.lbl_rec)
        
        # Logging Button
        self.btn_log = QPushButton("START LOGGING")
        self.btn_log.setCheckable(True)
        self.btn_log.clicked.connect(self.toggle_logging)
        self.btn_log.setStyleSheet(f"background-color: #333; color: {TEXT_COLOR}; padding: 6px; border: 1px solid #555;")
        top_bar.addWidget(self.btn_log)
        
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

        # Terminal Section (Display + Input)
        term_widget = QWidget()
        term_layout = QVBoxLayout(term_widget)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(0)

        self.terminal_display = QTextEdit()
        self.terminal_display.setReadOnly(True)
        self.terminal_display.setStyleSheet("background-color: #080808; color: #AAA; font-family: Consolas, Monospace; font-size: 14px; border: none; border-top: 1px solid #333; padding: 5px;")
        term_layout.addWidget(self.terminal_display)

        self.cmd_input = CommandInput()
        self.cmd_input.setPlaceholderText("ENTER COMMAND (Type 'help')...")
        self.cmd_input.setStyleSheet(f"background-color: #111; color: {NEON_GREEN}; font-family: Monospace; font-size: 14px; border: none; border-top: 1px solid #333; padding: 4px;")
        self.cmd_input.returnPressed.connect(self.process_command)
        
        # Autocomplete
        self.commands = ["help", "clear", "log start", "log stop", "connect", "disconnect", "quit", "status", "filter", "filter clear", "target", "export", "purge"]
        self.completer = QCompleter(self.commands)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.cmd_input.setCompleter(self.completer)
        
        term_layout.addWidget(self.cmd_input)

        splitter.addWidget(term_widget)

        main_layout.addWidget(splitter)
        
        # Footer
        self.lbl_status = QLabel("SYSTEM READY. Double-click any device for detailed telemetry.")
        self.lbl_status.setStyleSheet("color: #666; font-size: 11px; margin-top: 5px;")
        main_layout.addWidget(self.lbl_status)

        self.worker = None
        self.log_file = None
        self.csv_writer = None
        self.filter_query = "" # Store active filter
        
        # Recording Blink Timer
        self.rec_timer = QTimer()
        self.rec_timer.timeout.connect(self.blink_rec)
        self.rec_blink_state = True

        # Shortcuts
        self.shortcut_quit = QShortcut(QKeySequence("Esc"), self)
        self.shortcut_quit.activated.connect(self.handle_esc)
        self.shortcut_fs = QShortcut(QKeySequence("F11"), self)
        self.shortcut_fs.activated.connect(self.toggle_fullscreen)
        self.shortcut_log = QShortcut(QKeySequence("Ctrl+L"), self)
        self.shortcut_log.activated.connect(self.btn_log.click)

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
            self.log_to_terminal(f"LINK ESTABLISHED: {port}")
        else:
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("CONNECT SYSTEM")
            self.btn_connect.setStyleSheet("background-color: #005500; color: white; padding: 6px; font-weight: bold;")
            self.lbl_status.setText("LINK TERMINATED")
            self.lbl_status.setStyleSheet("color: #666;")
            self.log_to_terminal("LINK TERMINATED")

    def handle_esc(self):
        if self.cmd_input.text():
            self.cmd_input.clear()
        else:
            QApplication.quit()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def blink_rec(self):
        self.rec_blink_state = not self.rec_blink_state
        color = NEON_RED if self.rec_blink_state else "#440000"
        self.lbl_rec.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 14px; margin-right: 10px;")

    def toggle_logging(self):
        if self.btn_log.isChecked():
            filename = f"scan_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            try:
                self.log_file = open(filename, mode='w', newline='', encoding='utf-8')
                self.csv_writer = csv.writer(self.log_file)
                self.csv_writer.writerow(["Timestamp", "MAC", "Type", "RSSI"])
                self.btn_log.setText("STOP LOGGING")
                self.btn_log.setStyleSheet(f"background-color: {NEON_RED}; color: white; padding: 6px; font-weight: bold; border: none;")
                self.log_to_terminal(f"Logging started: {filename}")
                self.lbl_rec.setVisible(True)
                self.rec_timer.start(500)
            except Exception as e:
                self.log_to_terminal(f"[ERROR] Could not start logging: {e}")
                self.btn_log.setChecked(False)
        else:
            if self.log_file:
                self.log_file.close()
                self.log_file = None
                self.csv_writer = None
            self.btn_log.setText("START LOGGING")
            self.btn_log.setStyleSheet(f"background-color: #333; color: {TEXT_COLOR}; padding: 6px; border: 1px solid #555;")
            self.log_to_terminal("Logging stopped.")
            self.rec_timer.stop()
            self.lbl_rec.setVisible(False)

    def process_command(self):
        cmd_text = self.cmd_input.text().strip()
        self.cmd_input.clear()
        if not cmd_text: return

        self.log_to_terminal(f"> {cmd_text}")
        parts = cmd_text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "help":
            self.log_to_terminal("COMMANDS: help, clear, log [start/stop], connect, disconnect, quit, status, filter [text/clear], target <mac>, export, purge")
        elif cmd == "clear":
            self.terminal_display.clear()
        elif cmd == "quit":
            QApplication.quit()
        elif cmd == "connect":
            if self.worker is None: self.toggle_connection()
        elif cmd == "disconnect":
            if self.worker: self.toggle_connection()
        elif cmd == "log":
            if not args:
                self.log_to_terminal("Usage: log start | log stop")
            elif args[0] == "start":
                if not self.btn_log.isChecked():
                    self.btn_log.setChecked(True)
                    self.toggle_logging()
            elif args[0] == "stop":
                if self.btn_log.isChecked():
                    self.btn_log.setChecked(False)
                    self.toggle_logging()
        elif cmd == "status":
            status_msg = f"PORT: {self.port_selector.currentText()} | BAUD: {BAUD_RATE}\n"
            status_msg += f"LOGGING: {'ACTIVE' if self.btn_log.isChecked() else 'OFF'}\n"
            status_msg += f"TRACKED DEVICES: {len(self.device_history)}"
            self.log_to_terminal(status_msg)
        elif cmd == "filter":
            if not args:
                self.log_to_terminal("Usage: filter <text> | filter clear")
            elif args[0] == "clear":
                self.filter_query = ""
                self.log_to_terminal("Filter cleared.")
            else:
                self.filter_query = args[0].lower()
                self.log_to_terminal(f"Filter set to: '{self.filter_query}'")
        elif cmd == "target":
            if not args:
                self.log_to_terminal("Usage: target <mac_address>")
            else:
                mac = args[0]
                if mac in self.device_history:
                    self.open_analysis_window(mac)
                    self.log_to_terminal(f"Targeting {mac}...")
                else:
                    self.log_to_terminal(f"Device {mac} not found in history.")
        elif cmd == "export":
            filename = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            try:
                serializable_history = {}
                for k, v in self.device_history.items():
                    serializable_history[k] = {
                        "timestamps": [t.isoformat() for t in v["timestamps"]],
                        "rssi": v["rssi"],
                        "type": v["type"]
                    }
                with open(filename, 'w') as f:
                    json.dump(serializable_history, f, indent=4)
                self.log_to_terminal(f"Session exported to {filename}")
            except Exception as e:
                self.log_to_terminal(f"[ERROR] Export failed: {e}")
        elif cmd == "purge":
            self.device_history.clear()
            self.device_table.setRowCount(0)
            self.log_to_terminal("CACHE CLEARED. MEMORY FREED.")
        else:
            self.log_to_terminal(f"Unknown command: {cmd}")

    def log_to_terminal(self, text):
        if text.startswith("{"): return

        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "#888888" # Default Gray
        
        if text.startswith(">"): 
            color = NEON_GREEN
            text = text.replace(">", "", 1).strip()
            prefix = "CMD"
        elif "[ERROR]" in text:
            color = NEON_RED
            prefix = "ERR"
        elif "Logging" in text or "LINK" in text:
            color = NEON_CYAN
            prefix = "SYS"
        else:
            prefix = "RX "

        html = f'<span style="color:#444;">[{timestamp}]</span> <span style="color:{color}; font-weight:bold;">{prefix}</span> <span style="color:#DDD;">{text}</span>'
        self.terminal_display.append(html)
        
        # Auto-scroll
        cursor = self.terminal_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.terminal_display.setTextCursor(cursor)

    def process_json_data(self, data):
        timestamp = datetime.now()
        devices_list = data.get("devices", [])
        
        # 1. Update History & Log
        for dev in devices_list:
            mac = dev.get("mac", "Unknown")
            rssi = dev.get("rssi", 0)
            dev_type = dev.get("type", "Unknown")

            self.device_history[mac]["timestamps"].append(timestamp)
            self.device_history[mac]["rssi"].append(rssi)
            self.device_history[mac]["type"] = dev_type
            
            # Keep only last 500 points
            if len(self.device_history[mac]["timestamps"]) > 500:  # <--- CHANGED from 100
                self.device_history[mac]["timestamps"].pop(0)
                self.device_history[mac]["rssi"].pop(0)
            
            # Log to CSV
            if self.csv_writer:
                self.csv_writer.writerow([timestamp.strftime('%Y-%m-%d %H:%M:%S.%f'), mac, dev_type, rssi])

        # 2. Update Table (Show ALL history)
        all_macs = list(self.device_history.keys())
        
        # Sort by last seen timestamp (Descending) so active devices appear at the top
        all_macs.sort(key=lambda m: self.device_history[m]["timestamps"][-1] if self.device_history[m]["timestamps"] else datetime.min, reverse=True)
        
        self.device_table.setRowCount(len(all_macs))
        
        for row, mac in enumerate(all_macs):
            history = self.device_history[mac]
            dev_type = history["type"]
            rssi = history["rssi"][-1] if history["rssi"] else 0
            
            # Calculate Status
            if history["timestamps"]:
                last_seen = history["timestamps"][-1]
                seconds_ago = (timestamp - last_seen).total_seconds()
                status_text = "TRACKING" if seconds_ago < 2.0 else f"LOST {seconds_ago:.1f}s"
            else:
                status_text = "UNKNOWN"

            self.device_table.setItem(row, 0, QTableWidgetItem(mac))
            self.device_table.setItem(row, 1, QTableWidgetItem(dev_type))
            self.device_table.setItem(row, 2, QTableWidgetItem(str(rssi)))
            self.device_table.setItem(row, 3, QTableWidgetItem(status_text))
            
            # Apply Filter
            if self.filter_query:
                match = (self.filter_query in mac.lower()) or (self.filter_query in dev_type.lower())
                self.device_table.setRowHidden(row, not match)
            else:
                self.device_table.setRowHidden(row, False)

    def open_graph_window(self, index):
        row = index.row()
        mac = self.device_table.item(row, 0).text()
        self.open_analysis_window(mac)

    def open_analysis_window(self, mac):
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