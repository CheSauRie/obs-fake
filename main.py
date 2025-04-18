#!/usr/bin/env python3
import sys, subprocess, threading, asyncio, requests, os, stat
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QHBoxLayout,
    QFileDialog, QLabel, QComboBox, QCheckBox
)
from PyQt5.QtCore import pyqtSignal, Qt
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

# Ensure XDG_RUNTIME_DIR has correct permissions (0700)
def _fix_runtime_dir():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    fallback = os.path.expanduser('~/.cache/xdg_runtime')
    if runtime and os.path.isdir(runtime):
        mode = stat.S_IMODE(os.stat(runtime).st_mode)
        if mode != 0o700:
            os.makedirs(fallback, exist_ok=True)
            os.chmod(fallback, 0o700)
            os.environ['XDG_RUNTIME_DIR'] = fallback
    else:
        os.makedirs(fallback, exist_ok=True)
        os.chmod(fallback, 0o700)
        os.environ['XDG_RUNTIME_DIR'] = fallback

_fix_runtime_dir()

class StreamPublisher(QWidget):
    # signal to append log text in main thread
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MediaMTX Publisher (Advanced)")
        self.proc = None
        self.log_signal.connect(self._append_log)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        # Source selector
        h0 = QHBoxLayout()
        self.source_cb = QComboBox()
        self.source_cb.addItems(["File","Screen","Camera"])
        self.source_cb.currentTextChanged.connect(self._update_source_ui)
        h0.addWidget(QLabel("Source:"))
        h0.addWidget(self.source_cb)
        layout.addLayout(h0)

        # File/device selector
        h1 = QHBoxLayout()
        self.file_edit = QLineEdit()
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self.browse)
        self.device_label = QLabel("")
        h1.addWidget(self.file_edit)
        h1.addWidget(btn_browse)
        h1.addWidget(self.device_label)
        layout.addLayout(h1)

        # Protocol selector
        h2 = QHBoxLayout()
        self.proto_cb = QComboBox()
        self.proto_cb.addItems(["RTMP","RTSP","WHIP"])
        self.url_edit = QLineEdit()
        h2.addWidget(QLabel("Protocol:"))
        h2.addWidget(self.proto_cb)
        h2.addWidget(QLabel("URL:"))
        h2.addWidget(self.url_edit)
        layout.addLayout(h2)

        # Transcode options
        self.transcode_cb = QCheckBox("Enable Transcode/Filter/Overlay")
        self.transcode_cb.stateChanged.connect(lambda st: self.trans_widget.setVisible(st==Qt.Checked))
        layout.addWidget(self.transcode_cb)

        self.trans_widget = QWidget()
        tlay = QVBoxLayout()
        # resolution/bitrate
        rlay = QHBoxLayout()
        self.res_edit = QLineEdit("1280x720")
        self.bitrate_edit = QLineEdit("2M")
        rlay.addWidget(QLabel("Resolution:")); rlay.addWidget(self.res_edit)
        rlay.addWidget(QLabel("Bitrate:")); rlay.addWidget(self.bitrate_edit)
        tlay.addLayout(rlay)
        # overlay text
        tl = QHBoxLayout()
        self.text_overlay = QLineEdit()
        tl.addWidget(QLabel("Overlay text:")); tl.addWidget(self.text_overlay)
        tlay.addLayout(tl)
        # overlay image
        il = QHBoxLayout()
        self.img_overlay = QLineEdit()
        btn_img = QPushButton("Browse Img…"); btn_img.clicked.connect(self.browse_img)
        il.addWidget(QLabel("Overlay img:")); il.addWidget(self.img_overlay); il.addWidget(btn_img)
        tlay.addLayout(il)
        self.trans_widget.setLayout(tlay)
        self.trans_widget.setVisible(False)
        layout.addWidget(self.trans_widget)

        # Control buttons
        h3 = QHBoxLayout()
        btn_start = QPushButton("Start"); btn_start.clicked.connect(self.start_stream)
        btn_stop  = QPushButton("Stop");  btn_stop.clicked.connect(self.stop_stream)
        h3.addWidget(btn_start); h3.addWidget(btn_stop)
        layout.addLayout(h3)

        # Log output
        self.log = QTextEdit(); self.log.setReadOnly(True)
        layout.addWidget(self.log)

        self.setLayout(layout)
        self._update_source_ui(self.source_cb.currentText())

    def _update_source_ui(self, mode):
        if mode=="File":
            self.file_edit.setEnabled(True); self.device_label.setText("")
        elif mode=="Screen":
            self.file_edit.setEnabled(False); self.device_label.setText("x11grab :0.0")
        else:
            self.file_edit.setEnabled(False); self.device_label.setText("/dev/video0 v4l2")

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose video file")
        if path: self.file_edit.setText(path)

    def browse_img(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose overlay image")
        if path: self.img_overlay.setText(path)

    def _append_log(self, txt):
        self.log.append(txt)

    def log(self, txt):
        # emit to main thread
        self.log_signal.emit(txt)

    def start_stream(self):
        url=self.url_edit.text().strip()
        if not url:
            self.log("⚠️ Please enter URL.")
            return
        if self.proto_cb.currentText() in ("RTMP","RTSP"):
            threading.Thread(target=self._start_ffmpeg_stream,daemon=True).start()
        else:
            self.log("▶️ Starting WHIP stream…")
            threading.Thread(target=lambda: asyncio.run(self._whip_publish(url)),daemon=True).start()

    def _start_ffmpeg_stream(self):
        # build args
        mode=self.source_cb.currentText()
        if mode=="File": in_args=["-re","-i",self.file_edit.text().strip()]
        elif mode=="Screen": in_args=["-f","x11grab","-i",":0.0"]
        else: in_args=["-f","v4l2","-i","/dev/video0"]
        args=["ffmpeg"]+in_args
        if self.transcode_cb.isChecked():
            args+= ["-c:v","libx264","-b:v",self.bitrate_edit.text(),"-s",self.res_edit.text(),
                    "-c:a","aac","-b:a","128k"]
            f=[]
            if self.text_overlay.text().strip():
                f.append(f"drawtext=text='{self.text_overlay.text().strip()}':fontcolor=white:fontsize=24:x=10:y=10")
            if os.path.exists(self.img_overlay.text().strip()):
                img=self.img_overlay.text().strip()
                f.append(f"movie={img}[logo];[in][logo]overlay=W-w-10:H-h-10[out]")
            if f: args+= ["-vf",','.join(f)]
        else:
            args+= ["-c","copy"]
        fmt="flv" if self.proto_cb.currentText()=="RTMP" else "rtsp"
        args+= ["-f",fmt,self.url_edit.text().strip()]
        self.log(f"▶️ {' '.join(args)}")
        proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        self.proc=proc
        for line in proc.stdout:
            self.log(line.strip())

    async def _whip_publish(self,whip_url):
        # prepare MediaPlayer
        opts={}
        if self.transcode_cb.isChecked():
            opts={'-s':self.res_edit.text(),'-b:v':self.bitrate_edit.text(),'-c:v':'libx264','-c:a':'aac','-b:a':'128k'}
            vf=[]
            if self.text_overlay.text().strip():
                vf.append(f"drawtext=text='{self.text_overlay.text().strip()}':fontcolor=white:fontsize=24:x=10:y=10")
            if os.path.exists(self.img_overlay.text().strip()):
                img=self.img_overlay.text().strip()
                vf.append(f"movie={img}[logo];[in][logo]overlay=W-w-10:H-h-10[out]")
            if vf: opts['-vf']=','.join(vf)
        if self.source_cb.currentText()=="File":
            player=MediaPlayer(self.file_edit.text().strip(),options=opts or None)
        elif self.source_cb.currentText()=="Screen":
            player=MediaPlayer(':0.0',format='x11grab',options={'framerate':'30',**opts})
        else:
            player=MediaPlayer('/dev/video0',format='v4l2',options={'framerate':'30',**opts})
        pc=RTCPeerConnection()
        if player.audio: pc.addTrack(player.audio)
        if player.video: pc.addTrack(player.video)
        self.log("[WHIP] Creating offer and gathering ICE…")
        offer=await pc.createOffer(); await pc.setLocalDescription(offer)
        evt=asyncio.Event()
        @pc.on('icegatheringstatechange')
        def _on():
            if pc.iceGatheringState=='complete': evt.set()
        await evt.wait()
        self.log("[WHIP] Sending SDP…")
        gov={'Content-Type':'application/sdp'}
        resp=requests.post(whip_url,data=pc.localDescription.sdp,headers=gov,verify=False)
        if resp.status_code not in (200,201):
            self.log(f"[WHIP] HTTP {resp.status_code}: {resp.text}"); return
        loc=resp.headers.get('Location')
        self.log(f"[WHIP] Session: {loc}")
        answer=RTCSessionDescription(sdp=resp.text,type='answer')
        await pc.setRemoteDescription(answer)
        self.log("[WHIP] Streaming started.")
        try:
            while pc.connectionState!='closed': await asyncio.sleep(1)
        finally:
            if loc: requests.delete(loc)

    def stop_stream(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.log("🛑 Stopped.")
        else:
            self.log("🛑 No active ffmpeg.")

if __name__=='__main__':
    app=QApplication(sys.argv)
    w=StreamPublisher()
    w.resize(900,700)
    w.show()
    sys.exit(app.exec_())
