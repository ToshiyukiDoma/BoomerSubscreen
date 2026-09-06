import sys, json, socket, threading, queue, ctypes
from pathlib import Path
from app_paths import APP_ROOT, resource_path, asset_path
from PySide6.QtCore import (Qt, QTimer, QEvent, QPropertyAnimation,
                            QParallelAnimationGroup, QUrl, Signal, QObject, Property,
                            QSizeF, QSize, QPoint, QRectF, QEasingCurve)
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen, QIcon, QTransform
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                               QListWidget, QGraphicsOpacityEffect,
                               QGraphicsView, QGraphicsScene, QFrame,
                               QScrollArea, QGridLayout, QScroller)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem


from iidx_sfx import SoundEffectsPage
from iidx_ticker import IIDXTicker


class SpiceAPIWorker(QObject):
    """Asynchronous SpiceAPI client for an unencrypted local connection."""
    status_changed = Signal(bool, str)
    api_error = Signal(str)
    analogs_read = Signal(object)
    ticker_read = Signal(object)

    def __init__(self, host, port):
        super().__init__()
        self.host, self.port = host, int(port)
        self.jobs, self.sock, self.request_id = queue.Queue(), None, 0
        self.pending_analogs = {}
        self.analog_lock = threading.Lock()
        self.owned_analogs = set()
        self.ticker_pending = False
        self.heartbeat_pending = False
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, module, function, params=None):
        if self.running:
            self.jobs.put((module, function, params or []))

    def ticker_get(self):
        if not self.ticker_pending:
            self.ticker_pending = True
            self.submit("iidx", "ticker_get")

    def heartbeat(self):
        if not self.heartbeat_pending:
            self.heartbeat_pending = True
            self.submit("info", "launcher")

    def analog_write(self, name, value):
        with self.analog_lock:
            first = name not in self.pending_analogs
            self.pending_analogs[name] = max(0.0, min(1.0, float(value)))
        if first:
            self.submit("analog_pending", name)

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

        self.status_changed.emit(True, "SpiceAPI connected")
        return decoded.get("data", [])

    def _run(self):
        while self.running:
            job = self.jobs.get()
            if job is None:
                break
            try:
                module, function, params = job
                if module == "analog_pending":
                    with self.analog_lock:
                        value = self.pending_analogs.pop(function)
                    self.owned_analogs.add(function)
                    self._request("analogs", "write", [[function, value]])
                else:
                    data = self._request(*job)
                    if module == "analogs" and function == "read":
                        self.analogs_read.emit(data)
                    if module == "iidx" and function == "ticker_get":
                        self.ticker_read.emit(data)
            except Exception as exc:
                if job[:2] == ("iidx", "ticker_get"):
                    self.ticker_read.emit([" " * 9])
                self._close_socket()
                self.status_changed.emit(False, "SpiceAPI disconnected")
                self.api_error.emit(str(exc))
            finally:
                if job[:2] == ("iidx", "ticker_get"):
                    self.ticker_pending = False
                if job[:2] == ("info", "launcher"):
                    self.heartbeat_pending = False

        # Only release this application's sound-control overrides on normal exit.
        if self.sock is not None and self.owned_analogs:
            try:
                self._request("analogs", "write_reset", [[n] for n in self.owned_analogs])
            except Exception:
                pass
        self._close_socket()

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
        self.thread.join(timeout=4.5)


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
        self.video_item.videoSink().setVideoFrame(QVideoFrame())
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


class AssetSlider(QWidget):
    """A skinnable slider whose travel is defined by start/end coordinates."""
    valueChanged = Signal(float)

    def __init__(self, cfg, value, reference_size, parent=None):
        super().__init__(parent)
        rw, rh = reference_size
        pw, ph = parent.width(), parent.height()
        self.sx, self.sy = pw / rw, ph / rh
        self.cfg = cfg
        self.minimum = float(cfg.get("minimum", 0))
        self.maximum = float(cfg.get("maximum", 100))
        self.value = max(self.minimum, min(self.maximum, float(value)))
        asset_scale_x = 1.0 if cfg.get("native_asset_size", False) else self.sx
        asset_scale_y = 1.0 if cfg.get("native_asset_size", False) else self.sy
        self.handle_size = QSize(
            max(12, int(cfg.get("handle_width", 54) * asset_scale_x)),
            max(12, int(cfg.get("handle_height", 54) * asset_scale_y)))
        self.track = self._load_asset(cfg.get("track_image", ""))
        self.handle = self._load_asset(cfg.get("handle_image", ""))

        x1 = float(cfg.get("start_x", 650)) * self.sx
        y1 = float(cfg.get("start_y", 400)) * self.sy
        x2 = float(cfg.get("end_x", 1300)) * self.sx
        y2 = float(cfg.get("end_y", 400)) * self.sy
        natural_w = self.track.width()*asset_scale_x if not self.track.isNull() else abs(x2-x1)+30
        natural_h = self.track.height()*asset_scale_y if not self.track.isNull() else abs(y2-y1)+30
        track_w = max(20, float(cfg.get("track_width", natural_w/asset_scale_x))*asset_scale_x)
        track_h = max(20, float(cfg.get("track_height", natural_h/asset_scale_y))*asset_scale_y)
        midpoint_x, midpoint_y = (x1+x2)/2, (y1+y2)/2
        track_x = float(cfg.get("track_x", (midpoint_x-track_w/2)/self.sx))*self.sx
        track_y = float(cfg.get("track_y", (midpoint_y-track_h/2)/self.sy))*self.sy
        if "start_offset_x" in cfg:
            x1 = track_x + float(cfg["start_offset_x"])
            y1 = track_y + float(cfg["start_offset_y"])
            x2 = track_x + float(cfg["end_offset_x"])
            y2 = track_y + float(cfg["end_offset_y"])
        pad = max(self.handle_size.width(), self.handle_size.height()) + 18
        left = min(min(x1, x2)-pad, track_x)
        top = min(min(y1, y2)-pad, track_y)
        right = max(max(x1, x2)+pad, track_x+track_w)
        bottom = max(max(y1, y2)+pad, track_y+track_h)
        self.setGeometry(int(left), int(top), int(right-left), int(bottom-top))
        self.start = QPoint(int(x1-left), int(y1-top))
        self.end = QPoint(int(x2-left), int(y2-top))
        self.track_rect = QRectF(track_x-left, track_y-top, track_w, track_h)
        self.setFocusPolicy(Qt.NoFocus)

    @staticmethod
    def _load_asset(name):
        path = asset_path() / name if name else None
        return QPixmap(str(path)) if path and path.exists() else QPixmap()

    def ratio(self):
        span = self.maximum - self.minimum
        return 0.0 if span <= 0 else (self.value - self.minimum) / span

    def setValue(self, value, emit=True):
        new_value = max(self.minimum, min(self.maximum, float(value)))
        if new_value != self.value:
            self.value = new_value
            self.update()
            if emit:
                self.valueChanged.emit(self.value)

    def _set_from_position(self, pos):
        dx, dy = self.end.x()-self.start.x(), self.end.y()-self.start.y()
        length_sq = dx*dx + dy*dy
        if length_sq <= 0:
            return
        px, py = pos.x()-self.start.x(), pos.y()-self.start.y()
        ratio = max(0.0, min(1.0, (px*dx + py*dy) / length_sq))
        self.setValue(self.minimum + ratio * (self.maximum-self.minimum))

    def mousePressEvent(self, event):
        self._set_from_position(event.position())
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._set_from_position(event.position())
            event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.track.isNull():
            painter.drawPixmap(self.track_rect.toRect(), self.track)
        else:
            painter.setPen(QPen(QColor("#e8efff"), max(4, int(6*self.sy))))
            painter.drawLine(self.start, self.end)

        ratio = self.ratio()
        cx = self.start.x() + (self.end.x()-self.start.x()) * ratio
        cy = self.start.y() + (self.end.y()-self.start.y()) * ratio
        handle_rect = QRectF(cx-self.handle_size.width()/2, cy-self.handle_size.height()/2,
                             self.handle_size.width(), self.handle_size.height())
        if not self.handle.isNull():
            painter.drawPixmap(handle_rect.toRect(), self.handle)
        else:
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.setBrush(QColor("#ffd23f"))
            painter.drawRoundedRect(handle_rect, 8, 8)


class VideoThumbnailer(QObject):
    """Decodes one frame per video, sequentially, to keep startup load low."""
    ready = Signal(int, object)

    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.pending = [(i, file) for i, file in enumerate(files)
                        if file.suffix.lower() in (".mp4", ".webm")
                        and not (file.name == "Placeholder.mp4" and resource_path("Placeholder.png").exists())]
        self.current = None
        self.target_position = 0
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.player.setVideoSink(self.sink)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.sink.videoFrameChanged.connect(self._frame_changed)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._finish_current)
        self.started = False
        self.active = False

    def start(self):
        if not self.active:
            self.active = True
            QTimer.singleShot(0, self._next)

    def pause(self):
        self.active = False
        self.timeout.stop()
        if self.current is not None:
            self.pending.insert(0, self.current)
            self.current = None
        self.player.stop()
        self.player.setSource(QUrl())

    def _next(self):
        if not self.active or self.current is not None or not self.pending:
            return
        self.current = self.pending.pop(0)
        self.target_position = 0
        self.player.setSource(QUrl.fromLocalFile(str(self.current[1].resolve())))
        self.timeout.start(5000)

    def _status_changed(self, status):
        if self.current is None:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            duration = max(1, self.player.duration())
            # Pick a stable frame between 20% and 80% for each filename.
            ratio = 0.2 + (abs(hash(self.current[1].name)) % 61) / 100.0
            self.target_position = int(duration * ratio)
            self.player.setPosition(self.target_position)
            self.player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._finish_current()

    def _frame_changed(self, frame):
        if self.current is None or not frame.isValid():
            return
        if self.target_position and self.player.position() < self.target_position - 750:
            return
        image = frame.toImage()
        if image.isNull():
            return
        preview = QPixmap.fromImage(image).scaled(300, 174, Qt.KeepAspectRatioByExpanding,
                                                   Qt.SmoothTransformation)
        self.ready.emit(self.current[0], preview)
        self._finish_current()

    def _finish_current(self):
        if self.current is None:
            return
        self.timeout.stop()
        self.current = None
        self.player.stop()
        self.player.setSource(QUrl())
        QTimer.singleShot(60, self._next)


class BackgroundCard(QWidget):
    clicked = Signal(int)

    def __init__(self, index, file, preview, parent=None):
        super().__init__(parent)
        self.index, self.file, self.preview = index, file, preview
        self.animated = file.suffix.lower() in (".mp4", ".webm")
        self.is_selected = False
        self.setFixedSize(300, 200)
        self.setFocusPolicy(Qt.NoFocus)
        ui_dir = asset_path()
        self.card_background = QPixmap(str(ui_dir / "bg_background.png"))
        self.card_animated = QPixmap(str(ui_dir / "bg_animated.png"))
        self.card_selected = QPixmap(str(ui_dir / "bg_selected.png"))

    def set_preview(self, pixmap):
        self.preview = pixmap
        self.update()

    def set_selected(self, selected):
        self.is_selected = selected
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.index)
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.card_background)
        # Reserve card artwork and filename space around an uncropped preview.
        # Twenty percent larger than the previous 238x100 preview area, while
        # retaining an aspect-fit presentation and the filename space below.
        preview_rect = QRectF(self.rect().adjusted(7, 32, -7, -48))
        if not self.preview.isNull():
            source = QRectF(self.preview.rect())
            target_ratio = preview_rect.width()/max(1, preview_rect.height())
            source_ratio = source.width()/max(1, source.height())
            if source_ratio > target_ratio:
                target_height = preview_rect.width()/source_ratio
                target = QRectF(preview_rect.left(),
                                preview_rect.center().y()-target_height/2,
                                preview_rect.width(), target_height)
            else:
                target_width = preview_rect.height()*source_ratio
                target = QRectF(preview_rect.center().x()-target_width/2,
                                preview_rect.top(), target_width, preview_rect.height())
            painter.drawPixmap(target, self.preview, source)

        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setPointSize(13)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        name = metrics.elidedText(self.file.stem, Qt.ElideRight, self.width()-26)
        painter.drawText(QRectF(13, 159, self.width()-26, 32), Qt.AlignCenter, name)
        if self.animated:
            painter.drawPixmap(self.rect(), self.card_animated)
        if self.is_selected:
            painter.drawPixmap(self.rect(), self.card_selected)


class BackgroundGallery(QScrollArea):
    selected = Signal(int)

    def __init__(self, files, selected_index, parent=None):
        super().__init__(parent)
        self.files, self.cards = files, []
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("background:rgba(5,8,18,225);border:0;")
        QScroller.grabGesture(self.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        grid = QGridLayout(content)
        grid.setContentsMargins(55, 115, 55, 45)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(28)

        for index, file in enumerate(files):
            card = BackgroundCard(index, file, self._preview(file), content)
            card.clicked.connect(self.choose)
            grid.addWidget(card, index // 5, index % 5)
            self.cards.append(card)
        self.setWidget(content)
        self.highlight(selected_index)
        self.thumbnailer = VideoThumbnailer(files, self)
        self.thumbnailer.ready.connect(self.set_video_preview)

    def _preview(self, file):
        if file.name == "Placeholder.mp4" and resource_path("Placeholder.png").exists():
            file = resource_path("Placeholder.png")
        if file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return QPixmap(str(file)).scaled(300, 174, Qt.KeepAspectRatioByExpanding,
                                              Qt.SmoothTransformation)
        pixmap = QPixmap(300, 174)
        pixmap.fill(QColor("#18223a"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "VIDEO\n" + file.stem)
        painter.end()
        return pixmap

    def set_video_preview(self, index, pixmap):
        if 0 <= index < len(self.cards):
            self.cards[index].set_preview(pixmap)

    def showEvent(self, event):
        super().showEvent(event)
        self.thumbnailer.start()

    def hideEvent(self, event):
        self.thumbnailer.pause()
        super().hideEvent(event)

    def choose(self, index):
        self.highlight(index)
        self.selected.emit(index)

    def highlight(self, selected_index):
        for index, card in enumerate(self.cards):
            card.set_selected(index == selected_index)

class Btn(QLabel):
    def __init__(self, main_win, parent_widget, cfg, sx, sy, overlay_img_name="pressed.png", asset_dir="ui"):
        super().__init__(parent_widget)
        self.main_win = main_win
        self.cfg = cfg
        self.overlay = False
        self.overlay_pixmap = None
        
        img = (asset_path() if asset_dir == "ui" else Path(asset_dir)) / cfg["image"]
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

        overlay_path = (asset_path() if asset_dir == "ui" else Path(asset_dir)) / overlay_img_name
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


class TenKeyButton(QWidget):
    """Layered IIDX-style number key: base, glyph, then pressed overlay."""
    def __init__(self, main_win, digit, scale, asset_cfg, parent=None):
        super().__init__(parent)
        self.main_win = main_win
        self.digit = str(digit)
        self.keypad_index = max(0, min(1, int(asset_cfg.get("player", 1))-1))
        self.pressed_overlay = False
        ui_dir = asset_path()
        self.base = QPixmap(str(ui_dir / asset_cfg.get("key_image", "key_tenkey_num.png")))
        self.current = QPixmap(str(ui_dir / asset_cfg.get("key_current_image", "key_tenkey_num_current.png")))
        glyph_pattern = asset_cfg.get("number_image_pattern", "tenkey_num_{number}.png")
        self.glyph = QPixmap(str(ui_dir / glyph_pattern.format(number=self.digit)))
        width = max(1, int(self.base.width()*scale))
        height = max(1, int(self.base.height()*scale))
        self.setFixedSize(width, height)
        self.setFocusPolicy(Qt.NoFocus)

    def mousePressEvent(self, event):
        self.pressed_overlay = True
        self.update()
        self.main_win.trigger_action({"type": "keypad", "keypad": self.keypad_index, "key": self.digit})
        QTimer.singleShot(100, self.release_visual)
        event.accept()

    def release_visual(self):
        self.pressed_overlay = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.base)
        if not self.glyph.isNull():
            glyph_w = int(self.glyph.width() * self.width()/max(1, self.base.width()))
            glyph_h = int(self.glyph.height() * self.height()/max(1, self.base.height()))
            glyph_rect = QRectF((self.width()-glyph_w)/2, (self.height()-glyph_h)/2,
                                glyph_w, glyph_h).toRect()
            painter.drawPixmap(glyph_rect, self.glyph)
        if self.pressed_overlay and not self.current.isNull():
            painter.drawPixmap(self.rect(), self.current)


class KeypadToggleButton(QWidget):
    clicked = Signal()

    def __init__(self, base_image, current_image, parent=None):
        super().__init__(parent)
        self.base = QPixmap(str(asset_path() / base_image))
        self.current = QPixmap(str(asset_path() / current_image))
        self.active = False
        self.setFocusPolicy(Qt.NoFocus)

    def set_active(self, active):
        self.active = bool(active)
        self.update()

    def mousePressEvent(self, event):
        # Graphics View only delivers the release to a widget that accepts press.
        if event.button() == Qt.LeftButton:
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # The current graphic overlays the base; it never replaces/discards it.
        painter.drawPixmap(self.rect(), self.base)
        if self.active:
            painter.drawPixmap(self.rect(), self.current)


class LayeredAssetButton(QWidget):
    clicked = Signal()

    def __init__(self, base_image, overlay_image="", label_image="", parent=None,
                 fallback_text=""):
        super().__init__(parent)
        ui_dir = asset_path()
        self.base = QPixmap(str(ui_dir / base_image)) if base_image else QPixmap()
        self.overlay = QPixmap(str(ui_dir / overlay_image)) if overlay_image else QPixmap()
        self.label = QPixmap(str(ui_dir / label_image)) if label_image else QPixmap()
        self.fallback_text = fallback_text
        self.active = False
        self.pressed_visual = False
        self.setFocusPolicy(Qt.NoFocus)

    def set_active(self, active):
        self.active = bool(active)
        self.update()

    def mousePressEvent(self, event):
        self.pressed_visual = True
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        inside = self.rect().contains(event.position().toPoint())
        self.pressed_visual = False
        self.update()
        if inside:
            self.clicked.emit()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if not self.base.isNull():
            painter.drawPixmap(self.rect(), self.base)
        if (self.active or self.pressed_visual) and not self.overlay.isNull():
            painter.drawPixmap(self.rect(), self.overlay)
        if not self.label.isNull():
            painter.drawPixmap(self.rect(), self.label)
        elif self.fallback_text:
            painter.setPen(Qt.white)
            font = painter.font()
            font.setBold(True)
            font.setPointSize(14)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, self.fallback_text)

class FadeOpacity(QGraphicsOpacityEffect):
    """Bypass the effect cache when fully opaque, including at scaled output."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpacity(1.0)

    def getOpacity(self):
        return super().opacity()

    def setOpacity(self, value):
        super().setOpacity(value)
        self.setEnabled(value < 1.0)

    opacity = Property(float, getOpacity, setOpacity)


class CanvasOpacity(QObject):
    """Animate opacity on a scene item without nested widget-effect offsets."""
    def __init__(self, parent):
        super().__init__(parent)
        self.item = None
        self._opacity = 1.0

    def opacity(self):
        return self._opacity

    def setOpacity(self, value):
        self._opacity = float(value)
        if self.item is not None:
            self.item.setOpacity(self._opacity)

    opacity = Property(float, opacity, setOpacity)


class Win(QWidget):
    def __init__(self, game_mode=None):
        super().__init__()
        
        try:
            self.config = json.loads(resource_path("config.json").read_text(encoding="utf8"))
        except Exception:
            self.config = {}
        self.game_mode = str(game_mode or self.config.get("game_mode", "sdvx")).lower()
        if self.game_mode not in ("iidx", "sdvx"):
            self.game_mode = "sdvx"
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_config)

        # All layout/artwork coordinates stay on the 1080p design canvas.
        self.setFixedSize(1920, 1080)
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
        self.dim_opacity_effect = FadeOpacity()
        self.dim_opacity_effect.setOpacity(0.0)
        self.dim_overlay.setGraphicsEffect(self.dim_opacity_effect)

        # 3. Concentration Icon
        self.conc_icon = QLabel(self)
        conc_cfg = self.config.get("concentration_mode", {})
        rw = self.config.get("reference_width", 1920)
        rh = self.config.get("reference_height", 1080)
        sx = self.width() / rw
        sy = self.height() / rh

        img_path = asset_path() / conc_cfg.get("image", "concentration.png")
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
        self.conc_icon_opacity = FadeOpacity()
        self.conc_icon_opacity.setOpacity(0.0)
        self.conc_icon.setGraphicsEffect(self.conc_icon_opacity)

        # 4. UI Container (Holds all buttons and dropdowns so they can fade out together)
        self.ui_container = QWidget(self)
        self.ui_container.setGeometry(self.rect())
        self.ui_opacity_effect = FadeOpacity()
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

        # Always offer the bundled defaults, even with a custom background folder.
        for name in ("Placeholder.png", "Placeholder.mp4"):
            file = resource_path(name)
            if file.exists() and all(existing.name != name for existing in self.backgrounds):
                self.backgrounds.append(file)
                self.background_names.append(file.stem + (" (video)" if file.suffix == ".mp4" else " (image)"))

        selected_background = self.config.get("selected_background", "")
        if isinstance(selected_background, str):
            self.bg_index = next((i for i, file in enumerate(self.backgrounds)
                                  if file.name == selected_background), 0)
        else:
            self.bg_index = max(0, min(len(self.backgrounds)-1,
                                      int(selected_background))) if self.backgrounds else 0
        self.load_background()

        # Main pages. Navigation stays outside these containers and never disappears.
        self.home_page = QWidget(self.ui_container)
        self.backgrounds_page = QWidget(self.ui_container)
        self.settings_page = QWidget(self.ui_container)
        page_widgets = [self.home_page, self.backgrounds_page, self.settings_page]
        if self.game_mode == "iidx":
            self.sfx_page = QWidget(self.ui_container)
            page_widgets.append(self.sfx_page)
        for page in page_widgets:
            page.setGeometry(self.ui_container.rect())
        self.backgrounds_page.hide()
        self.settings_page.hide()
        self.pages = {"home": self.home_page, "backgrounds": self.backgrounds_page,
                      "settings": self.settings_page}
        if self.game_mode == "iidx":
            self.sfx_page.hide()
            self.pages["sfx"] = self.sfx_page
        self.current_page = "home"
        self.page_animation = None

        self.gallery = BackgroundGallery(self.backgrounds, self.bg_index, self.backgrounds_page)
        self.gallery.setGeometry(self.backgrounds_page.rect())
        self.gallery.selected.connect(self.change_background)

        api_cfg = self.config.get("spiceapi", {})
        self.log_cfg = self.config.get("logging", {})
        self.spice = SpiceAPIWorker(api_cfg.get("host", "127.0.0.1"),
                                    api_cfg.get("port", 1337))
        self.spice.status_changed.connect(self.update_api_status)
        self.spice.api_error.connect(self.report_api_error)

        self.api_status = QLabel("SpiceAPI: connecting…", self.ui_container)
        self.api_status.setAlignment(Qt.AlignCenter)
        self.api_status.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.api_status.setStyleSheet("background:transparent;color:#ffcc66;font-size:13px;")
        self.api_status.setGeometry(0, int(self.config.get("navigation", {}).get("home_y", 12))+74,
                                    self.width(), 20)
        self.connection_timer = QTimer(self)
        self.connection_timer.setInterval(2000)
        self.connection_timer.timeout.connect(self.spice.heartbeat)
        self.connection_timer.start()
        QTimer.singleShot(0, self.spice.heartbeat)

        overlay_img = self.config.get("pressed_overlay_image", "pressed.png")

        # Asset-based floating number pad, sized from tenkey_panel.png.
        keypad_cfg = self.config.get("floating_keypad", {})
        panel_path = asset_path() / keypad_cfg.get("panel_image", "tenkey_panel.png")
        panel_pixmap = QPixmap(str(panel_path))
        keypad_scale = float(keypad_cfg.get("scale", 0.9)) * min(sx, sy)
        panel_width = max(1, int(panel_pixmap.width()*keypad_scale))
        panel_height = max(1, int(panel_pixmap.height()*keypad_scale))
        panel_x = int(keypad_cfg.get("x", 100)*sx)
        panel_y = int(keypad_cfg.get("y", 430)*sy)
        # Keep the moving keypad out of the UI container's graphics effect. Qt can
        # offset animated child widgets when a parent QGraphicsEffect is enabled.
        self.keypad_container = QWidget(self)
        self.keypad_container.setGeometry(panel_x, panel_y, panel_width, panel_height)
        self.keypad_home_pos = QPoint(panel_x, panel_y)

        self.keypad_panel_bg = QLabel(self.keypad_container)
        self.keypad_panel_bg.setGeometry(self.keypad_container.rect())
        self.keypad_panel_bg.setPixmap(panel_pixmap)
        self.keypad_panel_bg.setScaledContents(True)
        self.keypad_panel_bg.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.keypad_panel_bg.show()

        default_keys = [
            {"digit": "7", "x": 22, "y": 86}, {"digit": "8", "x": 123, "y": 86},
            {"digit": "9", "x": 224, "y": 86}, {"digit": "4", "x": 22, "y": 187},
            {"digit": "5", "x": 123, "y": 187}, {"digit": "6", "x": 224, "y": 187},
            {"digit": "1", "x": 22, "y": 288}, {"digit": "2", "x": 123, "y": 288},
            {"digit": "3", "x": 224, "y": 288}, {"digit": "0", "x": 123, "y": 389},
        ]
        self.keypad_buttons = []
        for key_cfg in keypad_cfg.get("keys", default_keys):
            btn = TenKeyButton(self, key_cfg["digit"], keypad_scale, keypad_cfg,
                               self.keypad_container)
            btn.move(int(key_cfg["x"]*keypad_scale), int(key_cfg["y"]*keypad_scale))
            btn.show()
            self.keypad_buttons.append(btn)

        self.keypad_effect = CanvasOpacity(self.keypad_container)
        self.keypad_effect.setOpacity(1.0)
        self.keypad_visible = True
        self.keypad_animation = None

        self.build_settings_page(rw, rh)
        if self.game_mode == "iidx":
            self.sound_effects = SoundEffectsPage(self.spice, self.config.get("iidx_sfx", {}), self.sfx_page)
            self.sound_effects.setGeometry(self.sfx_page.rect())
        self.build_system_controls(sx, sy)
        self.build_navigation(sx, sy)
        self.build_player2_keypad(sx, sy)
        if self.game_mode == "iidx":
            self.ticker = IIDXTicker(self.ui_container)
            self.ticker.setGeometry(320, 117, 1280, 222)
            self.spice.ticker_read.connect(self.ticker.set_text)
            self.ticker.show()
            self.ticker_timer = QTimer(self)
            self.ticker_timer.setInterval(100)
            self.ticker_timer.timeout.connect(self.poll_ticker)
            self.ticker_timer.start()
            QTimer.singleShot(0, self.spice.ticker_get)

        # Concentration Mode Animation Setup
        self.in_concentration_mode = False
        self.anim_group = QParallelAnimationGroup()
        
        self.anim_ui = QPropertyAnimation(self.ui_opacity_effect, b"opacity")
        self.anim_keypad = QPropertyAnimation(self.keypad_effect, b"opacity")
        self.anim_dim = QPropertyAnimation(self.dim_opacity_effect, b"opacity")
        self.anim_icon = QPropertyAnimation(self.conc_icon_opacity, b"opacity")
        
        self.anim_ui.setDuration(1000)
        self.anim_keypad.setDuration(1000)
        self.anim_dim.setDuration(1000)
        self.anim_icon.setDuration(1000)
        
        self.anim_group.addAnimation(self.anim_ui)
        self.anim_group.addAnimation(self.anim_keypad)
        self.anim_group.addAnimation(self.anim_dim)
        self.anim_group.addAnimation(self.anim_icon)
        if hasattr(self, "p2_effect"):
            self.anim_p2 = QPropertyAnimation(self.p2_effect, b"opacity")
            self.anim_p2.setDuration(1000)
            self.anim_group.addAnimation(self.anim_p2)

        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.enter_concentration_mode)
        
        # Install global event filter to catch all touches
        QApplication.instance().installEventFilter(self)

        self.reset_idle_timer()

    def poll_ticker(self):
        if self.ticker.isVisible() and not self.in_concentration_mode:
            self.spice.ticker_get()

    def update_api_status(self, connected, message):
        if self.api_status.text() == message:
            return
        color = "#73ff8a" if connected else "#ffcc66"
        self.api_status.setText(message)
        self.api_status.setStyleSheet(
            f"background:transparent;color:{color};font-size:13px;")

    def report_api_error(self, message):
        if self.log_cfg.get("print_api_errors", True):
            print(f"SpiceAPI: {message}", file=sys.stderr)

    def build_navigation(self, sx, sy):
        self.nav_buttons = {}
        nav_cfg = self.config.get("navigation", {})
        home = LayeredAssetButton("icon_home.png", parent=self.ui_container)
        home.setGeometry((self.width()-70)//2, int(nav_cfg.get("home_y", 12)), 70, 70)
        home.clicked.connect(lambda: self.switch_page("home"))
        home.show()
        self.nav_buttons["home"] = home

        actions = [("backgrounds", "menubt-backgrounds.png"),
                   ("settings", "menubt-settings.png")]
        if self.game_mode == "iidx" and "sfx" in self.pages:
            actions.append(("sfx", "menubt-sfx.png"))
        button_width = 250
        button_height = 56
        menu_gap = int(nav_cfg.get("menu_gap", 20))
        total_width = len(actions)*button_width + max(0, len(actions)-1)*menu_gap
        menu_x = (self.width()-total_width)//2
        menu_y = self.height()-button_height-int(nav_cfg.get("menu_bottom_margin", 24))
        for index, (key, label_image) in enumerate(actions):
            button = LayeredAssetButton("menubt-off.png", "menubt-on.png", label_image,
                                        self.ui_container)
            button.setGeometry(menu_x + index*(button_width+menu_gap), menu_y,
                               button_width, button_height)
            button.clicked.connect(lambda checked=False, page=key: self.switch_page(page))
            button.show()
            self.nav_buttons[key] = button
        self.update_navigation_state()

        keypad_cfg = self.config.get("floating_keypad", {})
        toggle_size = max(54, int(keypad_cfg.get("toggle_size", 86)*min(sx, sy)))
        toggle = KeypadToggleButton(
            keypad_cfg.get("toggle_image", "tenkey_mini.png"),
            keypad_cfg.get("toggle_current_image", "tenkey_mini_current.png"),
            self.ui_container)
        toggle.setGeometry(int(keypad_cfg.get("toggle_x", 25)*sx),
                           int(keypad_cfg.get("toggle_y", 20)*sy),
                           toggle_size, toggle_size)
        toggle.clicked.connect(self.toggle_keypad)
        toggle.show()
        self.nav_buttons["keypad"] = toggle
        self.update_keypad_toggle_graphic()

    def update_navigation_state(self):
        if hasattr(self, "ticker"):
            self.ticker.setVisible(self.current_page in ("home", "sfx"))
            self.ticker.raise_()
        for key in ("backgrounds", "settings", "sfx"):
            if key in getattr(self, "nav_buttons", {}):
                self.nav_buttons[key].set_active(self.current_page == key)

    def update_keypad_toggle_graphic(self):
        if not hasattr(self, "nav_buttons") or "keypad" not in self.nav_buttons:
            return
        button = self.nav_buttons["keypad"]
        button.set_active(self.keypad_visible)

    def build_settings_page(self, rw, rh):
        self.settings_page.setStyleSheet("background:transparent;")
        conc_cfg = self.config.setdefault("concentration_mode", {})
        panel_width, panel_height = 525, 825
        panel_x = int(conc_cfg.get("panel_x", 140)*self.width()/rw)
        panel_y = max(0, (self.settings_page.height()-panel_height)//2)
        self.concentration_panel = QLabel(self.settings_page)
        self.concentration_panel.setGeometry(panel_x, panel_y, panel_width, panel_height)
        self.concentration_panel.setPixmap(QPixmap(str(asset_path("concentrationmode_bg.png"))))
        self.concentration_panel.setScaledContents(False)
        self.concentration_panel.setAttribute(Qt.WA_TransparentForMouseEvents)

        # Resolve the native slider artwork relative to the panel so the group
        # remains aligned even when the target monitor is not exactly 1080px tall.
        page_sx = self.settings_page.width()/rw
        page_sy = self.settings_page.height()/rh
        timeout_cfg = dict(conc_cfg.get("timeout_slider", {}))
        dim_cfg = dict(conc_cfg.get("dim_slider", {}))
        timeout_cfg.update(track_x=(panel_x+57)/page_sx, track_y=(panel_y+85)/page_sy)
        dim_cfg.update(track_x=(panel_x+316)/page_sx, track_y=(panel_y+85)/page_sy)
        self.timeout_slider = AssetSlider(timeout_cfg, conc_cfg.get("timeout_seconds", 30),
                                          (rw, rh), self.settings_page)
        self.dim_slider = AssetSlider(dim_cfg, float(conc_cfg.get("dim_opacity", 0.75))*100,
                                      (rw, rh), self.settings_page)
        self.timeout_slider.valueChanged.connect(self.set_timeout_value)
        self.dim_slider.valueChanged.connect(self.set_dim_value)

        self.timeout_label = QLabel(self.settings_page)
        self.timeout_label.setGeometry(panel_x, panel_y+682, 262, 45)
        self.dim_label = QLabel(self.settings_page)
        self.dim_label.setGeometry(panel_x+263, panel_y+682, 262, 45)
        for label in (self.timeout_label, self.dim_label):
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("background:transparent;color:white;font-size:27px;font-weight:bold;")
        self._update_setting_labels()

        self.conc_enable_btn = LayeredAssetButton(
            "concentrationmode.png", "concentrationmode_on.png",
            "concentrationmode_label.png", self.settings_page)
        self.conc_enable_btn.setGeometry(panel_x+(panel_width-400)//2, panel_y+746, 400, 56)
        self.conc_enable_btn.clicked.connect(
            lambda: self.set_concentration_enabled(
                not bool(self.config.get("concentration_mode", {}).get("enabled", False))))
        self._update_concentration_button()

    def build_system_controls(self, sx, sy):
        panel_width, panel_height = 325, 525
        panel_x = self.settings_page.width()-panel_width-140
        panel_y = max(0, (self.settings_page.height()-panel_height)//2)
        self.system_panel = QLabel(self.settings_page)
        self.system_panel.setGeometry(panel_x, panel_y, panel_width, panel_height)
        self.system_panel.setPixmap(QPixmap(str(asset_path("sys-bg.png"))))
        self.system_panel.setScaledContents(False)
        self.system_panel.setAttribute(Qt.WA_TransparentForMouseEvents)

        definitions = [("TEST", "sys-test.png", {"type": "button", "name": "Test"}, 100),
                       ("SERVICE", "sys-service.png", {"type": "button", "name": "Service"}, 215),
                       ("COIN", "sys-coin.png", {"type": "coin"}, 330)]
        self.system_buttons = []
        for fallback, label_name, action, y in definitions:
            label_path = label_name if asset_path(label_name).exists() else ""
            button = LayeredAssetButton("sys-bt.png", "sys-on.png", label_path,
                                        self.settings_page, fallback_text=fallback)
            button.setGeometry(panel_x+(panel_width-200)//2, panel_y+y, 200, 96)
            button.clicked.connect(lambda a=action: self.trigger_action(a))
            self.system_buttons.append(button)

    def _update_concentration_button(self):
        enabled = bool(self.config.get("concentration_mode", {}).get("enabled", False))
        self.conc_enable_btn.set_active(enabled)

    def _update_setting_labels(self):
        self.timeout_label.setText(f"{int(round(self.timeout_slider.value))} SECONDS")
        self.dim_label.setText(f"{int(round(self.dim_slider.value))}%")

    def set_concentration_enabled(self, enabled):
        self.config.setdefault("concentration_mode", {})["enabled"] = bool(enabled)
        self._update_concentration_button()
        if enabled:
            self.reset_idle_timer()
        else:
            self.idle_timer.stop()
            self.exit_concentration_mode()
        self.save_config()

    def set_timeout_value(self, value):
        self.config.setdefault("concentration_mode", {})["timeout_seconds"] = int(round(value))
        self._update_setting_labels()
        if hasattr(self, "idle_timer"):
            self.reset_idle_timer()
        self.schedule_config_save()

    def set_dim_value(self, value):
        self.config.setdefault("concentration_mode", {})["dim_opacity"] = round(value/100.0, 3)
        self._update_setting_labels()
        self.schedule_config_save()

    def schedule_config_save(self):
        self._save_timer.start(350)

    def save_config(self):
        try:
            Path("config.json").write_text(json.dumps(self.config, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf8")
        except OSError as exc:
            self.report_api_error(f"Could not save config: {exc}")

    def switch_page(self, target):
        if target == self.current_page or target not in self.pages:
            return
        if self.page_animation is not None:
            self.page_animation.stop()
            self.page_animation.deleteLater()
            self.page_animation = None
        # Cancel an interrupted transition before removing its target effects.
        for name, page in self.pages.items():
            page.setGraphicsEffect(None)
            page.setVisible(name == self.current_page)
        old_page, new_page = self.pages[self.current_page], self.pages[target]
        self.current_page = target
        self.update_navigation_state()
        old_effect = FadeOpacity(old_page)
        old_page.setGraphicsEffect(old_effect)
        fade_out = QPropertyAnimation(old_effect, b"opacity", self)
        fade_out.setDuration(170)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)

        def show_new_page():
            fade_out.deleteLater()
            old_page.hide()
            old_page.setGraphicsEffect(None)
            new_effect = FadeOpacity(new_page)
            new_page.setGraphicsEffect(new_effect)
            new_effect.setOpacity(0.0)
            new_page.show()
            new_page.raise_()
            self.api_status.raise_()
            if hasattr(self, "ticker"):
                self.ticker.raise_()
            if self.keypad_visible:
                self.keypad_container.raise_()
            for button in self.nav_buttons.values():
                button.raise_()
            fade_in = QPropertyAnimation(new_effect, b"opacity", self)
            fade_in.setDuration(220)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)
            def finish_transition():
                new_page.setGraphicsEffect(None)
                if self.page_animation is fade_in:
                    self.page_animation = None
                fade_in.deleteLater()
            fade_in.finished.connect(finish_transition)
            self.page_animation = fade_in
            fade_in.start()

        fade_out.finished.connect(show_new_page)
        self.page_animation = fade_out
        fade_out.start()

    def build_player2_keypad(self, sx, sy):
        p2 = self.config.get("iidx_player2_keypad", {})
        if self.game_mode != "iidx" or not p2.get("enabled", True):
            return
        cfg = dict(self.config.get("floating_keypad", {}))
        cfg.update(p2)
        cfg["player"] = 2
        self.p2_cfg = cfg
        scale = float(cfg.get("scale", .9)) * min(sx, sy)
        pm = QPixmap(str(asset_path() / cfg.get("panel_image", "tenkey_panel.png")))
        self.p2_panel = QWidget(self)
        self.p2_panel.resize(int(pm.width()*scale), int(pm.height()*scale))
        self.p2_home = QPoint(int(p2.get("x", 1510)*sx), int(p2.get("y", 430)*sy))
        self.p2_panel.move(self.p2_home)
        bg = QLabel(self.p2_panel)
        bg.setGeometry(self.p2_panel.rect())
        bg.setPixmap(pm)
        bg.setScaledContents(True)
        bg.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.p2_buttons = []
        for key in cfg.get("keys", []):
            btn = TenKeyButton(self, key["digit"], scale, cfg, self.p2_panel)
            btn.move(int(key["x"]*scale), int(key["y"]*scale))
            self.p2_buttons.append(btn)
        self.p2_effect = CanvasOpacity(self.p2_panel)
        self.p2_effect.setOpacity(1.)
        self.p2_visible = True
        self.p2_animation = None
        size = max(54, int(cfg.get("toggle_size", 86)*min(sx, sy)))
        toggle = LayeredAssetButton(
            cfg.get("toggle_image", "tenkey_mini.png"),
            cfg.get("toggle_current_image", "tenkey_mini_current.png"),
            "", self.ui_container)
        toggle.setGeometry(int(p2.get("toggle_x", 1809)*sx),
                           int(p2.get("toggle_y", 20)*sy), size, size)
        toggle.set_active(True)
        toggle.clicked.connect(self.toggle_player2_keypad)
        toggle.show()
        self.nav_buttons["keypad2"] = toggle

    def toggle_player2_keypad(self):
        if self.p2_animation is not None:
            self.p2_animation.stop()
            self.p2_animation.deleteLater()
        animation = QPropertyAnimation(self.p2_panel, b"pos", self)
        self.p2_animation = animation
        animation.setDuration(int(self.p2_cfg.get("animation_duration_ms", 800)))
        animation.setEasingCurve(QEasingCurve.InOutCubic)
        offset = QPoint(max(self.width()+40, self.p2_home.x()+self.p2_panel.width()+40), self.p2_home.y())
        animation.setStartValue(self.p2_panel.pos())
        self.p2_visible = not self.p2_visible
        self.p2_effect.setOpacity(1.)
        if self.p2_visible:
            self.p2_panel.show()
            self.p2_panel.raise_()
            animation.setEndValue(self.p2_home)
        else:
            animation.setEndValue(offset)
            animation.finished.connect(self.p2_panel.hide)
        self.nav_buttons["keypad2"].set_active(self.p2_visible)
        animation.start()

    def toggle_keypad(self):
        if self.keypad_animation is not None:
            self.keypad_animation.stop()
            self.keypad_animation.deleteLater()
        animation = QPropertyAnimation(self.keypad_container, b"pos", self)
        self.keypad_animation = animation
        cfg = self.config.get("floating_keypad", {})
        animation.setDuration(int(cfg.get("animation_duration_ms", 800)))
        animation.setEasingCurve(QEasingCurve.InOutCubic)
        home = self.keypad_home_pos
        right = str(cfg.get("slide_direction", "left")).lower() == "right"
        offset = QPoint(self.width()+40 if right else -self.keypad_container.width()-40, home.y())
        animation.setStartValue(self.keypad_container.pos())
        self.keypad_visible = not self.keypad_visible
        self.keypad_effect.setOpacity(1.)
        if self.keypad_visible:
            self.keypad_container.show()
            self.keypad_container.raise_()
            animation.setEndValue(home)
        else:
            animation.setEndValue(offset)
            animation.finished.connect(self.keypad_container.hide)
        self.update_keypad_toggle_graphic()
        animation.start()

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
        self.anim_keypad.setEndValue(0.0)
        if hasattr(self, "anim_p2"):
            self.anim_p2.setEndValue(0.0)
        self.anim_dim.setEndValue(self.config.get("concentration_mode", {}).get("dim_opacity", 0.5))
        self.anim_icon.setEndValue(1.0)
        self.anim_group.start()

    def exit_concentration_mode(self):
        if not self.in_concentration_mode: return
        self.in_concentration_mode = False
        
        self.anim_group.stop()
        self.anim_ui.setEndValue(1.0)
        self.anim_keypad.setEndValue(1.0 if self.keypad_visible else 0.0)
        if hasattr(self, "anim_p2"):
            self.anim_p2.setEndValue(1.0 if self.p2_visible else 0.0)
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
        if index < 0 or index >= len(self.backgrounds):
            return
        self.bg_index = index
        self.config["selected_background"] = self.backgrounds[index].name
        if hasattr(self, "gallery"):
            self.gallery.highlight(index)
        self.load_background()
        self.save_config()

    def load_background(self):
        self.media_player.stop()
        self.media_player.setSource(QUrl())
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
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.media_player.setActiveAudioTrack(-1)  # Background audio is always muted.
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
        self._save_timer.stop()
        self.idle_timer.stop()
        self.anim_group.stop()
        for animation in (self.page_animation, self.keypad_animation, getattr(self, "p2_animation", None)):
            if animation is not None:
                animation.stop()
                animation.deleteLater()
        self.page_animation = self.keypad_animation = None
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self.video.clear_frame()
        self.gallery.thumbnailer.pause()
        self.connection_timer.stop()
        if hasattr(self, "ticker_timer"):
            self.ticker_timer.stop()
        if hasattr(self, "sound_effects"):
            self.sound_effects.timer.stop()
        self.spice.close()
        super().closeEvent(event)

class SubscreenWindow(QGraphicsView):
    """Scale the complete 1080p canvas, including input, into the output window."""
    def __init__(self, game_mode=None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowIcon(QIcon(str(resource_path("icon.ico"))))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor("black"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.content = Win(game_mode)
        self.canvas_scene = QGraphicsScene(self)
        self.setScene(self.canvas_scene)
        self.proxy = self.canvas_scene.addWidget(self.content)
        self.keypad_proxies = []
        panels = [(self.content.keypad_container, self.content.keypad_effect, self.content.keypad_home_pos)]
        if hasattr(self.content, "p2_panel"):
            panels.append((self.content.p2_panel, self.content.p2_effect, self.content.p2_home))
        for panel, opacity, home in panels:
            panel.setParent(None)
            panel.move(home)
            proxy = self.canvas_scene.addWidget(panel)
            panel.setAutoFillBackground(False)
            panel.setAttribute(Qt.WA_TranslucentBackground, True)
            proxy.setZValue(1)
            proxy.setPos(home)
            opacity.item = proxy
            proxy.show()
            self.keypad_proxies.append(proxy)
        self.setSceneRect(0, 0, 1920, 1080)
        self._target_screen = None
        self._target_screen_name = None
        self.sync_monitor_geometry()
        # Games may change the primary display mode after the hook starts us.
        # Track the target screen's identity and current desktop coordinates.
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(500)
        self.monitor_timer.timeout.connect(self.sync_monitor_geometry)
        self.monitor_timer.start()
        self.apply_no_activate_style()
        self.show()
        self.update_canvas_scale()

    def sync_monitor_geometry(self):
        screens = QApplication.screens()
        if not screens:
            return
        if self._target_screen_name is None:
            index = max(0, int(self.content.config.get("monitor", 2))-1)
            target = screens[index] if index < len(screens) else screens[0]
            self._target_screen = target
            self._target_screen_name = target.name()
        elif self._target_screen in screens:
            target = self._target_screen
        else:
            # A removed/recreated screen may have a new Qt object. Only use
            # its name as a reconnect hint when that name is unambiguous.
            matches = [screen for screen in screens if screen.name() == self._target_screen_name]
            target = matches[0] if len(matches) == 1 else screens[0]
            if len(matches) == 1:
                self._target_screen = target
        geo = target.geometry()
        resolution = self.content.config.get("application_resolution", {})
        width = int(resolution.get("width", 0)) or geo.width()
        height = int(resolution.get("height", 0)) or geo.height()
        if width < 320 or height < 180:
            return  # Ignore temporary invalid geometry during a display switch.
        if (self.x(), self.y(), self.width(), self.height()) != (geo.x(), geo.y(), width, height):
            # Moving/resizing the existing nonactivating window does not request focus.
            self.setGeometry(geo.x(), geo.y(), width, height)
            self.update_canvas_scale()

    def update_canvas_scale(self):
        scale = min(self.viewport().width()/1920, self.viewport().height()/1080)
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(960, 540)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_canvas_scale()

    def closeEvent(self, event):
        self.monitor_timer.stop()
        self.content.close()
        super().closeEvent(event)

    def apply_no_activate_style(self):
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000)  # WS_EX_NOACTIVATE

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


def main():
    import argparse
    import os
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-mode", choices=("iidx", "sdvx"), default=None)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    os.chdir(APP_ROOT)
    app = QApplication(sys.argv[:1])
    w = SubscreenWindow(args.game_mode)
    # Keep this auxiliary UI below the game's CPU scheduling priority.
    if sys.platform == "win32" and w.content.config.get("performance", {}).get("background_priority", True):
        kernel_priority = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel_priority.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel_priority.SetPriorityClass(ctypes.c_void_p(-1), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    parent_handle = None
    if args.parent_pid and sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        parent_handle = kernel.OpenProcess(0x00100000, False, args.parent_pid)
        if not parent_handle:
            w.close()
            return 1
        timer = QTimer(w)
        timer.setInterval(500)
        def check_parent():
            if kernel.WaitForSingleObject(parent_handle, 0) != 258:
                w.close()
                app.quit()
        timer.timeout.connect(check_parent)
        timer.start()
    result = app.exec()
    if parent_handle:
        kernel.CloseHandle(parent_handle)
    return result


if __name__ == "__main__":
    sys.exit(main())
