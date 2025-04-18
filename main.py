#!/usr/bin/env python3
import sys, subprocess, threading, asyncio, requests, os, stat
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QHBoxLayout,
    QFileDialog, QLabel, QComboBox, QCheckBox
)
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

# Ensure XDG_RUNTIME_DIR has correct permissions (0700)
def _fix_runtime_dir():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime and os.path.isdir(runtime):
        mode = stat.S_IMODE(os.stat(runtime).st_mode)
        if mode != 0o700:
            fallback = os.path.expanduser('~/.cache/xdg_runtime')
            os.makedirs(fallback, exist_ok=True)
            os.chmod(fallback, 0o700)
            os.environ['XDG_RUNTIME_DIR'] = fallback
    else:
        # create default if not set
        fallback = os.path.expanduser('~/.cache/xdg_runtime')
        os.makedirs(fallback, exist_ok=True)
        os.chmod(fallback, 0o700)
        os.environ['XDG_RUNTIME_DIR'] = fallback

_fix_runtime_dir()

class StreamPublisher(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MediaMTX Publisher (Advanced)")
        self.proc = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        # Source type selector
        h0 = QHBoxLayout()
        self.source_cb = QComboBox()
        self.source_cb.addItems(["File", "Screen", "Camera"])
        self.source_cb.currentTextChanged.connect(self._update_source_ui)
        h0.addWidget(QLabel("Source: "))
        h0.addWidget(self.source_cb)
        layout.addLayout(h0)

        # File or device selector
        h1 = QHBoxLayout()
        self.file_edit = QLineEdit()
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self.browse)
        self.device_label = QLabel(" ")
        h1.addWidget(self.file_edit)
        h1.addWidget(btn_browse)
        h1.addWidget(self.device_label)
        layout.addLayout(h1)

        # Protocol selector
        h2 = QHBoxLayout()
        self.proto_cb = QComboBox()
        self.proto_cb.addItems(["RTMP", "RTSP", "WHIP"])
        self.url_edit = QLineEdit()
        h2.addWidget(QLabel("Protocol:"))
        h2.addWidget(self.proto_cb)
        h2.addWidget(QLabel("URL:"))
        h2.addWidget(self.url_edit)
        layout.addLayout(h2)

        # Transcode options
        self.transcode_cb = QCheckBox("Enable Transcode/Filter/Overlay")
        self.transcode_cb.stateChanged.connect(self._toggle_transcode_ui)
        layout.addWidget(self.transcode_cb)

        self.trans_widget = QWidget()
        tlay = QVBoxLayout()
        rlay = QHBoxLayout()
        self.res_edit = QLineEdit("1280x720")
        self.bitrate_edit = QLineEdit("2M")
        rlay.addWidget(QLabel("Resolution:"))
        rlay.addWidget(self.res_edit)
        rlay.addWidget(QLabel("Bitrate:"))
        rlay.addWidget(self.bitrate_edit)
        tlay.addLayout(rlay)
        tl = QHBoxLayout()
        self.text_overlay = QLineEdit()
        tl.addWidget(QLabel("Overlay text:"))
        tl.addWidget(self.text_overlay)
        tlay.addLayout(tl)
        il = QHBoxLayout()
        self.img_overlay = QLineEdit()
        btn_img = QPushButton("Browse Img…")
        btn_img.clicked.connect(self.browse_img)
        il.addWidget(QLabel("Overlay img:"))
        il.addWidget(self.img_overlay)
        il.addWidget(btn_img)
        tlay.addLayout(il)
        self.trans_widget.setLayout(tlay)
        self.trans_widget.setVisible(False)
        layout.addWidget(self.trans_widget)

        # Control buttons
        h3 = QHBoxLayout()
        self.btn_start = QPushButton("Start")
        self.btn_stop  = QPushButton("Stop")
        self.btn_start.clicked.connect(self.start_stream)
        self.btn_stop.clicked.connect(self.stop_stream)
        h3.addWidget(self.btn_start)
        h3.addWidget(self.btn_stop)
        layout.addLayout(h3)

        # Log output
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        self.setLayout(layout)
        self._update_source_ui(self.source_cb.currentText())

    def _update_source_ui(self, mode):
        if mode == "File":
            self.file_edit.setEnabled(True)
            self.device_label.setText("")
        elif mode == "Screen":
            self.file_edit.setEnabled(False)
            self.device_label.setText("x11grab on :0.0")
        else:
            self.file_edit.setEnabled(False)
            self.device_label.setText("/dev/video0 via v4l2")

    def _toggle_transcode_ui(self, st):
        self.trans_widget.setVisible(st == 2)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose video file")
        if path:
            self.file_edit.setText(path)

    def browse_img(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose overlay image")
        if path:
            self.img_overlay.setText(path)

    def log_append(self, txt):
        self.log.append(txt)

    def start_stream(self):
        proto = self.proto_cb.currentText()
        url = self.url_edit.text().strip()
        if not url:
            self.log_append("⚠️ Vui lòng nhập URL.")
            return

        if proto in ("RTMP", "RTSP"):
            self._start_ffmpeg_stream()
        else:
            self.log_append("▶️ Khởi tạo WHIP streaming…")
            threading.Thread(target=lambda: asyncio.run(self._whip_publish(url)), daemon=True).start()

    def _start_ffmpeg_stream(self):
        mode = self.source_cb.currentText()
        if mode == "File":
            input_args = ["-re", "-i", self.file_edit.text().strip()]
        elif mode == "Screen":
            input_args = ["-f", "x11grab", "-i", ":0.0"]
        else:
            input_args = ["-f", "v4l2", "-i", "/dev/video0"]

        args = ["ffmpeg"] + input_args
        if self.transcode_cb.isChecked():
            args += ["-c:v", "libx264", "-b:v", self.bitrate_edit.text(), "-s", self.res_edit.text(),
                     "-c:a", "aac", "-b:a", "128k"]
            filters = []
            txt = self.text_overlay.text().strip()
            if txt:
                filters.append(f"drawtext=text='{txt}':fontcolor=white:fontsize=24:x=10:y=10")
            img = self.img_overlay.text().strip()
            if img and os.path.exists(img):
                filters.append(f"movie={img}[logo];[in][logo]overlay=W-w-10:H-h-10[out]")
            if filters:
                args += ["-vf", ','.join(filters)]
        else:
            args += ["-c", "copy"]

        fmt = "flv" if self.proto_cb.currentText() == "RTMP" else "rtsp"
        args += ["-f", fmt, self.url_edit.text().strip()]
        self.log_append(f"▶️ Running: {' '.join(args)}")
        def run_ffmpeg():
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in self.proc.stdout:
                self.log_append(line.strip())
        threading.Thread(target=run_ffmpeg, daemon=True).start()

    async def _whip_publish(self, whip_url):
        options = {}
        if self.transcode_cb.isChecked():
            options.update({'-s': self.res_edit.text(), '-b:v': self.bitrate_edit.text(),
                            '-c:v': 'libx264', '-c:a': 'aac', '-b:a': '128k'})
            filters = []
            txt = self.text_overlay.text().strip()
            if txt:
                filters.append(f"drawtext=text='{txt}':fontcolor=white:fontsize=24:x=10:y=10")
            img = self.img_overlay.text().strip()
            if img and os.path.exists(img):
                filters.append(f"movie={img}[logo];[in][logo]overlay=W-w-10:H-h-10[out]")
            if filters:
                options['-vf'] = ','.join(filters)

        if self.source_cb.currentText() == "File":
            player = MediaPlayer(self.file_edit.text().strip(), options=options or None)
        elif self.source_cb.currentText() == "Screen":
            player = MediaPlayer(':0.0', format='x11grab', options={'framerate':'30', **options})
        else:
            player = MediaPlayer('/dev/video0', format='v4l2', options={'framerate':'30', **options})

        pc = RTCPeerConnection()
        if player.audio:
            pc.addTrack(player.audio)
        if player.video:
            pc.addTrack(player.video)

        self.log_append("[WHIP] Tạo offer và gathering ICE…")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        gather_complete = asyncio.Event()
        @pc.on('icegatheringstatechange')
        def on_state_change():
            if pc.iceGatheringState == 'complete':
                gather_complete.set()
        await gather_complete.wait()

        self.log_append("[WHIP] Gửi SDP tới server…")
        headers = {'Content-Type':'application/sdp'}
        resp = requests.post(whip_url, data=pc.localDescription.sdp, headers=headers, verify=False)
        if resp.status_code not in (200,201):
            self.log_append(f"[WHIP] Lỗi HTTP {resp.status_code}: {resp.text}")
            return

        location = resp.headers.get('Location')
        self.log_append(f"[WHIP] Session endpoint: {location}")

        answer = RTCSessionDescription(sdp=resp.text, type='answer')
        await pc.setRemoteDescription(answer)
        self.log_append("[WHIP] Streaming đã bắt đầu. Nhấn Stop.")

        try:
            while pc.connectionState != 'closed':
                await asyncio.sleep(1)
        finally:
            if location:
                requests.delete(location)

    def stop_stream(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.log_append("🛑 Đã dừng streaming.")
        else:
            self.log_append("🛑 Không có tiến trình ffmpeg đang chạy.")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = StreamPublisher()
    w.resize(900,700)
    w.show()
    sys.exit(app.exec_())
