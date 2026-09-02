import sys, json, socket, threading, queue, ctypes
from pathlib import Path
from PySide6.QtCore import (Qt, QTimer, QEvent, QPropertyAnimation,
                            QParallelAnimationGroup, QUrl, Signal, QObject, QSizeF)
from PySide6.QtGui import QPixmap, QColor, QPainter
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                               QListWidget, QGraphicsOpacityEffect,
                               QGraphicsView, QGraphicsScene, QFrame)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem


class SpiceAPIWorker(QObject):
    """Asynchronous SpiceAPI client for an unencrypted local connection."""
    status_changed = Signal(bool, str)
    api_error = Signal(str)

    def __init__(self, host, port):
        super().__init__()
        self.host, self.port = host, int(port)
        self.jobs, self.sock, self.request_id = queue.Queue(), None, 0
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, module, function, params=None):
        if self.running:
            self.jobs.put((module, function, params or []))

    def keypad_set(self, keypad, keys=""):
        self.submit("keypads", "set", [int(keypad), *list(keys)])

    def button_write(self, name, pressed=True):
        self.submit("buttons", "write", [[name, 1.0 if pressed else 0.0]])

    def button_reset(self, name):
        # Current Spice2x expects each reset name wrapped in its own array.
        self.submit("buttons", "write_reset", [[name]])

    def button_release(self, name):
        # Explicitly release first, then remove the override. If reset ever
        # fails, the remaining override is still safely in the released state.
        self.button_write(name, False)
        self.button_reset(name)

    def coin_insert(self):
        self.submit("coin", "insert")

    def _connect(self):
        self._close_socket()
        self.sock = socket.create_connection((self.host, self.port), timeout=1.5)
        self.sock.settimeout(2.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.status_changed.emit(True, f"SpiceAPI connected: {self.host}:{self.port}")

    def _request(self, module, function, params):
        if self.sock is None:
            self._connect()
        self.request_id = (self.request_id + 1) % (2 ** 64)
        body = {"id": self.request_id, "module": module,
                "function": function, "params": params}
        self.sock.sendall(json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\0")
        response = bytearray()
        while not response or response[-1] != 0:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("SpiceAPI closed the connection")
            response.extend(chunk)
        decoded = json.loads(response[:-1].decode("utf-8"))
        if decoded.get("id") != self.request_id:
            raise RuntimeError("SpiceAPI response ID mismatch")
        if decoded.get("errors"):
            raise RuntimeError("; ".join(map(str, decoded["errors"])))

    def _run(self):
        while self.running:
            job = self.jobs.get()
            if job is None:
                break
            try:
                self._request(*job)
            except Exception as exc:
                self._close_socket()
                self.status_changed.emit(False, "SpiceAPI disconnected")
                self.api_error.emit(str(exc))

    def _close_socket(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def close(self):
        self.running = False
        self.jobs.put(None)
        self._close_socket()


class VideoCanvas(QGraphicsView):
    """GPU-backed scene video surface that remains below normal Qt controls."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_scene = QGraphicsScene(self)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.video_scene.addItem(self.video_item)
        self.setScene(self.video_scene)
        self.setSceneRect(0, 0, self.width(), self.height())
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("background:black;border:0;")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.viewport().setAttribute(Qt.WA_TransparentForMouseEvents)

    def clear_frame(self):
        self.video_scene.invalidate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        size = QSizeF(self.viewport().size())
        self.video_item.setSize(size)
        self.setSceneRect(0, 0, size.width(), size.height())


class BackgroundSelector(QWidget):
    """Dropdown-style selector that stays inside the no-activate main window."""
    changed = Signal(int)

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.names = names
        self.setFixedSize(500, 80)

        self.button = QPushButton(names[0] if names else "No backgrounds found", self)
        self.button.setGeometry(0, 0, 500, 80)
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.clicked.connect(self.toggle_list)

        self.list_widget = QListWidget(self)
        self.list_widget.setGeometry(0, 80, 500, 420)
        self.list_widget.addItems(names)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.itemClicked.connect(self.select_item)
        self.list_widget.hide()

        self.setStyleSheet("""
        QPushButton, QListWidget {
            background-color: rgba(0,0,0,225);
            color: white;
            border: 3px solid white;
            border-radius: 10px;
            padding: 10px 15px;
            font-size: 28px;
            font-weight: bold;
            text-align: left;
        }
        QListWidget::item { min-height: 58px; padding-left: 8px; }
        QListWidget::item:selected { background-color: rgba(255,0,0,180); }
        """)

    def toggle_list(self):
        opening = not self.list_widget.isVisible()
        self.setFixedHeight(500 if opening else 80)
        self.list_widget.setVisible(opening)
        if opening:
            self.raise_()

    def select_item(self, item):
        index = self.list_widget.row(item)
        self.button.setText(item.text())
        self.list_widget.hide()
        self.setFixedHeight(80)
        self.changed.emit(index)

class Btn(QLabel):
    def __init__(self, main_win, parent_widget, cfg, sx, sy, overlay_img_name="pressed.png", asset_dir="ui"):
        super().__init__(parent_widget)
        self.main_win = main_win
        self.cfg = cfg
        self.overlay = False
        self.overlay_pixmap = None
        
        img = Path(asset_dir) / cfg["image"]
        if img.exists():
            pm = QPixmap(str(img))
            self.setPixmap(pm)
            self.setScaledContents(True)
            self.resize(max(1, int(pm.width() * sx)), max(1, int(pm.height() * sy)))
        else:
            self.setText(cfg.get("controller_button", "BTN"))
            self.setStyleSheet("background:#333;color:white;border:1px solid white;")
            self.resize(100, 100)
            
        self.move(int(cfg["x"] * sx), int(cfg["y"] * sy))

        overlay_path = Path(asset_dir) / overlay_img_name
        if overlay_path.exists():
            self.overlay_pixmap = QPixmap(str(overlay_path)).scaled(
                self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )

    def mousePressEvent(self, e):
        self.overlay = True
        self.update()
        if "action" in self.cfg:
            self.main_win.trigger_action(self.cfg["action"])
        QTimer.singleShot(100, self.release_vis)

    def release_vis(self):
        self.overlay = False
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.overlay:
            qp = QPainter(self)
            if self.overlay_pixmap:
                qp.drawPixmap(0, 0, self.overlay_pixmap)
            else:
                qp.fillRect(self.rect(), QColor(255, 0, 0, 76))

class Win(QWidget):
    def __init__(self):
        super().__init__()
        
        try:
            self.config = json.loads(Path("config.json").read_text(encoding="utf8"))
        except Exception:
            self.config = {}

        screens = QApplication.screens()
        idx = max(0, self.config.get("monitor", 1) - 1)
        if idx >= len(screens): idx = 0
        geo = screens[idx].geometry()
        self.setGeometry(geo)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        # 1. Main background label
        self.bg = QLabel(self)
        self.bg.setGeometry(self.rect())
        self.video = VideoCanvas(self)
        self.video.setGeometry(self.rect())
        self.video.hide()
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setMuted(True)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video.video_item)
        self.media_player.mediaStatusChanged.connect(self._video_status_changed)

        # 2. Dim Overlay for Concentration Mode
        self.dim_overlay = QWidget(self)
        self.dim_overlay.setGeometry(self.rect())
        self.dim_overlay.setStyleSheet("background-color: black;")
        self.dim_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.dim_opacity_effect = QGraphicsOpacityEffect()
        self.dim_opacity_effect.setOpacity(0.0)
        self.dim_overlay.setGraphicsEffect(self.dim_opacity_effect)

        # 3. Concentration Icon
        self.conc_icon = QLabel(self)
        conc_cfg = self.config.get("concentration_mode", {})
        rw = self.config.get("reference_width", 1920)
        rh = self.config.get("reference_height", 1080)
        sx = self.width() / rw
        sy = self.height() / rh

        img_path = Path("ui") / conc_cfg.get("image", "concentration.png")
        if img_path.exists():
            pm = QPixmap(str(img_path))
            self.conc_icon.setPixmap(pm)
            self.conc_icon.setScaledContents(True)
            self.conc_icon.resize(int(pm.width() * sx), int(pm.height() * sy))
        else:
            self.conc_icon.setText("CONCENTRATION MODE")
            self.conc_icon.setStyleSheet("color:white; font-size:32px;")
            self.conc_icon.adjustSize()
            
        cx = int(conc_cfg.get("x", 800) * sx)
        cy = int(conc_cfg.get("y", 400) * sy)
        self.conc_icon.move(cx, cy)
        self.conc_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.conc_icon_opacity = QGraphicsOpacityEffect()
        self.conc_icon_opacity.setOpacity(0.0)
        self.conc_icon.setGraphicsEffect(self.conc_icon_opacity)

        # 4. UI Container (Holds all buttons and dropdowns so they can fade out together)
        self.ui_container = QWidget(self)
        self.ui_container.setGeometry(self.rect())
        self.ui_opacity_effect = QGraphicsOpacityEffect()
        self.ui_opacity_effect.setOpacity(1.0)
        self.ui_container.setGraphicsEffect(self.ui_opacity_effect)

        self.backgrounds = []
        self.background_names = []

        bg_dir = Path(self.config.get("background_directory", "backgrounds"))
        if not bg_dir.exists():
            bg_dir = Path("backgrounds")

        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4", "*.webm"):
            for f in sorted(bg_dir.glob(ext)):
                self.backgrounds.append(f)
                self.background_names.append(f.stem)

        self.bg_index = 0
        self.load_background()

        # In-window selector: unlike QComboBox, this never creates an activating popup.
        self.bg_dropdown = BackgroundSelector(self.background_names, self.ui_container)
        self.bg_dropdown.move(20, 20)
        self.bg_dropdown.changed.connect(self.change_background)

        api_cfg = self.config.get("spiceapi", {})
        self.log_cfg = self.config.get("logging", {})
        self.spice = SpiceAPIWorker(api_cfg.get("host", "127.0.0.1"),
                                    api_cfg.get("port", 1337))
        self.spice.status_changed.connect(self.update_api_status)
        self.spice.api_error.connect(self.report_api_error)

        self.api_status = QLabel("SpiceAPI: waiting for first button press", self.ui_container)
        self.api_status.setStyleSheet(
            "background:rgba(0,0,0,180);color:#ffcc66;padding:8px;font-size:18px;")
        self.api_status.adjustSize()
        self.api_status.move(20, 110)
        self.api_status.setVisible(self.log_cfg.get("show_status_overlay", True))

        overlay_img = self.config.get("pressed_overlay_image", "pressed.png")

        # Load buttons into the ui_container
        for b in self.config.get("main_buttons", []):
            Btn(self, self.ui_container, b, sx, sy, overlay_img).show()

        self.extra_buttons = []
        for b in self.config.get("extra_buttons", []):
            btn = Btn(self, self.ui_container, b, sx, sy, overlay_img)
            btn.show()
            self.extra_buttons.append(btn)

        self.extras_visible = not self.config.get("extras_hidden_on_startup", True)
        self.update_extras()

        default_toggle = {"image": "system_toggle.png", "x": 1650, "y": 100}
        toggle_cfg = self.config.get("toggle_button", default_toggle)
        self.toggle_btn = Btn(self, self.ui_container, toggle_cfg, sx, sy, overlay_img)
        self.toggle_btn.mousePressEvent = lambda e: self.toggle_extras()
        self.toggle_btn.show()

        # Concentration Mode Animation Setup
        self.in_concentration_mode = False
        self.anim_group = QParallelAnimationGroup()
        
        self.anim_ui = QPropertyAnimation(self.ui_opacity_effect, b"opacity")
        self.anim_dim = QPropertyAnimation(self.dim_opacity_effect, b"opacity")
        self.anim_icon = QPropertyAnimation(self.conc_icon_opacity, b"opacity")
        
        self.anim_ui.setDuration(1000)
        self.anim_dim.setDuration(1000)
        self.anim_icon.setDuration(1000)
        
        self.anim_group.addAnimation(self.anim_ui)
        self.anim_group.addAnimation(self.anim_dim)
        self.anim_group.addAnimation(self.anim_icon)

        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.enter_concentration_mode)
        
        # Install global event filter to catch all touches
        QApplication.instance().installEventFilter(self)

        self.showFullScreen()
        self.apply_no_activate_style()
        self.reset_idle_timer()

    def apply_no_activate_style(self):
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000)  # WS_EX_NOACTIVATE

    def update_api_status(self, connected, message):
        color = "#73ff8a" if connected else "#ffcc66"
        self.api_status.setText(message)
        self.api_status.setStyleSheet(
            f"background:rgba(0,0,0,180);color:{color};padding:8px;font-size:18px;")
        self.api_status.adjustSize()

    def report_api_error(self, message):
        if self.log_cfg.get("print_api_errors", True):
            print(f"SpiceAPI: {message}", file=sys.stderr)

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32":
            from ctypes import wintypes
            class MSG(ctypes.Structure):
                _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                            ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                            ("time", wintypes.DWORD), ("pt", wintypes.POINT)]
            msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
            if msg.message == 0x0021:  # WM_MOUSEACTIVATE
                return True, 3  # MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    def eventFilter(self, watched, event):
        t = event.type()
        # Reset timer on any mouse move, click, or touch
        if t in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.TouchBegin, QEvent.TouchUpdate):
            self.reset_idle_timer()
            
            # Wake up if asleep
            if self.in_concentration_mode:
                self.exit_concentration_mode()
                # Consume the initial click so it doesn't accidentally trigger a button underneath
                if t in (QEvent.MouseButtonPress, QEvent.TouchBegin):
                    return True
                    
        return super().eventFilter(watched, event)

    def reset_idle_timer(self):
        conc_cfg = self.config.get("concentration_mode", {})
        if not conc_cfg.get("enabled", True):
            return
            
        timeout_ms = int(conc_cfg.get("timeout_seconds", 60) * 1000)
        self.idle_timer.start(timeout_ms)

    def enter_concentration_mode(self):
        if self.in_concentration_mode: return
        self.in_concentration_mode = True
        
        self.anim_group.stop()
        self.anim_ui.setEndValue(0.0)
        self.anim_dim.setEndValue(self.config.get("concentration_mode", {}).get("dim_opacity", 0.5))
        self.anim_icon.setEndValue(1.0)
        self.anim_group.start()

    def exit_concentration_mode(self):
        if not self.in_concentration_mode: return
        self.in_concentration_mode = False
        
        self.anim_group.stop()
        self.anim_ui.setEndValue(1.0)
        self.anim_dim.setEndValue(0.0)
        self.anim_icon.setEndValue(0.0)
        self.anim_group.start()

    def update_extras(self):
        for b in self.extra_buttons:
            b.setVisible(self.extras_visible)

    def toggle_extras(self):
        self.extras_visible = not self.extras_visible
        self.update_extras()

    def change_background(self, index):
        self.bg_index = index
        self.load_background()

    def load_background(self):
        self.media_player.stop()
        self.video.clear_frame()
        self.video.hide()
        self.bg.show()

        if not self.backgrounds:
            self.bg.setStyleSheet("background:black;")
            return

        file = self.backgrounds[self.bg_index]
        ext = file.suffix.lower()

        if ext in (".mp4", ".webm"):
            self.bg.hide()
            self.video.show()
            self.video.lower()
            self.media_player.setSource(QUrl.fromLocalFile(str(file.resolve())))
            self.media_player.play()
        else:
            self.bg.setPixmap(QPixmap(str(file)).scaled(
                self.size(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation
            ))

    def _video_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.media_player.setPosition(0)
            self.media_player.play()

    def trigger_action(self, action):
        action_type = action.get("type", "")
        if action_type == "keypad":
            keypad, key = int(action.get("keypad", 0)), str(action.get("key", ""))
            self.spice.keypad_set(keypad, key)
            QTimer.singleShot(75, lambda: self.spice.keypad_set(keypad, ""))
        elif action_type == "button":
            name = str(action.get("name", ""))
            if name:
                self.spice.button_write(name, True)
                QTimer.singleShot(75, lambda: self.spice.button_release(name))
        elif action_type == "coin":
            self.spice.coin_insert()

    def closeEvent(self, event):
        self.media_player.stop()
        self.spice.close()
        super().closeEvent(event)

app = QApplication(sys.argv)
w = Win()
sys.exit(app.exec())
