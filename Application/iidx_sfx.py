"""Native-artwork IIDX sound controls. SpiceAPI values are normalized 0..1."""
from pathlib import Path
from app_paths import asset_path
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QWidget, QLabel

CONTROLS = (
    ("VEFX", "sfx_vefx.png", "sfx-frame1.png"),
    ("Low-EQ", "sfx_loweq.png", "sfx-frame1.png"),
    ("Hi-EQ", "sfx_hieq.png", "sfx-frame1.png"),
    ("Filter", "sfx_filter.png", "sfx-frame2.png"),
    ("Play Volume", "sfx_vol.png", "sfx-frame2.png"),
)


class SoundSlider(QWidget):
    changed = Signal(float)

    def __init__(self, name, label, frame, cfg, parent):
        super().__init__(parent)
        self.scale = 1.5
        self.setFixedSize(228, 642)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.NoFocus)
        self.images = [QPixmap(str(asset_path(n))) for n in
                       ("sfx-bg.png", frame, label, "slider_ticker.png")]
        if any(pm.isNull() for pm in self.images):
            raise ValueError(f"Missing IIDX sound artwork for {name}")
        self.start_y = float(cfg.get("start_y", 301))
        self.end_y = float(cfg.get("end_y", 21))
        if not 0 <= self.end_y < self.start_y <= 322:
            raise ValueError("iidx_sfx endpoints must satisfy 0 <= end_y < start_y <= 322")
        self.value = 0.5
        self.known = False
        self.dragging = False

    def set_value(self, value):
        value = max(0., min(1., float(value)))
        if self.known and self.value == value:
            return
        self.value = value
        self.known = True
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(self.scale, self.scale)
        # Background and frame share a center; labels sit above the slider.
        p.drawPixmap(10, 52, self.images[0])
        p.drawPixmap(0, 79, self.images[1])
        p.drawPixmap(0, 0, self.images[2])
        if self.known:
            cy = 79 + self.start_y + self.value * (self.end_y - self.start_y)
            p.drawPixmap(28, round(cy - 19), self.images[3])

    def move_handle(self, y):
        self.set_value((y / self.scale - 79 - self.start_y) / (self.end_y - self.start_y))
        self.changed.emit(self.value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() >= 52 * self.scale:
            self.dragging = True
            self.move_handle(event.position().y())
            event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.move_handle(event.position().y())
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.dragging and event.button() == Qt.LeftButton:
            self.move_handle(event.position().y())
            self.dragging = False
            event.accept()


class SoundEffectsPage(QWidget):
    def __init__(self, spice, cfg, parent):
        super().__init__(parent)
        self.spice = spice
        self.gap = max(0, int(cfg.get("gap", 0)))
        self.sliders = {}
        self.touched = set()
        self.read_pending = False
        self.status = QLabel("Connecting to SpiceAPI…", self)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("color:white;background:transparent;font-size:18px;")
        for name, label, frame in CONTROLS:
            slider = SoundSlider(name, label, frame, cfg, self)
            slider.setEnabled(False)
            slider.changed.connect(lambda value, n=name: self.change(n, value))
            self.sliders[name] = slider
        spice.analogs_read.connect(self.received)
        spice.api_error.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def resizeEvent(self, event):
        width = 5 * 228 + 4 * self.gap
        left = (self.width() - width) // 2
        top = 349
        for i, slider in enumerate(self.sliders.values()):
            slider.move(left + i * (228 + self.gap), top)
        self.status.setGeometry(0, top + 622, self.width(), 24)

    def poll(self):
        if self.isVisible() and not getattr(self.window(), "in_concentration_mode", False) and not self.read_pending:
            self.read_pending = True
            self.spice.submit("analogs", "read")

    def change(self, name, value):
        self.touched.add(name)
        self.spice.analog_write(name, value)

    def received(self, data):
        self.read_pending = False
        values = {row[0]: row[1] for row in data
                  if isinstance(row, list) and len(row) >= 2
                  and isinstance(row[1], (int, float))}
        missing = []
        for name, slider in self.sliders.items():
            available = name in values
            slider.setEnabled(available)
            if not available:
                missing.append(name)
            elif name not in self.touched:
                slider.set_value(values[name])
        self.status.setText("Unavailable analogs: " + ", ".join(missing) if missing else "")

    def failed(self, message):
        self.read_pending = False
        self.touched.clear()
        for slider in self.sliders.values():
            slider.setEnabled(False)
        self.status.setText("SpiceAPI: " + str(message)[:160])
