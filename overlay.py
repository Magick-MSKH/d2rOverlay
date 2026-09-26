import sys
import cv2
import pytesseract
import mss
import numpy as np
import csv
import os
import win32gui
import win32con
from difflib import get_close_matches
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QFont

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

REL_LEFT = 1620
REL_TOP = 90
REL_WIDTH = 300
REL_HEIGHT = 25

class AreaDatabase:
    def __init__(self, filename="d2UniquesDensity.csv"):
        self.db = {}
        self.area_names = []
        self.load_csv(filename)

    def load_csv(self, filename):
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0] if __name__ == "__main__" else __file__))
        file_path = os.path.join(script_dir, filename)
        if not os.path.exists(file_path):
            return
        try:
            with open(file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row["Area Name"].strip()
                    self.db[name] = row
                    self.area_names.append(name)
        except Exception:
            pass

    def lookup_zone(self, ocr_text):
        if not ocr_text or not self.db:
            return None
        cleaned_ocr = ocr_text.upper().strip()
        
        replacements = {"CSL_B": "COLD", "CSL_b": "COLD", "CO1D": "COLD", "ST0NY": "STONY", "M00": "MOO", "1": "I", "0": "O", "_": ""}
        for error, correction in replacements.items():
            cleaned_ocr = cleaned_ocr.replace(error, correction)
        cleaned_ocr = "".join([c for c in cleaned_ocr if c.isalnum() or c.isspace()])
        
        if any(town in cleaned_ocr for town in ["HARR", "ROGUE", "LUT GHOLEIN", "KURAST DOCK", "PANDEMONIUM"]):
            return None

        for area_name in self.area_names:
            if cleaned_ocr == area_name.upper():
                return self.db[area_name]

        upper_names = [name.upper() for name in self.area_names]
        matches = get_close_matches(cleaned_ocr, upper_names, n=1, cutoff=0.70)
        if matches:
            return self.db[self.area_names[upper_names.index(matches[0])]]
        return None

class ScannerWorker(QThread):
    area_detected = pyqtSignal(str)

    def get_d2r_window_box(self):
        try:
            hwnd = win32gui.FindWindow(None, "Diablo II: Resurrected")
            if hwnd == 0 or win32gui.IsIconic(hwnd):
                return None
            rect = win32gui.GetWindowRect(hwnd)
            return {"top": rect[1] + REL_TOP, "left": rect[0] + REL_LEFT, "width": REL_WIDTH, "height": REL_HEIGHT}
        except Exception:
            return None

    def preprocess_image(self, frame):
        cv2.imwrite("debug_crop.png", frame)
        # --------------------------------

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(resized, 160, 255, cv2.THRESH_BINARY)
        return thresh

    def run(self):
        with mss.MSS() as sct:
            while True:
                box = self.get_d2r_window_box()
                if box is None:
                    self.area_detected.emit("Game Closed/Minimized")
                    self.msleep(2000)
                    continue
                screenshot = sct.grab(box)
                frame = np.array(screenshot)
                processed_frame = self.preprocess_image(frame)
                text = pytesseract.image_to_string(processed_frame, config='--psm 7')
                clean_text = text.strip()
                if clean_text:
                    self.area_detected.emit(clean_text)
                self.msleep(1500)

class D2ROverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.database = AreaDatabase()
        self.current_difficulty = "Hell"
        self.last_detected_text = ""
        
        self.position_index = 0 
        
        self.init_ui()
        self.start_scanner_thread()

        self.track_timer = pyqtSignal()
        from PyQt6.QtCore import QTimer
        self.timer = QTimer()
        self.timer.timeout.connect(self.snap_to_game_position)
        self.timer.start(100)

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.SubWindow
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        hwnd = int(self.winId())
        extended_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, extended_style | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED)

        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(2)

        # Build HUD Data Display Tiers
        self.label_area = QLabel("Area: Awaiting Game...")
        self.label_alvl = QLabel("Area Level: --")
        self.label_density = QLabel("Density Value: --")
        self.label_uniques = QLabel("Uniques Range: --")
        self.label_super = QLabel("Superuniques: --")
        self.label_monsters = QLabel("Monster Types: --")

        self.label_super.setWordWrap(True)
        self.label_monsters.setWordWrap(True)

        self.layout.addWidget(self.label_area)
        self.layout.addWidget(self.label_alvl)
        self.layout.addWidget(self.label_density)
        self.layout.addWidget(self.label_uniques)
        self.layout.addWidget(self.label_super)
        self.layout.addWidget(self.label_monsters)
        
        self.setLayout(self.layout)
        self.apply_style_profile()

    def closeEvent(self, event):
        print("Shutting down overlay smoothly...")
        
        self.hide()
        
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
            
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

        event.accept()

        QApplication.quit()

    def apply_style_profile(self):
        if self.position_index in [0, 1]:
            align_flag = Qt.AlignmentFlag.AlignRight
            text_align_css = "text-align: right;"
        else: # Green sits on the Left edge
            align_flag = Qt.AlignmentFlag.AlignLeft
            text_align_css = "text-align: left;"

        for i in range(self.layout.count()):
            widget = self.layout.itemAt(i).widget()
            if isinstance(widget, QLabel):
                widget.setAlignment(align_flag)

        if self.position_index == 0:
            font_color = "#ffcc00"
            headline_color = "#ffffff"
        elif self.position_index == 1:
            font_color = "#3399ff"
            headline_color = "#66b2ff"
        else:
            font_color = "#00ff66"
            headline_color = "#66ff99"

        self.setStyleSheet(f"""
            QLabel {{
                color: {font_color};
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                font-weight: 500;
                background-color: transparent;
                {text_align_css}
                qproperty-text: ""; 
            }}
        """)
        self.label_area.setStyleSheet(f"color: {headline_color}; font-size: 16px; font-weight: bold; {text_align_css}")
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self.cycle_hud_position()

    def cycle_hud_position(self):
        self.position_index = (self.position_index + 1) % 3
        self.apply_style_profile()
        self.snap_to_game_position()
        print(f"HUD Position shifted to profile slot: {self.position_index}")

    def snap_to_game_position(self):
        try:
            hwnd = win32gui.FindWindow(None, "Diablo II: Resurrected")
            if hwnd == 0 or win32gui.IsIconic(hwnd):
                return
            
            rect = win32gui.GetWindowRect(hwnd)
            win_left, win_top, win_right, win_bottom = rect
            win_width = win_right - win_left
            win_height = win_bottom - win_top

            if self.position_index == 0:
                box_width = 380
                box_height = 180
                x = win_left + REL_LEFT - box_width + 35
                y = win_top + REL_TOP + 35
            elif self.position_index == 1:
                box_width = 350
                box_height = 180
                x = win_right - box_width - 30
                y = win_bottom - box_height - 100
            else:
                box_width = 350
                box_height = 180
                x = win_left + 30
                y = win_bottom - box_height - 100

            self.setFixedSize(box_width, box_height)
            self.move(QPoint(x, y))
        except Exception:
            pass

    def start_scanner_thread(self):
        self.worker = ScannerWorker()
        self.worker.area_detected.connect(self.update_display)
        self.worker.start()

    def update_display(self, detected_text):
        self.last_detected_text = detected_text
        
        if detected_text in ["Game Closed/Minimized"]:
            self.clear_all_hud_values(detected_text)
            return

        zone_data = self.database.lookup_zone(detected_text)
        diff = self.current_difficulty
        
        if zone_data:
            self.label_area.setText(f"Area: {zone_data['Area Name']}")
            self.label_alvl.setText(f"Area Level: {zone_data[f'{diff} alvl']}")
            self.label_density.setText(f"Density Value: {zone_data[f'Density ({'Nightmare' if diff == 'NM' else 'Hell'})']}")
            self.label_uniques.setText(f"Uniques Range: {zone_data[f'{diff} Min Uniques']} - {zone_data[f'{diff} Max Uniques']}")
            
            su = zone_data['Superunique'].strip()
            self.label_super.setText(f"Superuniques: {su if su else 'None'}")
            
            monsters = zone_data[f'Monsters ({'Nightmare' if diff == 'NM' else 'Hell'})'].strip()
            self.label_monsters.setText(f"Monster Types: {monsters if monsters else 'Unknown'}")
        else:
            self.clear_all_hud_values(f"Area: {detected_text} (Not in DB)")

    def clear_all_hud_values(self, area_header_text):
        self.label_area.setText(area_header_text)
        self.label_alvl.setText("Area Level: --")
        self.label_density.setText("Density Value: --")
        self.label_uniques.setText("Uniques Range: --")
        self.label_super.setText("Superuniques: --")
        self.label_monsters.setText("Monster Types: --")


def setup_global_hotkey(window_instance, app_instance):
    import threading
    import keyboard
    import time
    
    def hotkey_listener():
        try:
            keyboard.unhook_all()
        except Exception:
            pass
            
        print("Initializing Low-Level Windows Hotkey Hooks...")
        
        keyboard.add_hotkey('f11', lambda: window_instance.cycle_hud_position())
        
        keyboard.add_hotkey('f12', lambda: window_instance.close())
        
        while True:
            time.sleep(1)
            
    th = threading.Thread(target=hotkey_listener, daemon=True)
    th.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = D2ROverlayWindow()
    overlay.show()
    
    try:
        import keyboard
        setup_global_hotkey(overlay, app)
    except ImportError:
        print("Tip: Run 'pip install keyboard' to enable global hotkey options!")
        
    sys.exit(app.exec())