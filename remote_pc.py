"""
RemotePC v3 - 手机浏览器远程控制电脑
新增：音量±/静音 + 截屏发送到手机 + 关闭窗口 + 显示桌面
"""

import os
import sys
import json
import socket
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime
import base64

# ============ 依赖检查 ============
def ensure_deps():
    required = {"flask": "flask", "pystray": "pystray", "PIL": "Pillow"}
    missing = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + missing + ["-q"]
        )

ensure_deps()

from flask import Flask, render_template_string, request, jsonify
import pystray
from pystray import MenuItem as Item, Icon
from PIL import Image, ImageDraw
import ctypes
from ctypes import wintypes

# ============ 常量 ============
APP_NAME = "RemotePC"
VERSION = "3.0.0"
CONFIG_DIR = Path(os.environ.get("APPDATA", ".")) / "RemotePC"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_PORT = 8976
SCREENSHOT_DIR = CONFIG_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ============ 配置 ============
class Config:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.data = {
            "port": DEFAULT_PORT,
            "auto_start": False,
            "minimize_to_tray": True,
            "log": [],
        }
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def add_log(self, msg):
        ts = datetime.now().strftime("%m-%d %H:%M:%S")
        self.data["log"].append(f"[{ts}] {msg}")
        if len(self.data["log"]) > 80:
            self.data["log"] = self.data["log"][-80:]
        self.save()

# ============ 开机自启动 ============
def set_auto_start(enable: bool):
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        if enable:
            if getattr(sys, "frozen", False):
                val = f'"{sys.executable}"'
            else:
                val = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, val)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def check_auto_start() -> bool:
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ
        )
        val, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return False

# ============ 网络 / 系统 ============
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def do_shutdown():
    os.system("shutdown /s /t 3 /f")

def do_restart():
    os.system("shutdown /r /t 3 /f")

def do_hibernate():
    os.system("shutdown /h")

def do_sleep():
    ctypes.windll.powrprof.SetSuspendState(0, 1, 0)

def do_lock():
    ctypes.windll.user32.LockWorkStation()

# ============ 音量控制 ============
def volume_up():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.keybd_event(0xAF, 0, 0, 0)
    user32.keybd_event(0xAF, 0, 2, 0)

def volume_down():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.keybd_event(0xAE, 0, 0, 0)
    user32.keybd_event(0xAE, 0, 2, 0)

def volume_mute():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.keybd_event(0xAD, 0, 0, 0)
    user32.keybd_event(0xAD, 0, 2, 0)

# ============ 窗口控制 ============
def close_active_window():
    """Alt + F4"""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.keybd_event(0x12, 0, 0, 0)   # Alt
    user32.keybd_event(0x73, 0, 0, 0)   # F4
    user32.keybd_event(0x73, 0, 2, 0)
    user32.keybd_event(0x12, 0, 2, 0)

def show_desktop():
    """Win + D"""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.keybd_event(0x5B, 0, 0, 0)
    user32.keybd_event(0x44, 0, 0, 0)
    user32.keybd_event(0x44, 0, 2, 0)
    user32.keybd_event(0x5B, 0, 2, 0)

# ============ 截屏并发送到手机 ============
def take_screenshot():
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = SCREENSHOT_DIR / f"screenshot_{ts}.png"
        im.save(fn, "PNG")

        # 转 base64
        import io
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        return None

# ============ 网页前端 ============
HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>RemotePC</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#08080e;--s1:#10101c;--s2:#181828;
  --cyan:#00e5ff;--purple:#7c4dff;--red:#ff1744;
  --green:#00e676;--orange:#ff9100;
  --txt:#e0e0ee;--dim:#5a5a7a;--r:18px;
}
html,body{min-height:100vh;background:var(--bg);color:var(--txt);font-family:-apple-system,"SF Pro","Segoe UI",sans-serif}
.wrap{max-width:400px;margin:0 auto;padding:16px 14px 48px}
.hdr{text-align:center;padding:28px 0 20px}
.hdr h1{font-size:30px;font-weight:800;letter-spacing:-1px;background:linear-gradient(135deg,var(--cyan),var(--purple));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.card{background:var(--s1);border-radius:var(--r);padding:18px;margin-bottom:14px}
.stat{display:flex;align-items:center;gap:14px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--green);animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.4}}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn{border:none;border-radius:var(--r);padding:18px 10px;cursor:pointer;color:#fff;font-size:13px;font-weight:700;display:flex;flex-direction:column;align-items:center;gap:6px}
.btn:active{transform:scale(.94)}
.btn .ico{font-size:30px}

.b-sleep{background:linear-gradient(135deg,#1565c0,#0d47a1)}
.b-hibernate{background:linear-gradient(135deg,#6a1b9a,#4a148c)}
.b-lock{background:linear-gradient(135deg,#00838f,#006064)}
.b-restart{background:linear-gradient(135deg,#ef6c00,#e65100)}
.b-shutdown{grid-column:1/-1;background:linear-gradient(135deg,#c62828,#b71c1c);padding:22px}
.b-volume-up{background:linear-gradient(135deg,#43a047,#1b5e20)}
.b-volume-down{background:linear-gradient(135deg,#0288d1,#01579b)}
.b-volume-mute{background:linear-gradient(135deg,#f57c00,#e65100)}
.b-close-win{background:linear-gradient(135deg,#ff5722,#e64a19)}
.b-show-desktop{background:linear-gradient(135deg,#9c27b0,#7b1fa2)}
.b-screenshot{background:linear-gradient(135deg,#2196f3,#1565c0);grid-column:1/-1;padding:22px}

#screenshotImg{max-width:100%;border-radius:12px;margin-top:10px;display:none}
.ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);backdrop-filter:blur(10px);z-index:100;justify-content:center;align-items:center}
.ov.on{display:flex}
.cbox{background:var(--s1);border-radius:22px;padding:30px 26px;max-width:310px;width:88%;text-align:center}
.cacts{display:flex;gap:8px}
.cacts button{flex:1;padding:13px;border:none;border-radius:12px;font-weight:700}
.toast{position:fixed;bottom:28px;left:50%;transform:translateX(-50%) translateY(120px);background:var(--s2);padding:12px 26px;border-radius:12px;transition:.4s}
.toast.show{transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr"><h1>RemotePC</h1><div class="sub">手机远程控制面板</div></div>
  <div class="card stat"><div class="dot"></div><div><b>{{hostname}}</b><br>{{ip}}:{{port}} 在线</div></div>
  
  <img id="screenshotImg">
  
  <div class="grid">
    <button class="btn b-volume-up" id="volUp"><span class="ico">🔊</span>音量+</button>
    <button class="btn b-volume-down" id="volDown"><span class="ico">🔉</span>音量-</button>
    <button class="btn b-volume-mute" id="volMute"><span class="ico">🔇</span>静音</button>
    <button class="btn b-close-win" id="closeWin"><span class="ico">🪟</span>关闭窗口</button>
    <button class="btn b-show-desktop" id="showDesktop"><span class="ico">🖥️</span>显示桌面</button>
    <button class="btn b-sleep" id="sleep"><span class="ico">😴</span>休眠</button>
    <button class="btn b-hibernate" id="hibernate"><span class="ico">🌙</span>睡眠</button>
    <button class="btn b-lock" id="lock"><span class="ico">🔒</span>锁定</button>
    <button class="btn b-restart" id="restart"><span class="ico">🔄</span>重启</button>
    <button class="btn b-shutdown" id="shutdown"><span class="ico">⏻</span>关机</button>
    <button class="btn b-screenshot" id="screenshot"><span class="ico">📸</span>截屏发送到手机</button>
  </div>
</div>

<div class="ov" id="ov">
  <div class="cbox">
    <div id="ci">⚠️</div>
    <h2 id="ct">确认操作</h2>
    <p id="cd"></p>
    <div class="cacts">
      <button class="b-no" id="btnCancel">取消</button>
      <button class="b-yes" id="btnConfirm">确认</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let currentAction = ""
const imgEl = document.getElementById("screenshotImg")

const actionMap = {
  volUp:       { act:"volume_up",    name:"音量+" },
  volDown:     { act:"volume_down",  name:"音量-" },
  volMute:     { act:"volume_mute",  name:"静音" },
  closeWin:    { act:"close_window", name:"关闭当前窗口" },
  showDesktop: { act:"show_desktop", name:"显示桌面" },
  sleep:       { act:"sleep",        name:"休眠" },
  hibernate:   { act:"hibernate",    name:"睡眠" },
  lock:        { act:"lock",         name:"锁定" },
  restart:     { act:"restart",      name:"重启" },
  shutdown:    { act:"shutdown",     name:"关机" },
  screenshot:  { act:"screenshot",   name:"截屏" }
}

Object.keys(actionMap).forEach(id => {
  document.getElementById(id).onclick = () => {
    let a = actionMap[id]
    currentAction = a.act
    document.getElementById("cd").textContent = "确定要执行：" + a.name + "？"
    ov.classList.add("on")
  }
})

btnCancel.onclick = () => ov.classList.remove("on")
btnConfirm.onclick = () => {
  ov.classList.remove("on")
  send(currentAction)
}

function send(act) {
  fetch("/api/action", {
    method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:"action="+encodeURIComponent(act)
  }).then(r=>r.json()).then(j=>{
    toast(j.msg)
    if (act === "screenshot" && j.img) {
      imgEl.src = j.img
      imgEl.style.display = "block"
    }
  })
}

function toast(msg){
  let t = document.getElementById("toast")
  t.textContent = msg
  t.classList.add("show")
  setTimeout(()=>t.classList.remove("show"),2000)
}
</script>
</body>
</html>
"""

# ============ Flask 服务 ============
class WebServer:
    def __init__(self, cfg: Config, log_cb=None):
        self.cfg = cfg
        self.log_cb = log_cb
        self.app = Flask(__name__)
        self._routes()

    def _log(self, msg):
        self.cfg.add_log(msg)
        if self.log_cb: self.log_cb(msg)

    def _routes(self):
        @self.app.route("/")
        def index():
            return render_template_string(HTML_PAGE,
                hostname=socket.gethostname(),
                ip=get_local_ip(), port=self.cfg["port"], ver=VERSION)

        @self.app.route("/api/action", methods=["POST"])
        def api():
            act = request.form.get("action", "").strip()
            ip = request.remote_addr
            self._log(f"{ip} -> {act}")

            func_map = {
                "shutdown":      ("关机", do_shutdown),
                "restart":       ("重启", do_restart),
                "sleep":         ("休眠", do_sleep),
                "hibernate":     ("睡眠", do_hibernate),
                "lock":          ("锁定", do_lock),
                "volume_up":     ("音量+", volume_up),
                "volume_down":   ("音量-", volume_down),
                "volume_mute":   ("静音", volume_mute),
                "close_window":  ("关闭窗口", close_active_window),
                "show_desktop":  ("显示桌面", show_desktop),
                "screenshot":    ("截屏", take_screenshot),
            }

            if act not in func_map:
                return jsonify(ok=False, msg="未知操作")

            name, fn = func_map[act]
            if act == "screenshot":
                img = fn()
                if img:
                    return jsonify(ok=True, msg="截屏成功", img=img)
                else:
                    return jsonify(ok=False, msg="截屏失败")
            else:
                fn()
                return jsonify(ok=True, msg=name + "成功")

    def start(self):
        port = self.cfg["port"]
        threading.Thread(target=lambda: self.app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False), daemon=True).start()
        self._log(f"服务启动 端口:{port}")

# ============ GUI 界面 ============
class AppGUI:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.srv = None
        self.tray = None
        self.root = tk.Tk()
        self.root.title(f"RemotePC v{VERSION}")
        self.root.geometry("460x540")
        self.root.configure(bg="#08080e")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._ui()
        self._start_server()
        self._start_tray()

    def _ui(self):
        main = tk.Frame(self.root, bg="#08080e", padx=22, pady=18)
        main.pack(fill="both", expand=True)
        tk.Label(main, text="RemotePC", font=("Segoe UI", 28, "bold"), fg="#00e5ff", bg="#08080e").pack(anchor="w")
        ip = get_local_ip()
        port = self.cfg["port"]
        addr = f"http://{ip}:{port}"
        tk.Label(main, text=addr, font=("Consolas", 12), fg="#00e5ff", bg="#10101c", padx=10, pady=8).pack(fill="x", pady=10)

    def _start_server(self):
        self.srv = WebServer(self.cfg, log_cb=lambda m: None)
        self.srv.start()

    def _tray_img(self):
        img = Image.new("RGBA", (64,64), (0,0,0,0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([2,2,62,62], fill=(0,229,255,220), radius=14)
        return img

    def _start_tray(self):
        menu = pystray.Menu(Item("显示", self._show), Item("退出", self._quit))
        self.tray = Icon(APP_NAME, self._tray_img(), APP_NAME, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _show(self, *a): self.root.deiconify()
    def _quit(self, *a): self.tray.stop(); self.root.destroy()
    def _on_close(self): self.root.withdraw()
    def run(self): self.root.mainloop()

def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    cfg = Config()
    AppGUI(cfg).run()

if __name__ == "__main__":
    main()