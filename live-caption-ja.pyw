# Auto-generated single-file launcher for live-caption (Japanese mode).
# Standalone Python launcher incorporating the live-caption core and ASR fixes.
import os as _lc_os
import sys as _lc_sys
from pathlib import Path as _LCPath

_LC_ROOT = _LCPath(__file__).resolve().parent
_lc_os.chdir(_LC_ROOT)

# A .pyw double-click may use the system Python. Re-exec once into the project's venv,
# where all required packages are already installed.
if _lc_os.environ.get("_LIVE_CAPTION_PYW_BOOTSTRAPPED") != "1":
    # CLI and --list/--help require a real console. Only GUI double-click
    # should re-exec into pythonw.exe (which has no standard output console).
    _lc_cli = any(arg in ("--console", "--list", "--help", "-h") for arg in _lc_sys.argv[1:])
    _lc_python = _LC_ROOT / ".venv" / "Scripts" / ("python.exe" if _lc_cli else "pythonw.exe")
    if not _lc_python.is_file():
        try:
            import ctypes as _lc_ctypes
            _lc_ctypes.windll.user32.MessageBoxW(
                0,
                "Project virtual environment not found:\n" + str(_lc_python) + "\n\nRun setup first.",
                "live-caption",
                0x10,
            )
        finally:
            raise SystemExit(1)
    _lc_env = dict(_lc_os.environ)
    _lc_env["_LIVE_CAPTION_PYW_BOOTSTRAPPED"] = "1"
    _lc_os.execve(
        str(_lc_python),
        [str(_lc_python), str(_LC_ROOT / _LCPath(__file__).name), *_lc_sys.argv[1:]],
        _lc_env,
    )

# pythonw has no console when launched by double-click. Keep ConsoleSink safe by
# redirecting missing standard streams to a UTF-8 log file instead of None.
_lc_log_stream = None
if _lc_sys.stdout is None or _lc_sys.stderr is None:
    _lc_log_stream = open(_LC_ROOT / "live-caption-pyw.log", "a", encoding="utf-8", buffering=1)
    if _lc_sys.stdout is None:
        _lc_sys.stdout = _lc_log_stream
    if _lc_sys.stderr is None:
        _lc_sys.stderr = _lc_log_stream
for _lc_stream in (_lc_sys.stdout, _lc_sys.stderr):
    if hasattr(_lc_stream, "reconfigure"):
        _lc_stream.reconfigure(errors="backslashreplace")
if _lc_sys.stdin is None:
    _lc_sys.stdin = open(_lc_os.devnull, "r", encoding="utf-8")

# Double-click defaults: Japanese recognition and transcript output.
# Preserve genuine user arguments: injected defaults must not shadow GUI choices.
_LC_ORIGINAL_ARGS = tuple(_lc_sys.argv[1:])
# argparse accepts both --option value and --option=value; preserve either form.
def _lc_has_option(option):
    return any(arg == option or arg.startswith(option + "=") for arg in _lc_sys.argv[1:])

if not _lc_has_option("--lang"):
    _lc_sys.argv += ["--lang", "ja"]
if not _lc_has_option("--out"):
    _lc_sys.argv += ["--out", str(_LC_ROOT / "transcript.txt")]

# ==================== embedded original live_caption.py ====================
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
live_caption.py — 实时把「电脑正在播放的声音」转成字幕（本地 GPU，免费）

  采集: WASAPI loopback (pyaudiowpatch)，不需要 VB-Cable / 立体声混音
  识别: Qwen3-ASR-1.7B GGUF + llama.cpp（NVIDIA GPU）；Silero VAD 切句
  显示: 屏幕底部悬浮字幕条（可拖动、可调字号），同时也打到终端 / 文件

用法:
  python live_caption.py --lang zh                 # 中文字幕（Qwen3-ASR）
  python live_caption.py --lang zh --hotwords "BLG,T1,峡谷先锋,纳什男爵"   # 加专有名词（也可写在 hotwords.txt）
  python live_caption.py --list                    # 列出可用的输出设备
  python live_caption.py --device 7                # 指定输出设备(index)
  python live_caption.py --console                 # 不要悬浮窗，只在终端显示
  python live_caption.py --out notes.txt --srt notes.srt
字幕窗: 鼠标拖动移动位置；滚轮调字号；右上角 ⚙ 打开设置；× 或 Esc 退出。终端 Ctrl+C 退出。
"""
import argparse
import os
import re
import sys
import time
import threading
import queue
import json
import collections
from urllib.parse import urlencode
from urllib.request import urlopen
from datetime import datetime, timedelta


# The top-level imports run before main()'s exception handler.  In .pyw mode
# pythonw has no console, so surface import failures directly to the user.
try:
    import numpy as np
    import pyaudiowpatch as pyaudio
except Exception:
    import traceback as _lc_traceback
    _lc_import_error = _lc_traceback.format_exc()
    try:
        with open(_LC_ROOT / "live_caption.log", "a", encoding="utf-8") as _lc_error_file:
            _lc_error_file.write("\n===== 启动依赖导入失败 =====\n" + _lc_import_error + "\n")
    except OSError:
        pass
    if _lc_os.name == "nt":
        try:
            import ctypes as _lc_ctypes
            _lc_ctypes.windll.user32.MessageBoxW(
                0, "启动依赖导入失败，详情见 live_caption.log\n\n" + _lc_import_error[-1500:],
                "live-caption", 0x10)
        except Exception:
            pass
    raise SystemExit(1)

SR = 16000  # 模型输入采样率


# ============================ 音频采集 ============================
def list_devices(pa):
    print("== WASAPI 输出设备 (loopback 可用) ==")
    try:
        api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        print("找不到 WASAPI"); return
    default = pa.get_device_info_by_index(api["defaultOutputDevice"])["name"]
    for d in pa.get_loopback_device_info_generator():
        mark = "  <- 当前默认" if default in d["name"] else ""
        print(f"  [{d['index']}] {d['name']}  ({int(d['defaultSampleRate'])} Hz, {d['maxInputChannels']} ch){mark}")
    print("用 --device <index> 指定；不指定则跟随系统默认输出设备。")


def get_loopback_device(pa, index=None):
    api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    out = pa.get_device_info_by_index(api["defaultOutputDevice"] if index is None else index)
    if out.get("isLoopbackDevice"):
        return out
    for lb in pa.get_loopback_device_info_generator():
        if out["name"] in lb["name"]:
            return lb
    raise RuntimeError(f"找不到 {out['name']} 的 loopback 设备，用 --list 看看")


class Resampler:
    def __init__(self, sr_in, sr_out=SR):
        self.sr_in, self.sr_out = sr_in, sr_out
        self.soxr = None
        if sr_in != sr_out:
            import soxr
            self.soxr = soxr.ResampleStream(sr_in, sr_out, 1, dtype="float32", quality="HQ")

    def __call__(self, mono):
        if self.sr_in == self.sr_out:
            return mono
        return self.soxr.resample_chunk(mono)

    def flush(self):
        if self.soxr is not None:
            return self.soxr.resample_chunk(np.empty(0, dtype=np.float32), last=True)
        return np.empty(0, dtype=np.float32)


class AudioFrameProcessor:
    """Decode, downmix and resample PCM only on the consumer thread."""
    def __init__(self, sample_rate, channels):
        self.rate = int(sample_rate)
        self.channels = int(channels)
        if self.rate <= 0 or self.channels <= 0:
            raise ValueError("Invalid audio device parameters")
        self.resampler = Resampler(self.rate)
        self._chosen_channel = None

    def __call__(self, packet):
        if isinstance(packet, np.ndarray):
            return packet.astype(np.float32, copy=False)
        raw, frames = packet
        if len(raw) != int(frames) * self.channels * 4:
            raise ValueError("WASAPI callback returned a truncated PCM frame")
        audio = np.frombuffer(raw, dtype='<f4').reshape(int(frames), self.channels)
        if self.channels == 1:
            mono = audio[:, 0]
        else:
            left, right = audio[:, 0], audio[:, 1]
            mono = audio.mean(axis=1)
            mean_energy = float(np.mean(left * left + right * right)) * .5
            mixed_energy = float(np.mean(mono * mono))
            if mean_energy > 1e-8 and mixed_energy < .08 * mean_energy:
                l_energy, r_energy = float(np.mean(left * left)), float(np.mean(right * right))
                if self._chosen_channel is None:
                    self._chosen_channel = 0 if l_energy >= r_energy else 1
                mono = audio[:, self._chosen_channel]
            else:
                self._chosen_channel = None
        mono = np.nan_to_num(mono, copy=True).astype(np.float32, copy=False)
        return self.resampler(mono)

    def flush(self):
        return self.resampler.flush()


def start_capture(pa, dev, audio_q, stop, overflowed=None, capture_errors=None):
    """Only validate size and enqueue immutable bytes inside PortAudio callback."""
    ch = int(dev['maxInputChannels'])
    sr = int(dev['defaultSampleRate'])
    def cb(in_data, frame_count, time_info, status):
        if stop.is_set():
            return (None, pyaudio.paComplete)
        try:
            if in_data is None or len(in_data) != frame_count * ch * 4:
                raise ValueError("WASAPI supplied incomplete float32 PCM")
            audio_q.put_nowait((in_data, frame_count))
        except queue.Full:
            if overflowed is not None:
                overflowed.set()
            stop.set()
            return (None, pyaudio.paComplete)
        except Exception as exc:
            if capture_errors is not None:
                capture_errors.append(exc)
            stop.set()
            return (None, pyaudio.paComplete)
        if status:
            if overflowed is not None:
                overflowed.set()
            stop.set()
            return (None, pyaudio.paComplete)
        return (None, pyaudio.paContinue)
    stream = pa.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True,
                     input_device_index=dev['index'],
                     frames_per_buffer=max(64, int(sr * .05)),
                     stream_callback=cb)
    try:
        stream.start_stream()
    except BaseException:
        stream.close()
        raise
    return stream



# ============================ 输出：终端 / 文件 ============================
def fmt_ts(sec):
    total_ms = max(0, round(sec * 1000))
    total_s, ms = divmod(total_ms, 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _read_srt_append_state(path):
    """Resume the last valid SRT cue and its end time without loading the file."""
    last_index, last_end = 0, 0.0
    if not path:
        return last_index, last_end
    pattern = re.compile(r"^\d{2,}:\d{2}:\d{2},\d{3} --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})$")
    try:
        with open(path, encoding="utf-8-sig") as existing:
            previous = ""
            for line in existing:
                match = pattern.fullmatch(line.strip())
                if match and previous.strip().isdigit():
                    last_index = max(last_index, int(previous.strip()))
                    h, m, s, ms = map(int, match.groups())
                    last_end = max(last_end, h * 3600 + m * 60 + s + ms / 1000)
                previous = line
    except FileNotFoundError:
        pass
    return last_index, last_end


class ConsoleSink:
    def __init__(self, out_path=None, srt_path=None, show_partial=True):
        self.n_srt, self.srt_offset = _read_srt_append_state(srt_path)
        self._srt_last_end = self.srt_offset
        self.out = open(out_path, "a", encoding="utf-8") if out_path else None
        try:
            self.srt = open(srt_path, "a", encoding="utf-8") if srt_path else None
        except BaseException:
            if self.out:
                self.out.close()
            raise
        self.show_partial = show_partial
        self.t0 = time.time()
        self._plen = 0

    def set_capture_origin(self, capture_time):
        # SRT zero must be the first audio capture, not model warmup/startup.
        self.t0 = float(capture_time)

    def partial(self, text):
        if not self.show_partial:
            return
        line = "  … " + text
        sys.stdout.write("\r" + line + " " * max(0, self._plen - len(line)))
        sys.stdout.flush()
        self._plen = len(line)

    def final(self, text, t_start, t_end):
        errors = []
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            sys.stdout.write("\r" + " " * self._plen + "\r")
            self._plen = 0
            print(f"[{stamp}] {text}", flush=True)
        except Exception as exc:
            errors.append(exc)
        if self.out:
            try:
                self.out.write(f"[{stamp}] {text}\n"); self.out.flush()
            except Exception as exc:
                errors.append(exc)
        if self.srt:
            try:
                # A hard-cut may reuse 250ms of PCM in the following segment.
                # The cue timeline must nevertheless remain chronological.
                rel_start = max(self._srt_last_end, self.srt_offset + t_start - self.t0)
                rel_end = max(rel_start + .001, self.srt_offset + t_end - self.t0)
                self.n_srt += 1
                self.srt.write(f"{self.n_srt}\n{fmt_ts(rel_start)} --> {fmt_ts(rel_end)}\n{text}\n\n")
                self.srt.flush()
                self._srt_last_end = rel_end
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise OSError("Subtitle persistence failed") from errors[0]

    def close(self):
        errors = []
        for f in (self.out, self.srt):
            if f:
                try:
                    f.close()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise OSError("Subtitle file close failed") from errors[0]


# ============================ 输出：悬浮字幕条 ============================
class OverlaySink:
    """屏幕底部的视频式字幕条：最新一行正常显示，上一行缩小变灰；没新话时整体渐隐。
    必须在主线程创建/运行；识别线程通过 queue 投递文本。"""
    MAX_UI_EVENTS = 1024
    MAX_UI_DRAIN = 128

    def __init__(self, font_size=30, width_frac=0.8, bottom=70, alpha=0.82, lines=2, hold=6.0, font="Microsoft YaHei"):
        import tkinter as tk
        import tkinter.font as tkfont
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        self.tk = tk
        # UI state is non-durable. Never allow a stalled Tk main loop to exhaust RAM.
        self.q = queue.Queue(maxsize=self.MAX_UI_EVENTS)
        self._close_requested = threading.Event()
        self.settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live-caption-settings.json")
        self._initial_settings = {
            "asr_model": "Q8_0",
            "asr_language": "ja",  # 首次启动默认日语；旧配置显式 auto 仍然保留
            "context_enabled": True,
            "context_history": 1,
            "context_chars": 80,
            "llama_ctx": 8192,
            "slots": 2,
            "max_tokens": 200,
            "temperature": 0.0,
            "partial_every": 0.4,
            "silence": 0.85,
            "vad_win": 0.2,
            "vad_threshold": 0.45,
            "vad_min_speech_ms": 100,
            "vad_min_silence_ms": 200,
            "vad_speech_pad_ms": 100,
            "min_seg": 1.0,
            "max_seg": 6.0,
            "max_gain": 60.0,
            "asr_device": -1,
            "asr_backend": 'llama',
            "asr_model_path": '',
            "asr_mmproj_path": '',
            "asr_llama_server_path": '',
            "asr_llama_port": 8765,
            "asr_llama_url": '',
            "asr_hotwords": '',
            "asr_hotwords_file": '',
            "asr_debug_dir": '',
            "asr_output_txt": '',
            "asr_output_srt": '',
            "asr_no_partial": False,
            "font_name": font,
            "font_size": font_size,
            "alpha": alpha,
            "width_frac": width_frac,
            "window_height": 0,
            "bottom": bottom,
            "hold": hold,
            "auto_hide": True,
            "current_color": "#ffffff",
            "translation_color": "#d0d0d0",
            "previous_color": "#9a9a9a",
            "background_color": "#000000",
            "topmost": True,
            "show_previous": True,
            "history_count": 1,
            "current_bold": True,
        }
        cfg = self._load_settings(self._initial_settings)

        def number(key, cast, low, high):
            try:
                value = cast(cfg[key])
                if not np.isfinite(value):
                    raise ValueError(f"Nonfinite setting: {key}")
            except (TypeError, ValueError, OverflowError):
                value = cast(self._initial_settings[key])
            return max(low, min(high, value))

        def flag(key):
            value = cfg[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() not in ("", "0", "false", "no", "off")
            return bool(value)

        font_value = cfg["font_name"]
        model_value = str(cfg.get("asr_model", "Q8_0")).upper()
        self.asr_model = model_value if model_value in ("Q8_0", "BF16") else "Q8_0"
        language_value = str(cfg.get("asr_language", "ja")).strip().lower()
        self.asr_language = language_value if language_value in ("auto", "ja", "zh", "en") else "ja"
        self.context_enabled = flag("context_enabled")
        self.context_history = number("context_history", int, 1, 5)
        self.context_chars = number("context_chars", int, 40, 1000)
        self.llama_ctx = number("llama_ctx", int, 2048, 16384)
        self.slots = number("slots", int, 1, 4)
        self.max_tokens = number("max_tokens", int, 64, 1024)
        self.temperature = number("temperature", float, 0.0, 1.0)
        self.partial_every = number("partial_every", float, 0.2, 2.0)
        self.silence = number("silence", float, 0.2, 2.0)
        self.vad_win = number("vad_win", float, 0.1, 1.0)
        self.vad_threshold = number("vad_threshold", float, 0.1, 0.9)
        self.vad_min_speech_ms = number("vad_min_speech_ms", int, 50, 1000)
        self.vad_min_silence_ms = number("vad_min_silence_ms", int, 50, 2000)
        self.vad_speech_pad_ms = number("vad_speech_pad_ms", int, 0, 1000)
        self.min_seg = number("min_seg", float, 0.2, 5.0)
        self.max_seg = number("max_seg", float, 0.0, 30.0)
        self.max_gain = number("max_gain", float, 1.0, 100.0)
        self.asr_device = number("asr_device", int, -1, 1000)
        self.asr_llama_port = number("asr_llama_port", int, 1, 65535)
        backend_name = str(cfg.get("asr_backend", "llama")).strip().lower()
        self.asr_backend = backend_name if backend_name in ("llama", "hf") else "llama"
        self.asr_no_partial = flag("asr_no_partial")
        if self.asr_backend == "hf":
            self.temperature = 0.0
        for field in ("asr_model_path", "asr_mmproj_path", "asr_llama_server_path",
                      "asr_llama_url", "asr_hotwords", "asr_hotwords_file",
                      "asr_debug_dir", "asr_output_txt", "asr_output_srt"):
            setattr(self, field, str(cfg.get(field) or "").strip()[:2048])
        self.font_name = font_value.strip() if isinstance(font_value, str) and font_value.strip() else str(font)
        self.font_size = number("font_size", int, 12, 80)
        self.alpha = number("alpha", float, 0.20, 1.0)
        self.width_frac = number("width_frac", float, 0.30, 1.0)
        self.window_height = number("window_height", int, 0, 10000)
        self.bottom = number("bottom", int, 0, 500)
        self.hold = number("hold", float, 1.0, 20.0)
        self.auto_hide = flag("auto_hide")
        self.current_color = str(cfg["current_color"])
        self.translation_color = str(cfg["translation_color"])
        self.previous_color = str(cfg["previous_color"])
        self.background_color = str(cfg["background_color"])
        self.topmost = flag("topmost")
        self.show_previous = flag("show_previous")
        self.history_count = number("history_count", int, 0, 5)
        self.current_bold = flag("current_bold")
        self.settings_win = None
        self.asr_settings_win = None
        self._setting_vars = None
        self._settings_save_job = None
        self.finals = []          # 当前 final + 可配置数量的历史 final
        self.partial_text = ""
        self.translation_text = ""
        self.translation_source = ""
        self.caption_seen = False    # 启动状态条在首句字幕出现前保持可见
        self.last_update = time.time()
        self.fading = False
        self._fade_generation = 0
        self.closed = False
        root = tk.Tk()
        self.root = root
        for attr, fallback in (
            ("current_color", "#ffffff"),
            ("translation_color", "#d0d0d0"),
            ("previous_color", "#9a9a9a"),
            ("background_color", "#000000"),
        ):
            setattr(self, attr, self._safe_color(getattr(self, attr), fallback))
        root.overrideredirect(True)
        root.attributes("-topmost", self.topmost)
        root.attributes("-alpha", self.alpha)
        root.configure(bg=self.background_color)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.w = int(sw * self.width_frac)
        self.sh = sh

        self.f_cur = tkfont.Font(family=self.font_name, size=self.font_size,
                                 weight="bold" if self.current_bold else "normal")
        self.f_trans = tkfont.Font(family=self.font_name, size=max(12, int(self.font_size * 0.82)), weight="normal")
        self.f_prev = tkfont.Font(family=self.font_name, size=max(10, int(self.font_size * 0.68)), weight="normal")
        self.l_prev = tk.Label(root, text="", fg=self.previous_color, bg=self.background_color, justify="center", anchor="s",
                               font=self.f_prev, wraplength=self.w - 40, padx=20)
        self.l_cur = tk.Label(root, text="", fg=self.current_color, bg=self.background_color, justify="center", anchor="n",
                              font=self.f_cur, wraplength=self.w - 40, padx=20)
        self.l_trans = tk.Label(root, text="", fg=self.translation_color, bg=self.background_color, justify="center", anchor="n",
                                font=self.f_trans, wraplength=self.w - 40, padx=20)
        if self.show_previous:
            self.l_prev.pack(side="top", fill="x", pady=(8, 0))
        self.l_cur.pack(side="top", fill="x", pady=(2, 0))
        self.l_trans.pack(side="top", fill="x", pady=(0, 10))
        self.hint = tk.Label(root, text="拖动=移动  边缘/角=缩放  滚轮=字号  ⚙=设置  Esc=退出",
                             fg="#777777", bg=self.background_color, font=(self.font_name, 9))
        self.hint.place(relx=1.0, rely=0.0, x=-86, anchor="ne")
        self.window_controls = tk.Frame(root, bg=self.background_color, bd=0, highlightthickness=0)
        self.window_controls.place(relx=1.0, rely=0.0, x=-6, y=2, anchor="ne")
        self.settings_btn = tk.Button(self.window_controls, text="⚙", command=self.open_settings,
                                      fg="#b0b0b0", bg=self.background_color,
                                      activeforeground="#ffffff", activebackground="#333333", relief="flat",
                                      bd=0, highlightthickness=0, padx=6, pady=0,
                                      font=("Segoe UI Symbol", 11), cursor="hand2")
        self.settings_btn.pack(side="left", padx=(0, 8))
        self.close_btn = tk.Button(self.window_controls, text="×", command=self.close,
                                   fg="#b0b0b0", bg=self.background_color,
                                   activeforeground="#ffffff", activebackground="#333333", relief="flat",
                                   bd=0, highlightthickness=0, padx=6, pady=0,
                                   font=("Segoe UI", 13, "bold"), cursor="hand2")
        self.close_btn.pack(side="left")
        root.after(5000, self.hint.place_forget)
        self._relayout(first=True)

        for wdg in (root, self.l_prev, self.l_cur, self.l_trans):
            wdg.bind("<Motion>", self._resize_cursor)
            wdg.bind("<ButtonPress-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
            wdg.bind("<ButtonRelease-1>", self._drag_end)
            wdg.bind("<MouseWheel>", self._wheel)
        root.bind("<Escape>", lambda e: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(60, self._poll)

    def _load_settings(self, defaults):
        cfg = dict(defaults)
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for key in defaults:
                    if key in saved:
                        cfg[key] = saved[key]
        except (OSError, ValueError, TypeError):
            pass
        return cfg

    def _safe_color(self, color, fallback):
        try:
            self.root.winfo_rgb(color)
            return color
        except Exception:
            return fallback

    def _current_settings(self):
        return {
            "asr_model": self.asr_model,
            "asr_language": getattr(self, "asr_language", "ja"),
            "context_enabled": self.context_enabled,
            "context_history": self.context_history,
            "context_chars": self.context_chars,
            "llama_ctx": self.llama_ctx,
            "slots": self.slots,
            "max_tokens": self.max_tokens,
            "temperature": round(self.temperature, 3),
            "partial_every": round(self.partial_every, 3),
            "silence": round(self.silence, 3),
            "vad_win": round(self.vad_win, 3),
            "vad_threshold": round(self.vad_threshold, 3),
            "vad_min_speech_ms": self.vad_min_speech_ms,
            "vad_min_silence_ms": self.vad_min_silence_ms,
            "vad_speech_pad_ms": self.vad_speech_pad_ms,
            "min_seg": round(self.min_seg, 3),
            "max_seg": round(self.max_seg, 3),
            "max_gain": round(self.max_gain, 2),
            "asr_device": self.asr_device,
            "asr_backend": self.asr_backend,
            "asr_model_path": self.asr_model_path,
            "asr_mmproj_path": self.asr_mmproj_path,
            "asr_llama_server_path": self.asr_llama_server_path,
            "asr_llama_port": self.asr_llama_port,
            "asr_llama_url": self.asr_llama_url,
            "asr_hotwords": self.asr_hotwords,
            "asr_hotwords_file": self.asr_hotwords_file,
            "asr_debug_dir": self.asr_debug_dir,
            "asr_output_txt": self.asr_output_txt,
            "asr_output_srt": self.asr_output_srt,
            "asr_no_partial": self.asr_no_partial,
            "font_name": self.font_name,
            "font_size": self.font_size,
            "alpha": round(self.alpha, 3),
            "width_frac": round(self.width_frac, 3),
            "window_height": int(self.window_height),
            "bottom": self.bottom,
            "hold": round(self.hold, 2),
            "auto_hide": self.auto_hide,
            "current_color": self.current_color,
            "translation_color": self.translation_color,
            "previous_color": self.previous_color,
            "background_color": self.background_color,
            "topmost": self.topmost,
            "show_previous": self.show_previous,
            "history_count": self.history_count,
            "current_bold": self.current_bold,
        }

    def _save_settings(self):
        import tempfile
        temporary = None
        try:
            # Serialize before touching the last good file. Replace only after
            # the complete UTF-8 snapshot is flushed and closed on Windows.
            content = json.dumps(self._current_settings(), ensure_ascii=False, indent=2)
            directory = os.path.dirname(os.path.abspath(self.settings_path))
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".caption-settings-", suffix=".tmp",
                                             delete=False) as f:
                temporary = f.name
                f.write(content + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.settings_path)
            temporary = None
        except (OSError, TypeError, ValueError) as e:
            print("保存字幕设置失败:", repr(e), flush=True)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _schedule_save(self):
        if self._settings_save_job is not None:
            try:
                self.root.after_cancel(self._settings_save_job)
            except Exception:
                pass
        self._settings_save_job = self.root.after(350, self._save_settings)

    def _apply_style(self, anchor_to_screen=False, keep_center=False):
        old_center = self.root.winfo_x() + self.root.winfo_width() / 2
        self.root.configure(bg=self.background_color)
        self.root.attributes("-topmost", self.topmost)
        if not self.fading:
            self.root.attributes("-alpha", self.alpha)

        self.f_cur.configure(family=self.font_name, size=self.font_size,
                             weight="bold" if self.current_bold else "normal")
        self.f_trans.configure(family=self.font_name, size=max(12, int(self.font_size * 0.82)))
        self.f_prev.configure(family=self.font_name, size=max(10, int(self.font_size * 0.68)))
        self.hint.configure(bg=self.background_color, font=(self.font_name, 9))
        self.window_controls.configure(bg=self.background_color)
        self.settings_btn.configure(bg=self.background_color)
        self.close_btn.configure(bg=self.background_color)

        sw = self.root.winfo_screenwidth()
        self.w = max(320, int(sw * self.width_frac))
        wrap = max(280, self.w - 40)
        self.l_prev.configure(fg=self.previous_color, bg=self.background_color, wraplength=wrap)
        self.l_cur.configure(fg=self.current_color, bg=self.background_color, wraplength=wrap)
        self.l_trans.configure(fg=self.translation_color, bg=self.background_color, wraplength=wrap)

        for label in (self.l_prev, self.l_cur, self.l_trans):
            label.pack_forget()
        if self.show_previous:
            self.l_prev.pack(side="top", fill="x", pady=(8, 0))
        self.l_cur.pack(side="top", fill="x", pady=(2, 0))
        self.l_trans.pack(side="top", fill="x", pady=(0, 10))

        self._relayout(anchor_to_screen=anchor_to_screen, center_x=old_center if keep_center else None)
        self._render()

    def _settings_changed(self, key=None):
        from tkinter import TclError
        if not self._setting_vars:
            return
        v = self._setting_vars
        if key == "auto_hide":
            self.auto_hide = bool(v["auto_hide"].get())
            if not self.auto_hide:
                # Restore a previously fully transparent window immediately.
                # Also invalidate every pending fade callback.
                self._stop_fade()
                self.root.attributes("-alpha", self.alpha)
            else:
                # Don't disappear instantly when turning the feature back on.
                self.last_update = time.time()
            self._schedule_save()
            return
        if key == "asr_model":
            value = str(v["asr_model"].get()).upper()
            self.asr_model = value if value in ("Q8_0", "BF16") else "Q8_0"
            self._schedule_save()
            return
        if key == "asr_language":
            value = str(v["asr_language"].get()).strip().lower()
            self.asr_language = value if value in ("auto", "ja", "zh", "en") else "ja"
            self._schedule_save()
            return
        if key == "asr_backend":
            selected = str(v["asr_backend"].get()).strip().lower()
            self.asr_backend = selected if selected in ("llama", "hf") else "llama"
            if self.asr_backend == "hf":
                self.temperature = 0.0
                v["temperature"].set(0.0)
            self._schedule_save()
            return
        if key == "asr_no_partial":
            self.asr_no_partial = bool(v["asr_no_partial"].get())
            self._schedule_save()
            return
        _asr_text_fields = ("asr_model_path", "asr_mmproj_path", "asr_llama_server_path",
                            "asr_llama_url", "asr_hotwords", "asr_hotwords_file",
                            "asr_debug_dir", "asr_output_txt", "asr_output_srt")
        if key in _asr_text_fields:
            setattr(self, key, str(v[key].get()).strip()[:2048])
            self._schedule_save()
            return
        # Commit only the edited widget: while a user types, another Spinbox
        # may temporarily contain an empty/invalid value. One bad field must
        # not prevent a valid ASR setting from being persisted.
        recognition_fields = {
            "context_history": (int, 1, 5), "context_chars": (int, 40, 1000),
            "llama_ctx": (int, 2048, 16384), "slots": (int, 1, 4),
            "max_tokens": (int, 64, 1024), "temperature": (float, 0.0, 1.0),
            "partial_every": (float, 0.2, 2.0), "silence": (float, 0.2, 2.0),
            "vad_win": (float, 0.1, 1.0), "vad_threshold": (float, 0.1, 0.9),
            "vad_min_speech_ms": (int, 50, 1000),
            "vad_min_silence_ms": (int, 50, 2000),
            "vad_speech_pad_ms": (int, 0, 1000),
            "min_seg": (float, 0.2, 5.0), "max_seg": (float, 0.0, 30.0),
            "max_gain": (float, 1.0, 100.0),
            "asr_device": (int, -1, 1000),
            "asr_llama_port": (int, 1, 65535),
        }
        if key == "temperature" and self.asr_backend == "hf":
            v["temperature"].set(0.0)
        if key in recognition_fields or key in ("context_enabled", "_asr_all"):
            keys = ("context_enabled", *recognition_fields) if key == "_asr_all" else (key,)
            updated = {}
            for field in keys:
                try:
                    if field == "context_enabled":
                        value = bool(v[field].get())
                    else:
                        cast, low, high = recognition_fields[field]
                        value = cast(v[field].get())
                        if not np.isfinite(value):
                            continue
                        if field == "max_seg":
                            # Only exactly zero disables time-based segmentation.
                            value = 0.0 if value == 0 else max(2.0, min(high, value))
                        else:
                            value = max(low, min(high, value))
                except (ValueError, TypeError, OverflowError, TclError):
                    # Restore the last valid value instead of leaving an
                    # unparseable IntVar/DoubleVar visible after focus-out.
                    v[field].set(getattr(self, field))
                    continue
                updated[field] = value
            if not updated:
                return
            for field, value in updated.items():
                setattr(self, field, value)
                if field != "context_enabled" and v[field].get() != value:
                    v[field].set(value)  # show the effective clamped value
            if self.max_seg > 0 and self.max_seg < self.min_seg:
                self.max_seg = self.min_seg
                v["max_seg"].set(self.max_seg)
            self._schedule_save()
            return
        try:
            self.font_name = v["font_name"].get().strip() or self._initial_settings["font_name"]
            self.font_size = max(12, min(80, int(round(v["font_size"].get()))))
            self.alpha = max(0.20, min(1.0, float(v["alpha"].get())))
            self.width_frac = max(0.30, min(1.0, float(v["width_frac"].get())))
            self.bottom = max(0, min(500, int(round(v["bottom"].get()))))
            self.hold = max(1.0, min(20.0, float(v["hold"].get())))
            self.topmost = bool(v["topmost"].get())
            self.show_previous = bool(v["show_previous"].get())
            self.history_count = max(0, min(5, int(round(v["history_count"].get()))))
            keep = max(1, self.history_count + 1)
            self.finals = self.finals[-keep:]
            if key == "history_count":
                self.window_height = 0
            self.current_bold = bool(v["current_bold"].get())
        except (ValueError, TypeError, TclError):
            return
        self._apply_style(anchor_to_screen=(key == "bottom"), keep_center=(key == "width_frac"))
        self._schedule_save()

    def _choose_color(self, key, title):
        from tkinter import colorchooser
        current = getattr(self, key)
        chosen = colorchooser.askcolor(color=current, parent=self.settings_win, title=title)[1]
        if not chosen:
            return
        setattr(self, key, chosen)
        button = getattr(self, f"_color_btn_{key}", None)
        if button is not None:
            button.configure(bg=chosen)
        self._apply_style()
        self._schedule_save()

    def _reset_settings(self):
        if not self._setting_vars:
            return
        d = self._initial_settings
        for key in ("asr_model", "asr_language", "context_enabled", "context_history", "context_chars", "llama_ctx", "slots",
                    "max_tokens", "temperature", "partial_every",
                    "silence", "vad_win", "vad_threshold",
                    "vad_min_speech_ms", "vad_min_silence_ms", "vad_speech_pad_ms", "min_seg", "max_seg",
                    "max_gain", "asr_backend", "asr_device", "asr_model_path", "asr_mmproj_path",
                    "asr_llama_server_path", "asr_llama_port", "asr_llama_url",
                    "asr_hotwords", "asr_hotwords_file", "asr_debug_dir",
                    "asr_output_txt", "asr_output_srt", "asr_no_partial",
                    "font_name", "font_size", "alpha", "width_frac", "bottom", "hold",
                    "topmost", "show_previous", "history_count", "current_bold", "auto_hide"):
            self._setting_vars[key].set(d[key])
        for key in ("current_color", "translation_color", "previous_color", "background_color"):
            setattr(self, key, d[key])
            button = getattr(self, f"_color_btn_{key}", None)
            if button is not None:
                button.configure(bg=d[key])
        self.window_height = 0
        self._settings_changed("_asr_all")
        self._settings_changed("asr_model")
        self._settings_changed("asr_language")
        for field in ("asr_backend", "asr_device", "asr_model_path", "asr_mmproj_path",
                      "asr_llama_server_path", "asr_llama_port", "asr_llama_url",
                      "asr_hotwords", "asr_hotwords_file", "asr_debug_dir",
                      "asr_output_txt", "asr_output_srt", "asr_no_partial"):
            self._settings_changed(field)
        self._settings_changed("auto_hide")
        self._settings_changed("bottom")

    def _apply_accuracy_preset(self):
        if not self._setting_vars:
            return
        preset = {
            "asr_model": "Q8_0",
            "asr_language": "auto",
            "context_enabled": True,
            "context_history": 1,
            "context_chars": 80,
            "llama_ctx": 8192,
            "slots": 2,
            "max_tokens": 200,
            "temperature": 0.0,
            "partial_every": 0.4,
            "silence": 0.5,
            "vad_win": 0.20,
            "vad_threshold": 0.45,
            "vad_min_speech_ms": 100,
            "vad_min_silence_ms": 200,
            "vad_speech_pad_ms": 100,
            "min_seg": 1.0,
            "max_seg": 6.0,
            "max_gain": 60.0,
        }
        for key, value in preset.items():
            self._setting_vars[key].set(value)
        self._settings_changed("_asr_all")
        self._settings_changed("asr_model")
        self._settings_changed("asr_language")

    def _close_asr_settings(self):
        if self._setting_vars:
            for key in ("asr_model_path", "asr_mmproj_path", "asr_llama_server_path",
                        "asr_llama_url", "asr_hotwords", "asr_hotwords_file",
                        "asr_debug_dir", "asr_output_txt", "asr_output_srt",
                        "asr_device", "asr_llama_port", "asr_backend", "asr_no_partial"):
                self._settings_changed(key)
            self._save_settings()
        if self.asr_settings_win is not None:
            try:
                self.asr_settings_win.destroy()
            except Exception:
                pass
        self.asr_settings_win = None

    def _choose_asr_output_device(self):
        from tkinter import messagebox
        try:
            pa = pyaudio.PyAudio()
            try:
                devices = [(int(d["index"]), str(d["name"]))
                           for d in pa.get_loopback_device_info_generator()]
            finally:
                pa.terminate()
        except Exception as exc:
            messagebox.showerror("音频设备", f"读取 WASAPI loopback 设备失败：{exc}",
                                 parent=self.asr_settings_win or self.root)
            return
        dialog = self.tk.Toplevel(self.asr_settings_win or self.root)
        dialog.title("选择系统输出设备")
        dialog.geometry("560x300")
        panel = self.tk.Frame(dialog, padx=10, pady=10)
        panel.pack(fill="both", expand=True)
        lb = self.tk.Listbox(panel, width=75, height=12)
        lb.pack(side="top", fill="both", expand=True)
        lb.insert("end", "[-1] 跟随系统默认输出设备")
        for index, name in devices:
            lb.insert("end", f"[{index}] {name}")
        lb.selection_set(0)
        def accept(event=None):
            selected = lb.curselection()
            if not selected:
                return
            index = -1 if selected[0] == 0 else devices[selected[0] - 1][0]
            self._setting_vars["asr_device"].set(index)
            self._settings_changed("asr_device")
            dialog.destroy()
        self.tk.Button(panel, text="使用所选设备", command=accept).pack(pady=8)
        lb.bind("<Double-Button-1>", accept)
        dialog.transient(self.asr_settings_win or self.root)
        dialog.lift()

    def _open_asr_settings(self):
        from tkinter import ttk
        if not self._setting_vars:
            return
        if self.asr_settings_win is not None:
            try:
                if self.asr_settings_win.winfo_exists():
                    self.asr_settings_win.deiconify()
                    self.asr_settings_win.lift()
                    self.asr_settings_win.focus_force()
                    return
            except Exception:
                pass

        win = self.tk.Toplevel(self.settings_win or self.root)
        self.asr_settings_win = win
        win.title("识别参数")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close_asr_settings)
        v = self._setting_vars

        outer = self.tk.Frame(win, padx=12, pady=10)
        outer.pack(fill="both", expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        core_tab = self.tk.Frame(notebook, padx=8, pady=6)
        service_tab = self.tk.Frame(notebook, padx=8, pady=6)
        text_tab = self.tk.Frame(notebook, padx=8, pady=6)
        notebook.add(core_tab, text="识别 / VAD")
        notebook.add(service_tab, text="设备 / 模型服务")
        notebook.add(text_tab, text="热词 / 输出")

        def add_text(parent, row, label, key, width=40):
            self.tk.Label(parent, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=5)
            box = self.tk.Entry(parent, textvariable=v[key], width=width)
            box.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
            box.bind("<FocusOut>", lambda e, k=key: self._settings_changed(k))
            box.bind("<Return>", lambda e, k=key: self._settings_changed(k))
            parent.columnconfigure(1, weight=1)
            return box

        def add_field(parent, row, col, label, key, start, end, increment):
            self.tk.Label(parent, text=label).grid(row=row, column=col * 2, sticky="w", padx=(0, 6), pady=4)
            box = self.tk.Spinbox(parent, from_=start, to=end, increment=increment, textvariable=v[key],
                                  width=9, command=lambda k=key: self._settings_changed(k))
            box.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 14), pady=4)
            box.bind("<Return>", lambda e, k=key: self._settings_changed(k))
            box.bind("<FocusOut>", lambda e, k=key: self._settings_changed(k))
            return box

        context_frame = ttk.LabelFrame(core_tab, text="上下文 / llama")
        context_frame.pack(fill="x", pady=(0, 8))
        self.tk.Checkbutton(context_frame, text="启用前文上下文", variable=v["context_enabled"],
                            command=lambda: self._settings_changed("context_enabled")).grid(
                                row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 2))
        self.tk.Label(context_frame, text="设置保存后下次启动生效；显式命令行参数优先。",
                      fg="#666666").grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=4)
        add_field(context_frame, 2, 0, "历史句数", "context_history", 1, 5, 1)
        add_field(context_frame, 2, 1, "历史最大字符", "context_chars", 40, 1000, 20)
        add_field(context_frame, 3, 0, "llama ctx 总长度", "llama_ctx", 2048, 16384, 1024)
        add_field(context_frame, 3, 1, "并行 slots", "slots", 1, 4, 1)
        add_field(context_frame, 4, 0, "Max tokens", "max_tokens", 64, 1024, 32)
        temperature_box = add_field(context_frame, 4, 1, "Temperature", "temperature", 0.0, 1.0, 0.1)
        self.tk.Label(context_frame, text="识别语言").grid(row=5, column=0, sticky="w", padx=8, pady=(2, 6))
        lang_box = ttk.Combobox(context_frame, textvariable=v["asr_language"],
                                 values=("auto", "ja", "zh", "en"), width=12, state="readonly")
        lang_box.grid(row=5, column=1, sticky="w", pady=(2, 6))
        lang_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed("asr_language"))

        vad_frame = ttk.LabelFrame(core_tab, text="VAD")
        vad_frame.pack(fill="x", pady=(0, 8))
        add_field(vad_frame, 0, 0, "检测步长 (s)", "vad_win", 0.1, 1.0, 0.05)
        add_field(vad_frame, 0, 1, "语音阈值", "vad_threshold", 0.1, 0.9, 0.05)
        add_field(vad_frame, 1, 0, "最短语音 (ms)", "vad_min_speech_ms", 50, 1000, 50)
        add_field(vad_frame, 1, 1, "最短静音 (ms)", "vad_min_silence_ms", 50, 2000, 50)
        add_field(vad_frame, 2, 0, "语音边界补偿 (ms)", "vad_speech_pad_ms", 0, 1000, 50)
        self.tk.Label(vad_frame, text="连续音频窗口检测；补偿音频会送入 ASR，设置重启后生效。",
                      fg="#666666").grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=4)

        segment_frame = ttk.LabelFrame(core_tab, text="切句 / 实时性")
        segment_frame.pack(fill="x", pady=(0, 8))
        add_field(segment_frame, 0, 0, "partial 刷新 (s)", "partial_every", 0.2, 2.0, 0.05)
        add_field(segment_frame, 1, 0, "静音断句 (s)", "silence", 0.2, 2.0, 0.05)
        add_field(segment_frame, 2, 1, "最短句段 (s)", "min_seg", 0.2, 5.0, 0.1)
        add_field(segment_frame, 3, 0, "最长句段 (s，0=30秒安全切段)", "max_seg", 0.0, 30.0, 0.5)
        add_field(segment_frame, 3, 1, "自动增益上限", "max_gain", 1.0, 100.0, 1.0)

        self.tk.Label(service_tab, text="后端").grid(row=0, column=0, sticky="w", pady=5)
        backend_box = ttk.Combobox(service_tab, textvariable=v["asr_backend"],
                                    values=("llama", "hf"), state="readonly", width=18)
        backend_box.grid(row=0, column=1, sticky="w", pady=5)
        def update_backend_ui(event=None):
            self._settings_changed("asr_backend")
            temperature_box.configure(state="disabled" if self.asr_backend == "hf" else "normal")
        backend_box.bind("<<ComboboxSelected>>", update_backend_ui)
        temperature_box.configure(state="disabled" if self.asr_backend == "hf" else "normal")
        add_field(service_tab, 1, 0, "输出设备索引 (-1 自动)", "asr_device", -1, 1000, 1)
        self.tk.Button(service_tab, text="选择设备…",
                       command=self._choose_asr_output_device).grid(
                           row=1, column=2, sticky="w", padx=8)
        add_field(service_tab, 2, 0, "llama 端口", "asr_llama_port", 1, 65535, 1)
        add_text(service_tab, 3, "自定义模型文件/名称", "asr_model_path")
        add_text(service_tab, 4, "mmproj GGUF 路径", "asr_mmproj_path")
        add_text(service_tab, 5, "llama-server.exe 路径", "asr_llama_server_path")
        add_text(service_tab, 6, "已有 llama-server URL", "asr_llama_url")
        self.tk.Label(service_tab,
                      text="留空=自动；外部 llama 服务不受本程序的 slots/ctx 设置控制。\\n"
                           "HF 后端只支持 Temperature=0；设备索引可用命令行 --list 查看。\\n"
                           "自定义模型路径优先于 Q8_0/BF16；HF 模式使用 Transformers 模型名。",
                      fg="#666666", justify="left").grid(row=7, column=0, columnspan=2, sticky="w", pady=6)

        add_text(text_tab, 0, "热词（逗号分隔）", "asr_hotwords")
        add_text(text_tab, 1, "热词文件路径", "asr_hotwords_file")
        add_text(text_tab, 2, "调试日志目录", "asr_debug_dir")
        add_text(text_tab, 3, "保存 TXT 路径", "asr_output_txt")
        add_text(text_tab, 4, "保存 SRT 路径", "asr_output_srt")
        self.tk.Checkbutton(text_tab, text="只输出最终字幕（关闭 Partial）",
                            variable=v["asr_no_partial"],
                            command=lambda: self._settings_changed("asr_no_partial")).grid(
                                row=5, column=0, columnspan=2, sticky="w", pady=8)
        self.tk.Label(text_tab, text="路径留空=不指定。保存并重启生效；命令行显式参数优先。",
                      fg="#666666").grid(row=6, column=0, columnspan=2, sticky="w", pady=5)

        note = self.tk.Label(
            outer,
            text="ASR 与 VAD 参数下次启动生效；命令行显式参数优先。",
            fg="#666666", anchor="w",
        )
        note.pack(fill="x", pady=(2, 8))
        buttons = self.tk.Frame(outer)
        buttons.pack(fill="x")
        self.tk.Button(buttons, text="高精度推荐", command=self._apply_accuracy_preset, width=12).pack(side="left")
        self.tk.Button(buttons, text="关闭", command=self._close_asr_settings, width=10).pack(side="right")

        win.update_idletasks()
        x = max(20, self.settings_win.winfo_x() - win.winfo_width() - 12) if self.settings_win else 20
        y = max(20, self.settings_win.winfo_y()) if self.settings_win else 20
        win.geometry(f"+{x}+{y}")
        win.lift()

    def _close_settings(self):
        self._save_settings()
        self._close_asr_settings()
        if self.settings_win is not None:
            try:
                self.settings_win.destroy()
            except Exception:
                pass
        self.settings_win = None
        self._setting_vars = None

    def open_settings(self):
        import tkinter.font as tkfont
        from tkinter import ttk

        if self.settings_win is not None:
            try:
                if self.settings_win.winfo_exists():
                    self.settings_win.deiconify()
                    self.settings_win.lift()
                    self.settings_win.focus_force()
                    return
            except Exception:
                pass

        win = self.tk.Toplevel(self.root)
        self.settings_win = win
        win.title("字幕设置")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close_settings)

        v = {
            "asr_model": self.tk.StringVar(value=self.asr_model),
            "asr_language": self.tk.StringVar(value=self.asr_language),
            "context_enabled": self.tk.BooleanVar(value=self.context_enabled),
            "context_history": self.tk.IntVar(value=self.context_history),
            "context_chars": self.tk.IntVar(value=self.context_chars),
            "llama_ctx": self.tk.IntVar(value=self.llama_ctx),
            "slots": self.tk.IntVar(value=self.slots),
            "max_tokens": self.tk.IntVar(value=self.max_tokens),
            "temperature": self.tk.DoubleVar(value=self.temperature),
            "partial_every": self.tk.DoubleVar(value=self.partial_every),
            "silence": self.tk.DoubleVar(value=self.silence),
            "vad_win": self.tk.DoubleVar(value=self.vad_win),
            "vad_threshold": self.tk.DoubleVar(value=self.vad_threshold),
            "vad_min_speech_ms": self.tk.IntVar(value=self.vad_min_speech_ms),
            "vad_min_silence_ms": self.tk.IntVar(value=self.vad_min_silence_ms),
            "vad_speech_pad_ms": self.tk.IntVar(value=self.vad_speech_pad_ms),
            "min_seg": self.tk.DoubleVar(value=self.min_seg),
            "max_seg": self.tk.DoubleVar(value=self.max_seg),
            "max_gain": self.tk.DoubleVar(value=self.max_gain),
            "asr_device": self.tk.IntVar(value=self.asr_device),
            "asr_backend": self.tk.StringVar(value=self.asr_backend),
            "asr_model_path": self.tk.StringVar(value=self.asr_model_path),
            "asr_mmproj_path": self.tk.StringVar(value=self.asr_mmproj_path),
            "asr_llama_server_path": self.tk.StringVar(value=self.asr_llama_server_path),
            "asr_llama_port": self.tk.IntVar(value=self.asr_llama_port),
            "asr_llama_url": self.tk.StringVar(value=self.asr_llama_url),
            "asr_hotwords": self.tk.StringVar(value=self.asr_hotwords),
            "asr_hotwords_file": self.tk.StringVar(value=self.asr_hotwords_file),
            "asr_debug_dir": self.tk.StringVar(value=self.asr_debug_dir),
            "asr_output_txt": self.tk.StringVar(value=self.asr_output_txt),
            "asr_output_srt": self.tk.StringVar(value=self.asr_output_srt),
            "asr_no_partial": self.tk.BooleanVar(value=self.asr_no_partial),
            "font_name": self.tk.StringVar(value=self.font_name),
            "font_size": self.tk.DoubleVar(value=self.font_size),
            "alpha": self.tk.DoubleVar(value=self.alpha),
            "width_frac": self.tk.DoubleVar(value=self.width_frac),
            "bottom": self.tk.DoubleVar(value=self.bottom),
            "hold": self.tk.DoubleVar(value=self.hold),
            "auto_hide": self.tk.BooleanVar(value=self.auto_hide),
            "topmost": self.tk.BooleanVar(value=self.topmost),
            "show_previous": self.tk.BooleanVar(value=self.show_previous),
            "history_count": self.tk.DoubleVar(value=self.history_count),
            "current_bold": self.tk.BooleanVar(value=self.current_bold),
        }
        self._setting_vars = v

        frame = self.tk.Frame(win, padx=14, pady=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        row = 0
        self.tk.Label(frame, text="ASR 主模型").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        model_holder = self.tk.Frame(frame)
        model_holder.grid(row=row, column=1, sticky="ew", pady=5)
        model_box = ttk.Combobox(model_holder, textvariable=v["asr_model"],
                                 values=("Q8_0", "BF16"), width=12, state="readonly")
        model_box.pack(side="left")
        model_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed("asr_model"))
        models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        q8_ok = os.path.isfile(os.path.join(models_dir, "Qwen3-ASR-1.7B-Q8_0.gguf"))
        bf16_ok = os.path.isfile(os.path.join(models_dir, "Qwen3-ASR-1.7B-bf16.gguf"))
        availability = f"Q8_0 {'✓' if q8_ok else '✗'}   BF16 {'✓' if bf16_ok else '✗'}"
        self.tk.Label(model_holder, text=availability, fg="#666666").pack(side="left", padx=(10, 0))
        row += 1

        self.tk.Label(frame, text="识别参数").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        self.tk.Button(frame, text="VAD / 切句 / 上下文…", command=self._open_asr_settings,
                       width=22).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        self.tk.Label(frame, text="字体").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        fonts = sorted(set(tkfont.families()))
        font_box = ttk.Combobox(frame, textvariable=v["font_name"], values=fonts, width=29)
        font_box.grid(row=row, column=1, sticky="ew", pady=5)
        font_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed("font_name"))
        font_box.bind("<Return>", lambda e: self._settings_changed("font_name"))
        row += 1

        def add_scale(label, key, start, end, resolution, display):
            nonlocal row
            self.tk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            holder = self.tk.Frame(frame)
            holder.grid(row=row, column=1, sticky="ew", pady=4)
            scale = self.tk.Scale(holder, from_=start, to=end, resolution=resolution, orient="horizontal",
                                  showvalue=False, variable=v[key], length=230,
                                  command=lambda _value, k=key: (value_label.configure(text=display(v[k].get())),
                                                                 self._settings_changed(k)))
            scale.pack(side="left", fill="x", expand=True)
            value_label = self.tk.Label(holder, width=8, anchor="e", text=display(v[key].get()))
            value_label.pack(side="right")
            row += 1

        add_scale("字号", "font_size", 12, 80, 1, lambda x: f"{int(round(x))} pt")
        add_scale("窗口透明度", "alpha", 0.20, 1.00, 0.01, lambda x: f"{int(round(x * 100))}%")
        add_scale("窗口宽度", "width_frac", 0.30, 1.00, 0.01, lambda x: f"{int(round(x * 100))}%")
        add_scale("距屏幕底部", "bottom", 0, 500, 5, lambda x: f"{int(round(x))} px")
        add_scale("字幕停留时间", "hold", 1.0, 20.0, 0.5, lambda x: f"{x:.1f} s")
        add_scale("历史字幕数量", "history_count", 0, 5, 1, lambda x: f"{int(round(x))} 句")

        for label, key in (
            ("当前日语颜色", "current_color"),
            ("中文翻译颜色", "translation_color"),
            ("历史字幕颜色", "previous_color"),
            ("背景颜色", "background_color"),
        ):
            self.tk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            btn = self.tk.Button(frame, text="选择颜色", width=12, bg=getattr(self, key),
                                 command=lambda k=key, t=label: self._choose_color(k, t))
            btn.grid(row=row, column=1, sticky="w", pady=4)
            setattr(self, f"_color_btn_{key}", btn)
            row += 1

        checks = self.tk.Frame(frame)
        checks.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.tk.Checkbutton(checks, text="窗口始终置顶", variable=v["topmost"],
                            command=lambda: self._settings_changed("topmost")).pack(side="left")
        self.tk.Checkbutton(checks, text="显示历史字幕", variable=v["show_previous"],
                            command=lambda: self._settings_changed("show_previous")).pack(side="left", padx=(12, 0))
        self.tk.Checkbutton(checks, text="当前字幕粗体", variable=v["current_bold"],
                            command=lambda: self._settings_changed("current_bold")).pack(side="left", padx=(12, 0))
        row += 1

        self.tk.Checkbutton(frame, text="空闲自动隐藏整个字幕窗口", variable=v["auto_hide"],
                            command=lambda: self._settings_changed("auto_hide")).grid(
                                row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))
        row += 1

        note = self.tk.Label(frame, text="字幕外观即时预览；模型和识别参数在下次启动生效。设置会自动保存。",
                             fg="#666666", anchor="w")
        note.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        row += 1

        buttons = self.tk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(8, 0))
        self.tk.Button(buttons, text="恢复默认", command=self._reset_settings, width=10).pack(side="left", padx=(0, 8))
        # Translation UI is independent of the ASR and audio configuration.
        from translation.gui import open_settings as open_translation_settings, open_history as open_translation_history
        self.tk.Button(buttons, text="翻译设置", command=lambda: open_translation_settings(win), width=10).pack(side="left", padx=(0, 8))
        self.tk.Button(buttons, text="译文历史", command=lambda: open_translation_history(win), width=10).pack(side="left", padx=(0, 8))
        self.tk.Button(buttons, text="保存并关闭", command=self._close_settings, width=12).pack(side="left")

        win.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
        y = max(20, self.root.winfo_y() - win.winfo_height() - 12)
        win.geometry(f"+{x}+{y}")
        win.lift()

    # ---- 布局 ----
    def _relayout(self, first=False, anchor_to_screen=False, center_x=None):
        # 默认按真实字体行高自动计算；用户拖动上下边缘后优先保留手动高度。
        history_lines = self.history_count if self.show_previous else 0
        auto_h = (self.f_prev.metrics("linespace") * history_lines + self.f_cur.metrics("linespace") * 2
                  + self.f_trans.metrics("linespace") * 2 + 34)
        h = self.window_height if self.window_height > 0 else auto_h
        if first:
            x = (self.root.winfo_screenwidth() - self.w) // 2
            y = self.sh - h - self.bottom
            self.root.geometry(f"{self.w}x{h}+{x}+{y}")
        elif anchor_to_screen:
            x = self.root.winfo_x()
            y = self.root.winfo_screenheight() - h - self.bottom
            self.root.geometry(f"{self.w}x{h}+{x}+{max(0, y)}")
        else:
            # 保持底边不动
            y_bottom = self.root.winfo_y() + self.root.winfo_height()
            x = self.root.winfo_x()
            if center_x is not None:
                x = int(center_x - self.w / 2)
                x = max(0, min(self.root.winfo_screenwidth() - self.w, x))
            self.root.geometry(f"{self.w}x{h}+{x}+{y_bottom - h}")
        self.h = h

    def _resize_edge_at(self, e):
        margin = 9
        rx = e.x_root - self.root.winfo_rootx()
        ry = e.y_root - self.root.winfo_rooty()
        w = max(1, self.root.winfo_width())
        h = max(1, self.root.winfo_height())
        edge = ""
        if rx <= margin:
            edge += "w"
        elif rx >= w - margin:
            edge += "e"
        if ry <= margin:
            edge += "n"
        elif ry >= h - margin:
            edge += "s"
        return edge

    def _resize_cursor(self, e):
        edge = self._resize_edge_at(e)
        cursor = "sizing" if len(edge) == 2 else (
            "sb_h_double_arrow" if edge in ("w", "e") else
            "sb_v_double_arrow" if edge in ("n", "s") else ""
        )
        try:
            e.widget.configure(cursor=cursor)
        except Exception:
            pass

    def _drag_start(self, e):
        self._resize_edge = self._resize_edge_at(e)
        if self._resize_edge:
            self._resize_origin = (
                e.x_root, e.y_root,
                self.root.winfo_x(), self.root.winfo_y(),
                self.root.winfo_width(), self.root.winfo_height(),
            )
            return
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        edge = getattr(self, "_resize_edge", "")
        if not edge:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
            return

        sx, sy, x0, y0, w0, h0 = self._resize_origin
        dx, dy = e.x_root - sx, e.y_root - sy
        min_w = max(320, int(self.root.winfo_screenwidth() * 0.30))
        min_h = 80
        x, y, w, h = x0, y0, w0, h0

        if "e" in edge:
            w = max(min_w, w0 + dx)
        elif "w" in edge:
            w = max(min_w, w0 - dx)
            x = x0 + (w0 - w)

        if "s" in edge:
            h = max(min_h, h0 + dy)
        elif "n" in edge:
            h = max(min_h, h0 - dy)
            y = y0 + (h0 - h)

        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.w, self.h = w, h
        self.width_frac = max(0.30, min(1.0, w / self.root.winfo_screenwidth()))
        self.window_height = h
        wrap = max(280, w - 40)
        self.l_prev.configure(wraplength=wrap)
        self.l_trans.configure(wraplength=wrap)
        if self._setting_vars:
            self._setting_vars["width_frac"].set(self.width_frac)

    def _drag_end(self, e):
        if getattr(self, "_resize_edge", ""):
            self._resize_edge = ""
            self._schedule_save()

    def _wheel(self, e):
        self.font_size = max(12, min(80, self.font_size + (2 if e.delta > 0 else -2)))
        if self._setting_vars:
            self._setting_vars["font_size"].set(self.font_size)
        self._apply_style()
        self._schedule_save()

    # ---- 识别线程调用 ----
    def _post_ui(self, kind, text):
        if self.closed or self._close_requested.is_set():
            return
        if kind == "p":
            # Replace the newest queued preview instead of replaying stale text.
            # queue.Queue's mutex protects its underlying deque on CPython.
            with self.q.mutex:
                if self.q.queue and self.q.queue[-1][0] == "p":
                    self.q.queue[-1] = ("p", text)
                    return
        try:
            self.q.put_nowait((kind, text))
            return
        except queue.Full:
            if kind != "f":
                # Drop replaceable UI events, never evict a previously queued Final.
                return
        # Never block ASR on a frozen Tk loop. Remove one obsolete UI preview,
        # translation or status (not a Final) to make room. If the queue is
        # exclusively Finals, fail visibly: MultiSink keeps its other outputs
        # alive and the ASR worker preserves recovery audio.
        with self.q.mutex:
            for index, event in enumerate(self.q.queue):
                if event[0] != "f":
                    del self.q.queue[index]
                    self.q.unfinished_tasks -= 1
                    self.q.not_full.notify()
                    break
        try:
            self.q.put_nowait(("f", text))
        except queue.Full as exc:
            raise OverflowError("Tkinter Final event buffer full (UI not responding)") from exc

    def partial(self, text):
        self._post_ui("p", text)

    def final(self, text, t_start=None, t_end=None):
        self._post_ui("f", text)

    def translation(self, source, translated):
        self._post_ui("t", (source, translated))

    def status(self, text):
        self._post_ui("s", text)

    @staticmethod
    def _translation_matches_caption(source, caption):
        """Accept a translation only for the currently displayed Japanese tail.

        Translation may include earlier Final chunks joined across maxlen cuts,
        so a normalized suffix comparison is safer than exact string equality.
        """
        if not source:
            return True  # Explicit clear / paused status is not a translation.
        if not caption:
            return False
        def key(value):
            return re.sub(r"[\s、。！？!?，,.．]", "", value or "")
        src, current = key(source), key(caption)
        return bool(src and current and src.endswith(current))

    def request_close(self):
        # Out-of-band shutdown cannot block or be evicted by queue backpressure.
        self._close_requested.set()

    # ---- 主线程 ----
    def _poll(self):
        if self.closed:
            return
        if self._close_requested.is_set():
            self.close()
            return
        changed = False
        for _ in range(self.MAX_UI_DRAIN):
            try:
                kind, text = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "q":
                self.close(); return
            if kind == "t":
                source, translated = text
                current = self.partial_text or (self.finals[-1] if self.finals else "")
                if not self._translation_matches_caption(source, current):
                    continue  # Delayed old translation must not follow a newer caption.
                self.translation_source = source
                self.translation_text = translated
                changed = True
                self.last_update = time.time()
                continue
            changed = True
            if kind == "p":
                self.partial_text = text
                if text:
                    self.caption_seen = True
            elif kind == "s":
                self.finals, self.partial_text = [], text
                self.translation_text = ""
                self.translation_source = ""
            else:
                if text:
                    self.caption_seen = True
                    keep = max(1, self.history_count + 1)
                    self.finals = (self.finals + [text])[-keep:]
                self.partial_text = ""
            current = self.partial_text or (self.finals[-1] if self.finals else "")
            # Partial text changes frequently: keep the last valid Chinese
            # until its replacement arrives. Completed Finals/status still
            # invalidate an unrelated translation, and incoming translations
            # must continue passing the existing strict source match above.
            if (kind != "p" and self.translation_source
                    and not self._translation_matches_caption(self.translation_source, current)):
                self.translation_source = self.translation_text = ""
            self.last_update = time.time()
        if changed:
            self._stop_fade()
            self.root.attributes("-alpha", self.alpha)
            self._render()
        elif (self.auto_hide and self.caption_seen and (self.finals or self.partial_text)
              and not self.fading and time.time() - self.last_update > self.hold):
            self._start_fade()
        self.root.after(60, self._poll)

    def _render(self):
        if self.partial_text:
            cur = self.partial_text
            history = self.finals[-self.history_count:] if self.history_count > 0 else []
        else:
            cur = self.finals[-1] if self.finals else ""
            if self.history_count > 0 and len(self.finals) > 1:
                history = self.finals[-(self.history_count + 1):-1]
            else:
                history = []
        translated = self.translation_text
        prev = "\n".join(history)
        self.l_prev.configure(text=prev if self.show_previous else "")
        self.l_cur.configure(text=cur)
        self.l_trans.configure(text=translated)

    # ---- 渐隐：hold 秒没新字后 1.2s 内把整条淡出 ----
    def _start_fade(self):
        if not self.auto_hide:
            return
        self._fade_generation += 1
        self.fading = True
        self._fade_step(0, self._fade_generation)

    def _fade_step(self, i, generation):
        if not self.auto_hide or not self.fading or self.closed or generation != self._fade_generation:
            return
        n = 20
        if i > n:
            self.finals, self.partial_text = [], ""
            self.translation_text, self.translation_source = "", ""
            self._render()
            self.root.attributes("-alpha", 0.0)   # 整条隐掉，来新字再出现
            self.fading = False
            return
        self.root.attributes("-alpha", self.alpha * (1 - i / n))
        self.root.after(60, lambda: self._fade_step(i + 1, generation))

    def _stop_fade(self):
        # Invalidate callbacks from earlier fade cycles, including when a new
        # fade begins before an old after() callback has finished waiting.
        self._fade_generation += 1
        if self.fading:
            self.fading = False
            self.root.attributes("-alpha", self.alpha)

    def run(self):
        self.root.mainloop()

    def close(self):
        if not self.closed:
            self.closed = True
            self._save_settings()
            self._close_asr_settings()
            if self.settings_win is not None:
                try:
                    self.settings_win.destroy()
                except Exception:
                    pass
            try:
                self.root.destroy()
            except Exception:
                pass


# All Python translation business code is embedded below in this .pyw.
# Each original module is executed from its readable inline source.
def _lc_load_inline_translation():
    import textwrap as _lc_textwrap
    import types as _lc_types

    _source = _LCPath(__file__).read_text(encoding="utf-8-sig")
    _package = _lc_types.ModuleType("translation")
    _package.__path__ = []
    _package.__package__ = "translation"
    _package.__file__ = str(_LC_ROOT / "translation" / "__init__.py")
    _lc_sys.modules["translation"] = _package
    for _file in ("text.py", "history.py", "api.py", "engine.py", "gui.py", "__init__.py"):
        _start = "    # <<< INLINE translation/" + _file + " BEGIN >>>\n"
        _end = "    # <<< INLINE translation/" + _file + " END >>>"
        _body = _lc_textwrap.dedent(_source.split(_start, 1)[1].split(_end, 1)[0])
        _name = _file[:-3]
        _module = _package if _name == "__init__" else _lc_types.ModuleType("translation." + _name)
        _module.__file__ = str(_LC_ROOT / "translation" / _file)
        _module.__package__ = "translation"
        if _name != "__init__":
            _lc_sys.modules["translation." + _name] = _module
            setattr(_package, _name, _module)
        exec(compile(_body, _module.__file__, "exec"), _module.__dict__)


_lc_load_inline_translation()


from translation import GoogleTranslateSink, google_translate

# Translation imports refer to the in-memory modules built from this .pyw.
# The translation/ source folder is not required; edits stay in this launcher.


class MultiSink:
    def __init__(self, *sinks):
        self.sinks = sinks

    def set_capture_origin(self, capture_time):
        for s in self.sinks:
            setter = getattr(s, "set_capture_origin", None)
            if setter is not None:
                setter(capture_time)

    def partial(self, text):
        for s in self.sinks:
            try:
                s.partial(text)
            except Exception as exc:
                print("临时字幕输出失败:", repr(exc), flush=True)

    def partial_stable(self, text, stable):
        for s in self.sinks:
            try:
                fn = getattr(s, "partial_stable", None)
                if fn:
                    fn(text, stable)
                else:
                    s.partial(text)
            except Exception as exc:
                print("临时字幕输出失败:", repr(exc), flush=True)

    def final(self, text, a, b):
        # Backward-compatible entrypoint for callers without a cut reason.
        return self.final_segment(text, a, b, None)

    def final_segment(self, text, a, b, reason=None):
        failures = []
        for s in self.sinks:
            try:
                reason_aware = getattr(s, "final_segment", None)
                if reason_aware is not None:
                    delivered = reason_aware(text, a, b, reason)
                else:
                    delivered = s.final(text, a, b)
                if delivered is False:
                    failures.append(OSError("Nested subtitle output reported failure"))
            except Exception as exc:
                # Keep overlay/translation available when console/SRT fails.
                print("单路字幕输出失败:", repr(exc), flush=True)
                failures.append(exc)
        # Existing callers may ignore the result; the ASR worker uses False
        # to preserve failed durable outputs without interrupting other sinks.
        return not failures


# ============================ 识别后端 ============================
QWEN_LANG = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "yue": "Cantonese",
             "fr": "French", "de": "German", "es": "Spanish", "ru": "Russian", "pt": "Portuguese",
             "it": "Italian", "ar": "Arabic", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian"}


# ASR source: TerryKiddy/live-caption @ 794fea1fd50f34e55a6e1e1537aa1659af61fea1 (MIT license)
# Keep os.add_dll_directory cookies alive for the lifetime of this process.
_LC_NVIDIA_DLL_HANDLES = []

def _add_nvidia_dlls():
    base = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    if not os.path.isdir(base):
        return
    for sub in os.listdir(base):
        b = os.path.join(base, sub, "bin")
        if os.path.isdir(b):
            os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                _LC_NVIDIA_DLL_HANDLES.append(os.add_dll_directory(b))


_add_nvidia_dlls()
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import pyaudiowpatch as pyaudio


class QwenBackend:
    """Qwen3-ASR (transformers 后端)。context 参数可塞热词/上一句。"""
    name = "Qwen3-ASR"

    def __init__(self, model_name, lang, hotwords, use_context):
        import torch, transformers
        transformers.logging.set_verbosity_error()
        from qwen_asr import Qwen3ASRModel
        self.model = Qwen3ASRModel.from_pretrained(model_name, dtype=torch.bfloat16, device_map="cuda:0",
                                                   max_inference_batch_size=4, max_new_tokens=256)
        self.lang = QWEN_LANG.get(lang, lang) if lang else None
        self.hot = hotwords or ""
        self.use_context = use_context
        self.last = ""

    def warmup(self):
        self.model.transcribe(audio=(np.zeros(SR, dtype=np.float32), SR), language=self.lang)

    def transcribe(self, audio):
        ctx = self.hot
        if self.use_context and self.last:
            ctx = (ctx + "\n" + self.last[-80:]).strip()
        r = self.model.transcribe(audio=(audio, SR), context=ctx, language=self.lang)
        return _clean(r[0].text) if r else ""


class LlamaBackend:
    """llama.cpp llama-server (b10941+ 支持 Qwen3-ASR 的 mtmd 音频)，HTTP /completion + multimodal_data。
    解码比 HF transformers 快好几倍。"""
    name = "llama.cpp"

    def __init__(self, server_exe, model, mmproj, lang, hotwords, use_context, port=8765, url=None, slots=2):
        import subprocess, urllib.request, socket, json
        self.urllib = urllib.request
        self.lang = QWEN_LANG.get(lang, lang) if lang else None
        self.hot = hotwords or ""
        self.use_context = use_context
        self.last = ""
        self.proc = None
        self.job = None
        self._lifecycle_lock = threading.RLock()
        if url is None:
            # 端口上已经有一个跑着同一个模型的 server（上次没关干净）就直接复用；被别的东西占着就换端口
            existing = self._probe(f"http://127.0.0.1:{port}", model)
            if existing == "same":
                self.url = f"http://127.0.0.1:{port}"
                print(f"复用已在运行的 llama-server :{port}")
            else:
                if existing == "other":
                    with socket.socket() as sk:
                        sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
                self.url = f"http://127.0.0.1:{port}"
                cmd = [server_exe, "-m", model, "--mmproj", mmproj, "-ngl", "99", "--port", str(port),
                       "-c", "8192", "--no-webui", "-np", str(slots), "--log-disable"]
                self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    self._bind_job(self.proc)
                except BaseException:
                    self.close()
                    raise
        else:
            self.url = url
        # 等 /health
        t0 = time.time()
        while time.time() - t0 < 180:
            try:
                with self.urllib.urlopen(self.url + "/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError("llama-server 启动失败（退出码 %s）" % self.proc.returncode)
                time.sleep(0.5)
        else:
            raise RuntimeError("llama-server 没起来")

    def _probe(self, url, model, slots=None, ctx_size=None):
        """返回 'none'(端口空闲) / 'same'(同模型 server 在跑) / 'other'(端口被占但不是我们的)"""
        import json, socket
        with socket.socket() as sk:
            sk.settimeout(0.3)
            if sk.connect_ex(("127.0.0.1", int(url.rsplit(":", 1)[1]))) != 0:
                return "none"
        try:
            props = _asr_json_request(url + "/props", timeout=2.,
                                      cancel_events=(getattr(self, 'cancel', None),
                                                     getattr(self, '_request_closed', None)))
            current = props.get("model_path", "")
            same_path = current and os.path.normcase(os.path.abspath(current)) == os.path.normcase(os.path.abspath(model))
            same_slots = slots is None or props.get("total_slots") == slots
            same_ctx = ctx_size is None or props.get("default_generation_settings", {}).get("n_ctx") == ctx_size
            if same_path and same_slots and same_ctx:
                return "same"
        except _ASRCancelled:
            raise
        except Exception:
            pass
        return "other"

    def _bind_job(self, proc):
        """Bind only our child to an owned, pointer-sized Windows Job handle."""
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(proc._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        self.job = handle
        return handle

    def _prompt(self):
        ctx = self.hot
        if self.use_context and self.last:
            ctx = (ctx + "\n" + self.last[-80:]).strip()
        p = ("<|im_start|>system\n" + ctx + "<|im_end|>\n"
             "<|im_start|>user\n<|audio_start|><__media__><|audio_end|><|im_end|>\n"
             "<|im_start|>assistant\n")
        if self.lang:
            p += f"language {self.lang}<asr_text>"
        return p

    @staticmethod
    def _wav_b64(audio):
        import io, wave, base64
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        return base64.b64encode(b.getvalue()).decode()

    def warmup(self):
        self.transcribe(np.zeros(SR, dtype=np.float32))

    def transcribe(self, audio):
        import json
        ctx = self.hot
        if self.use_context and self.last:
            ctx = (ctx + "\n" + self.last[-80:]).strip()
        msgs = [{"role": "system", "content": ctx},
                {"role": "user", "content": [{"type": "input_audio",
                                              "input_audio": {"data": self._wav_b64(audio), "format": "wav"}}]}]
        body = json.dumps({"messages": msgs, "max_tokens": 200, "temperature": 0.0, "cache_prompt": True}).encode()
        req = self.urllib.Request(self.url + "/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
        with self.urllib.urlopen(req, timeout=60) as r:
            out = json.loads(r.read().decode())
        text = out["choices"][0]["message"]["content"]
        # 模型输出形如 "language Chinese<asr_text>正文"
        if "<asr_text>" in text:
            lang, text = text.split("<asr_text>", 1)
            if self.lang and self.lang not in lang:
                return ""      # 指定了语言，但这段判成别的语言（多半是音乐/噪声），丢掉
        return _clean(text)

    def close(self):
        with self._lifecycle_lock:
            import subprocess
            proc, self.proc = self.proc, None
            try:
                if proc is not None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5.)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5.)
            finally:
                handle, self.job = getattr(self, "job", None), None
                if handle is not None and os.name == "nt":
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel.CloseHandle.restype = wintypes.BOOL
                    kernel.CloseHandle(handle)


class DebugLog:
    """--debug DIR：每个最终段存 wav + 一行 jsonl（长度/时延/文本），用来调参。"""

    def __init__(self, d):
        import json
        self.json = json
        self.d = d
        os.makedirs(d, exist_ok=True)
        self.f = open(os.path.join(d, "log.jsonl"), "a", encoding="utf-8")
        self.t0 = time.time()

    def write(self, rec, audio=None):
        import wave
        if audio is not None:
            import uuid
            fn = f"seg{rec['seg']:04d}_{uuid.uuid4().hex}.wav"
            with wave.open(os.path.join(self.d, fn), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
            rec["wav"] = fn
        self.f.write(self.json.dumps(rec, ensure_ascii=False) + "\n"); self.f.flush()


def find_llama_server(here):
    import shutil
    cands = [os.environ.get("LLAMA_SERVER"), os.path.join(here, "llama", "llama-server.exe"),
             os.path.join(here, "llama-server.exe"), shutil.which("llama-server")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    raise RuntimeError("找不到 llama-server.exe：请把 llama.cpp 的 Windows CUDA 构建解压到脚本旁的 llama/ 目录，"
                       "或设置环境变量 LLAMA_SERVER，或用 --llama-server 指定")


def load_hotwords(args):
    words = []
    path = args.hotwords_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "hotwords.txt")
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as stream:
            for line in stream:
                line = line.split("#")[0].strip()
                if line:
                    words += [w.strip() for w in re.split(r"[,，\s]+", line) if w.strip()]
    if args.hotwords:
        words += [w.strip() for w in args.hotwords.split(",") if w.strip()]
    return ",".join(dict.fromkeys(words))


class RollingVadDetector:
    """Stateful Silero ONNX VAD, 512-sample frames and 350ms+ pre-roll."""
    FRAME = 512
    CONTEXT = 64
    def __init__(self, threshold, min_speech_ms, min_silence_ms, pad_ms, session=None):
        if session is None:
            from faster_whisper.vad import get_vad_model
            session = get_vad_model().session
        self.session = session
        self.threshold = float(threshold)
        self.negative = max(.02, self.threshold - .15) if self.threshold > .15 else self.threshold
        self.min_speech = max(1, int(SR * min_speech_ms / 1000))
        self.min_silence = max(1, int(SR * min_silence_ms / 1000))
        self.preroll = max(int(SR * .35), int(SR * pad_ms / 1000))
        self.history_limit = max(int(SR * 1.25), self.preroll + 2 * self.FRAME)
        self.total_samples = 0
        self.processed = 0
        self.last_spans = []
        self.last_speech_end = 0
        self.recent = np.empty(0, dtype=np.float32)
        self.pending = np.empty(0, dtype=np.float32)
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._candidate = None
        self._speech_start = None
        self._active = False
        self._low_since = None

    def _step(self, frame):
        payload = np.concatenate((self._context, frame)).reshape(1, -1)
        out, self._h, self._c = self.session.run(
            None, {'input': payload, 'h': self._h, 'c': self._c})
        score = float(np.asarray(out).reshape(-1)[0])
        self._context = frame[-self.CONTEXT:].copy()
        self.processed += self.FRAME
        if score >= self.threshold:
            self._low_since = None
            if self._candidate is None:
                self._candidate = self.processed - self.FRAME
            if not self._active and self.processed - self._candidate >= self.min_speech:
                self._active = True
                self._speech_start = max(0, self._candidate - self.preroll)
            if self._active:
                self.last_speech_end = self.processed
                self.last_spans = [{'start': self._speech_start, 'end': self.processed}]
        elif not self._active:
            self._candidate = None
        elif score < self.negative:
            if self._low_since is None:
                self._low_since = self.processed - self.FRAME
            if self.processed - self._low_since >= self.min_silence:
                self._active = False
                self._candidate = None
                self._speech_start = None
                self._low_since = None
        return self._active

    def feed(self, audio):
        audio = np.asarray(audio, dtype=np.float32)
        self.total_samples += len(audio)
        self.recent = np.concatenate((self.recent, audio))[-self.history_limit:]
        self.pending = np.concatenate((self.pending, audio))
        while len(self.pending) >= self.FRAME:
            self._step(self.pending[:self.FRAME])
            self.pending = self.pending[self.FRAME:]
        return self._active

    def slice_recent(self, absolute_start):
        oldest = self.total_samples - len(self.recent)
        return self.recent[max(0, absolute_start - oldest):].copy()

    def finish(self):
        return None

# ============================ 识别循环 ============================
class _ASROutputTruncated(RuntimeError):
    """Generator output ran out of tokens before producing final text."""


def _join_recovery_texts(chunks):
    """Only trim a substantial identical overlap, not common short Japanese words."""
    result = ""
    for text in chunks:
        text = _clean(text)
        if not text:
            continue
        max_overlap = min(len(result), len(text), 12)
        shared = next((k for k in range(max_overlap, 2, -1)
                       if result.endswith(text[:k])), 0)
        result += text[shared:]
    return result


def _transcribe_final_with_budget(backend, audio):
    """Bounded token retry, then small-audio fallback for pathological output.

    Never return a truncated model response. Never alter another worker's
    decoder settings. A wholly failed split is preserved by the existing
    durable recovery WAV mechanism.
    """
    if not getattr(backend, "supports_token_budget", False):
        return backend.transcribe(audio)
    first = max(64, min(1024, int(getattr(backend, "_lc_max_tokens", 200))))
    budgets = sorted(set([first, min(1024, max(384, first * 2)),
                          min(1024, max(768, first * 4))]))
    for budget in budgets:
        try:
            return backend.transcribe(audio, max_tokens=budget)
        except _ASROutputTruncated:
            print(f"ASR output truncated at {budget} tokens", flush=True)
    if len(audio) <= int(2.05 * SR):
        raise _ASROutputTruncated("short ASR output still truncates; recovery WAV required")
    # <=16s Turn: at most twelve ~1.8s clips. Unbounded splitting could
    # otherwise turn a single 5s error into minutes of bad ASR generations.
    frame = int(1.8 * SR)
    overlap = int(.2 * SR)
    chunk_limit = 12
    pos = 0
    parts = []
    while pos < len(audio):
        end = min(len(audio), pos + frame)
        chunk = audio[pos:end]
        if len(chunk) < int(.5 * SR) and parts:
            # Never discard the last <500ms of speech. Re-recognize the
            # trailing full-width window and reconcile its short overlap.
            pos = max(0, len(audio) - frame)
            end = len(audio)
            chunk = audio[pos:end]
        if len(parts) >= chunk_limit:
            raise _ASROutputTruncated("ASR split retry exceeds safety segment limit")
        try:
            # One moderate budget per small segment. A failed segment rejects
            # the entire completion rather than silently dropping the word.
            text = backend.transcribe(chunk, max_tokens=min(768, max(first * 2, 384)))
        except _ASROutputTruncated as exc:
            print(f"ASR split recovery failed at audio {pos/SR:.2f}-{end/SR:.2f}s", flush=True)
            raise _ASROutputTruncated("ASR chunked recovery failed; WAV preserved") from exc
        if not text or len(text) > 110:
            raise _ASROutputTruncated("ASR split gave empty/implausible text; WAV preserved")
        parts.append(text)
        if end >= len(audio):
            break
        pos = end - overlap
    if not parts:
        raise _ASROutputTruncated("ASR split recovery returned no valid chunks")
    print(f"ASR completed by bounded split recovery: {len(parts)} chunks", flush=True)
    return _join_recovery_texts(parts)


class _TurnAssembler:
    """Bounded Segment/Turn coalescer; maxlen is not a sentence boundary.

    Source speech spans retain their original clock. Drop only actual PCM
    overlap on a contiguous segment boundary. A complete Turn publishes once.
    """
    def __init__(self, max_audio_sec=16.):
        self.max_samples = int(max_audio_sec * SR)
        self.pending = None

    def offer(self, job):
        if job["kind"] != "final":
            return job
        prev = self.pending
        if prev is None:
            if job.get("reason") == "maxlen":
                self.pending = job
                return None
            return job
        head = np.asarray(prev["audio"], dtype=np.float32)
        tail = np.asarray(job["audio"], dtype=np.float32)
        overlap = int(round((prev["t_start"] + len(head) / SR - job["t_start"]) * SR))
        overlap = max(0, min(len(tail), overlap))
        combined = np.concatenate((head, tail[overlap:]))
        ready = dict(job, audio=combined, audio_samples=len(combined),
                     t_start=prev["t_start"],
                     reason="turn_" + str(job.get("reason") or "unknown"),
                     source_segments=prev.get("source_segments", (prev["seg"],))+
                                     (job["seg"],))
        if job.get("reason") == "maxlen" and len(combined) < self.max_samples:
            self.pending = ready
            return None
        self.pending = None
        return ready

    def flush(self):
        pending, self.pending = self.pending, None
        if pending is not None:
            pending["reason"] = "turn_shutdown"
        return pending


def _is_retryable_asr_error(exc):
    """Retry temporary transport failures, not malformed requests/model errors."""
    from urllib.error import HTTPError, URLError
    if isinstance(exc, HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (TimeoutError, ConnectionError, URLError, OSError))


def _write_recovery_audio(jobs, directory, subfolder=""):
    """Write unfinished Final audio after a forced shutdown; never silently drop it."""
    import hashlib
    import tempfile
    import wave
    from pathlib import Path
    folder = Path(directory) / "asr_recovery"
    if subfolder:
        folder = folder / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    created = []
    for job in jobs:
        audio = np.asarray(job["audio"], dtype=np.float32)
        identity = hashlib.sha256(audio.tobytes() + repr((job["seg"], job["t_q"])).encode()).hexdigest()
        dest = folder / f"seg_{job['seg']:07d}_{identity}.wav"
        if not dest.is_file():
            fd, temporary = tempfile.mkstemp(prefix=".recovery-", suffix=".tmp", dir=folder)
            try:
                with os.fdopen(fd, "wb") as stream:
                    with wave.open(stream, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(SR)
                        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        created.append(str(dest))
    return created



class _FinalBacklogSpool:
    """Bound Final RAM while letting VAD/capture proceed during slow inference.

    Audio spilled to WAV is private. Successful jobs are deleted; unfinished WAVs
    intentionally remain in asr_recovery/live_pending for manual restoration.
    """
    MAX_BYTES = 256 * 1024 * 1024
    MAX_JOBS = 4096

    def __init__(self, directory):
        from pathlib import Path
        self.directory = directory
        self.folder = Path(directory) / "asr_recovery" / "live_pending"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.bytes = sum(f.stat().st_size for f in self.folder.glob('*.wav'))
        self.staged = 0
        self._bytes_lock = threading.Lock()
        if self.bytes:
            print("⚠ 上次未完成的 ASR 音频仍保留在 asr_recovery/live_pending；"
                  "不会自动重新识别以免重复输出。", flush=True)

    def stage(self, job):
        projected = 44 + len(job["audio"]) * 2
        with self._bytes_lock:
            if self.bytes + projected > self.MAX_BYTES:
                raise OSError("ASR 磁盘缓冲已达 256 MiB 上限；停止采集以避免静默丢字")
            path = _write_recovery_audio([job], self.directory, "live_pending")[0]
            from pathlib import Path
            self.bytes += Path(path).stat().st_size
            self.staged += 1
            if self.staged == 1 or self.staged % 50 == 0:
                print(f"⚠ ASR 识别速度低于采集速度，已将 {self.staged} 段转入临时磁盘缓冲。", flush=True)
            return {**job, "audio": None, "spool_path": path}

    @staticmethod
    def load(job):
        if job.get("audio") is not None:
            return job["audio"]
        import wave
        with wave.open(job["spool_path"], "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, SR):
                raise ValueError("Unexpected buffered ASR audio format")
            data = wav.readframes(wav.getnframes())
        return np.frombuffer(data, dtype='<i2').astype(np.float32) / 32767.0

    def completed(self, job):
        path = job.get("spool_path")
        if path:
            from pathlib import Path
            file = Path(path)
            with self._bytes_lock:
                try:
                    size = file.stat().st_size
                    file.unlink()
                    self.bytes = max(0, self.bytes - size)
                except FileNotFoundError:
                    pass


def _preserve_final_jobs(jobs, directory):
    """Keep already-spooled audio; atomically save any remaining in-RAM Finals."""
    saved = []
    for job in jobs:
        if job.get("spool_path"):
            from pathlib import Path
            if not Path(job["spool_path"]).is_file():
                raise FileNotFoundError(job["spool_path"])
            saved.append(job["spool_path"])
        else:
            saved.extend(_write_recovery_audio([job], directory))
    return saved


def _clean(text):
    # Repetition alone is not evidence of hallucination (laughter, names,
    # acknowledgements and numbers can all repeat legitimately).
    text = text.strip()
    text = re.sub(r"\b([A-Za-z0-9])((?: [A-Za-z0-9])+)\b", lambda m: m.group(0).replace(" ", ""), text)  # "B L G" -> "BLG"
    text = re.sub(r"(?<=[一-鿿]) (?=[A-Za-z0-9])|(?<=[A-Za-z0-9]) (?=[一-鿿])", "", text)  # 中英之间去空格
    return text



def _choose_quiet_cut(audio, sample_rate=SR):
    """Find a low-energy word boundary within 480ms of a hard cut."""
    total = len(audio)
    frame = max(1, int(.016 * sample_rate))
    if total < frame * 6:
        return total
    lo = max(frame, total - int(.48 * sample_rate))
    hi = total - int(.08 * sample_rate)
    candidates = []
    for begin in range(lo, hi, frame):
        part = audio[begin:begin + frame]
        if len(part) < frame:
            break
        candidates.append((float(np.mean(np.asarray(part, dtype=np.float32)**2)), begin + frame // 2))
    if not candidates:
        return total
    energy, at = min(candidates, key=lambda x: x[0])
    recent_energy = float(np.mean(np.asarray(audio[lo:total], dtype=np.float32)**2))
    return at if energy < recent_energy * .15 and energy < .0004 else total

class _PartialStabilizer:
    """Confirm a prefix across hypotheses, but unlock consistently corrected text."""
    def __init__(self, confirm_count=3):
        self.confirm_count = max(2, int(confirm_count))
        self.history = collections.deque(maxlen=self.confirm_count)
        self.reset()

    def reset(self):
        self.history.clear()
        self.stable = self.display = ''
        self.conflicts = 0

    def update(self, raw_text):
        candidate = _clean(raw_text)
        self.history.append(candidate)
        if len(self.history) == self.confirm_count:
            common = os.path.commonprefix(list(self.history))
            if common.startswith(self.stable) and len(common)>len(self.stable):
                self.stable = common
        if self.stable and not candidate.startswith(self.stable):
            self.conflicts += 1
            if self.conflicts < self.confirm_count:
                return self.display, self.stable
            self.stable = os.path.commonprefix([self.stable, candidate])
            self.history.clear()
            self.history.append(candidate)
            self.conflicts = 0
        else:
            self.conflicts = 0
        self.display = candidate
        return candidate, self.stable


def run_asr(args, sink, stop, status=None):
    import collections

    _validate_vad_window(args.vad_win)
    count = getattr(args, 'stable_count', 3)
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 10:
        raise ValueError("--stable-count must be an integer in range 2..10")
    if status is not None:
        status_callback = status
        def status(message):
            try:
                status_callback(message)
            except Exception as exc:
                print(f"Status output failed: {exc!r}", flush=True)

    pa = pyaudio.PyAudio()
    be = dbg = None
    try:
        dev = get_loopback_device(pa, args.device)
        print(f"采集: {dev['name']}  ({int(dev['defaultSampleRate'])} Hz, {int(dev['maxInputChannels'])} ch)")
        hot = load_hotwords(args)
        if hot:
            print(f"热词: {hot}")
        here = os.path.dirname(os.path.abspath(__file__))
        t = time.time()
        if getattr(args, "backend", "llama") == "llama":
            if not args.llama_server and not args.llama_url:
                args.llama_server = find_llama_server(here)
            model = args.model or os.path.join(here, "models", "Qwen3-ASR-1.7B-Q8_0.gguf")
            mmproj = args.mmproj or os.path.join(here, "models", "mmproj-Qwen3-ASR-1.7B-bf16.gguf")
            print(f"加载模型 {os.path.basename(model)} (llama.cpp) …", flush=True)
            if status:
                status("加载模型 (llama.cpp) …")
            backend_options = {"cancel": stop} if getattr(LlamaBackend, "supports_cancel", False) else {}
            be = LlamaBackend(args.llama_server, model, mmproj, args.lang, hot,
                              args.context, args.llama_port, args.llama_url, args.slots, **backend_options)
        else:
            model = args.model or "Qwen/Qwen3-ASR-1.7B"
            print(f"加载模型 {model} (transformers) …", flush=True)
            if status:
                status(f"加载模型 {model} …")
            backend_options = {"cancel": stop} if getattr(QwenBackend, "supports_cancel", False) else {}
            be = QwenBackend(model, args.lang, hot, args.context, **backend_options)
        # Keep the cached model warm; short Silero windows will be evaluated over
        # bounded overlapping history rather than treated as independent segments.
        detector = RollingVadDetector(args.vad_threshold, args.vad_min_speech_ms,
                                      args.vad_min_silence_ms, args.vad_speech_pad_ms)
        be.warmup()
        print(f"模型就绪 {time.time() - t:.1f}s。开始监听。", flush=True)
        if status:
            status("● 监听中")
        dbg = DebugLog(args.debug) if args.debug else None

        cv = threading.Condition()
        publish_lock = threading.Lock()
        final_q = collections.deque()
        partial_slot = [None]
        partial_done = {}
        retired_upto = [-1]  # Monotonic segment ID; never accumulate an unbounded set.
        final_closed = [False]
        final_inflight = [None]
        in_memory_finals = [0]
        spooled_jobs = []   # Segments which could not be enqueued on disk failure.
        spill_pending = collections.deque()
        spill_errors = []
        spool = _FinalBacklogSpool(here)
        shutdown_abandoned = [False]
        partial_stopping = [False]
        partial_generation = [0]

        def log_dbg(job, text, t0, t1, reused=False, audio_data=None):
            if dbg is None:
                return
            rec = {"seg": job["seg"], "kind": job["kind"], "reason": job.get("reason"),
                   "reused": reused,
                   "audio_sec": round(job.get("audio_samples", len(job["audio"])
                       if job["audio"] is not None else 0) / SR, 2),
                   "speech_end": round(job["t_end"] - dbg.t0, 2),
                   "queued": round(job["t_q"] - dbg.t0, 2),
                   "infer_start": round(t0 - dbg.t0, 2), "infer_sec": round(t1 - t0, 2),
                   "latency": round(t1 - job["t_end"], 2), "text": text}
            # Spilled Finals still produce --debug WAVs when explicitly requested.
            dbg.write(rec, (job["audio"] if job["audio"] is not None else audio_data)
                      if job["kind"] == "final" else None)

        def worker_final():
            while True:
                with cv:
                    while not final_q and not final_closed[0] and not shutdown_abandoned[0]:
                        cv.wait()
                    while final_q and final_q[0].get('spooling') and not shutdown_abandoned[0]:
                        cv.wait()
                    if shutdown_abandoned[0] or not final_q:
                        return
                    job = final_q.popleft()
                    if job["audio"] is not None:
                        in_memory_finals[0] -= 1
                    final_inflight[0] = job
                    cv.notify_all()  # release bounded producer backpressure
                t0 = time.time()
                reused = job["text"] is not None
                audio = None
                text = ""
                failed = False
                delivered = False
                try:
                    for attempt in range(3):
                        try:
                            audio = spool.load(job)
                            text = _transcribe_final_with_budget(be, audio)
                            if not text:
                                raise RuntimeError("ASR returned empty text for qualified speech")
                            break
                        except Exception as e:
                            if attempt >= 2 or not _is_retryable_asr_error(e):
                                failed = True
                                print(f"最终识别失败 seg={job['seg']} attempts={attempt+1}: {e!r}", flush=True)
                                break
                            print(f"最终识别临时失败 seg={job['seg']}, 重试 {attempt+1}/2: {e!r}", flush=True)
                            time.sleep(0.25 * (attempt + 1))
                    t1 = time.time()
                    with publish_lock:
                        with cv:
                            abandoned = shutdown_abandoned[0]
                        if not abandoned and text:
                            be.last = text
                            try:
                                reason_aware = getattr(sink, "final_segment", None)
                                if reason_aware:
                                    delivered = reason_aware(text, job["t_start"], job["t_end"], job.get("reason"))
                                else:
                                    delivered = sink.final(text, job["t_start"], job["t_end"])
                                if delivered is False:
                                    raise OSError("One or more Final outputs failed")
                                delivered = True
                            except Exception as e:
                                print(f"最终字幕输出失败 seg={job['seg']}: {e!r}", flush=True)
                                try:
                                    _preserve_final_jobs([job], here)
                                except Exception as audio_error:
                                    print(f"恢复音频保存失败: {audio_error!r}", flush=True)
                    if not abandoned:
                        if failed:
                            # Even permanent model errors should not silently erase
                            # the only copy of captured speech. Recovery WAVs are
                            # local and may contain sensitive audio; user can delete.
                            try:
                                saved = _preserve_final_jobs([job], here)
                                print(f"识别失败音频已保存: {saved[0]}", flush=True)
                                if status:
                                    status("⚠ 识别失败，音频已保留；请查看日志")
                            except Exception as save_error:
                                print(f"识别失败且无法保存恢复音频: {save_error!r}", flush=True)
                                if status:
                                    status("⚠ 识别失败且音频保存失败；请检查磁盘与日志")
                        if delivered:
                            # Publish completed before removing the only recovery WAV.
                            # Cleanup failures must not kill the Final worker.
                            try:
                                spool.completed(job)
                            except OSError as exc:
                                print(f"ASR 缓冲文件清理失败 seg={job['seg']}: {exc!r}", flush=True)
                        try:
                            log_dbg(job, text, t0, t1, reused, audio_data=audio)
                        except Exception as e:
                            print(f"ASR 调试日志写入失败 seg={job['seg']}: {e!r}", flush=True)
                finally:
                    with cv:
                        final_inflight[0] = None
                        cv.notify_all()

        def worker_spill():
            while True:
                with cv:
                    while not spill_pending and not final_closed[0] and not shutdown_abandoned[0]:
                        cv.wait()
                    if shutdown_abandoned[0] or not spill_pending:
                        return
                    job = spill_pending.popleft()
                try:
                    staged = spool.stage(job)
                    with cv:
                        job['spool_path'] = staged['spool_path']
                        job['audio'] = None
                        job['spooling'] = False
                        in_memory_finals[0] -= 1
                        cv.notify_all()
                except Exception as exc:
                    with cv:
                        job['spooling'] = False
                        spill_errors.append(exc)
                        stop.set()
                        cv.notify_all()
                    print(f"ASR 异步磁盘缓冲失败，停止采集并保留音频: {exc!r}", flush=True)

        def worker_partial():
            stabilizer = _PartialStabilizer(getattr(args, 'stable_count', 3))
            active_segment = None
            while True:
                with cv:
                    while partial_slot[0] is None and not partial_stopping[0]:
                        cv.wait()
                    if partial_stopping[0]:
                        return
                    job, partial_slot[0] = partial_slot[0], None
                    # An already queued Final always wins over starting another
                    # Partial. An already running inference cannot be preempted.
                    if final_q or final_inflight[0] is not None:
                        continue
                t0 = time.time()
                try:
                    text = be.transcribe(job["audio"])
                except Exception as e:
                    print("临时识别出错:", repr(e), flush=True)
                    continue
                t1 = time.time()
                # Output can block independently of inference. Never hold the
                # queue condition across user/file/UI callbacks.
                with publish_lock:
                    with cv:
                        if (partial_stopping[0] or job["seg"] <= retired_upto[0]
                                or job["generation"] != partial_generation[0]):
                            continue
                    if active_segment != job['seg']:
                        stabilizer.reset()
                        active_segment = job['seg']
                    display, stable = stabilizer.update(text)
                    try:
                        if display:
                            stable_sink = getattr(sink, 'partial_stable', None)
                            if stable_sink:
                                stable_sink(display, stable)
                            else:
                                sink.partial(display)
                    except Exception as e:
                        print(f"临时字幕输出失败: {e!r}", flush=True)
                try:
                    log_dbg(job, text, t0, t1)
                except Exception as e:
                    print(f"ASR 调试日志写入失败: {e!r}", flush=True)

        threads = [threading.Thread(target=worker_final, name="asr-final", daemon=True),
                   threading.Thread(target=worker_partial, name="asr-partial", daemon=True),
                   threading.Thread(target=worker_spill, name="asr-spill", daemon=True)]
        for th in threads:
            th.start()

        def submit(kind, audio, seg, t_start, t_end, reason=None, text=None):
            job = {"kind": kind, "audio": audio.copy(), "audio_samples": len(audio), "seg": seg,
                   "t_start": t_start, "t_end": t_end, "t_q": time.time(),
                   "reason": reason, "text": text}
            if kind == "final" and 2.0 <= turn_max_seg <= 8.0:
                job = turn_assembler.offer(job)
                if job is None:
                    return
            with cv:
                seg = job["seg"]
                if kind == "final":
                    retired_upto[0] = max(retired_upto[0], seg)
                    partial_generation[0] += 1
                    if partial_slot[0] is not None and partial_slot[0]["seg"] == seg:
                        partial_slot[0] = None
                    partial_done.pop(seg, None)
                    # Disk spill instead of waiting: the capture consumer must
                    # not be blocked by an ASR backlog and overflow its 12.8s ring.
                    # Queue ordering is unchanged; only the audio payload moves.
                    if shutdown_abandoned[0]:
                        spooled_jobs.append(job)
                        return
                    if len(final_q) >= spool.MAX_JOBS:
                        spooled_jobs.append(job)
                        stop.set()
                        raise RuntimeError("ASR Final 排队超过安全任务上限")
                    if in_memory_finals[0] >= 16:
                        if len(spill_pending) >= 32:
                            spooled_jobs.append(job)
                            stop.set()
                            raise RuntimeError("ASR 异步磁盘写入队列已满；采集停止并保留音频")
                        job['spooling'] = True
                        spill_pending.append(job)
                    in_memory_finals[0] += 1
                    final_q.append(job)
                else:
                    # Reduce contention: Final work takes priority under backlog.
                    if final_q or final_inflight[0] is not None:
                        return
                    partial_generation[0] += 1
                    job["generation"] = partial_generation[0]
                    partial_slot[0] = job   # only latest pending partial
                cv.notify_all()

        # Bounded capture buffer (~12.8 s at 0.1 s/callback). A sustained stall
        # stops recording with an explicit overrun report, never silent unbounded RAM.
        audio_q = queue.Queue(maxsize=256)  # 50ms callbacks: ~12.8 s
        audio_processor = AudioFrameProcessor(int(dev['defaultSampleRate']), int(dev['maxInputChannels']))
        overflowed = threading.Event()
        capture_errors = []
        stream = None
        frame_buf = np.empty(0, dtype=np.float32)
        win_samples = getattr(detector, 'FRAME', max(1, int(SR * args.vad_win)))
        seg_parts = []  # Append VAD windows; concatenate once for a Final/Partial.
        seg_samples = 0
        seg_start_sample = None
        last_speech_sample = None
        speech_samples = 0
        previous_cutoff = 0
        seg_id = 0
        # Pre-dispatch before another <=max_seg segment could exceed 16s.
        # This is a bounded, near-16s Turn; do not merge user-configured 30s
        # segments because a single input already exceeds this budget.
        turn_max_seg = float(getattr(args, "max_seg", 6.))
        turn_assembler = _TurnAssembler(max_audio_sec=16. - min(turn_max_seg, 8.))
        run_peak = 0.0
        last_partial_sample = 0
        carry_overlap = 0
        start_wall = time.time()
        t_run0 = start_wall
        clock_anchors = [(0, 0.)]
        idle_no_pcm_since = [None]
        set_capture_origin = getattr(sink, "set_capture_origin", None)
        if set_capture_origin is not None:
            set_capture_origin(start_wall)

        def stamp(sample):
            for index, offset in reversed(clock_anchors):
                if sample >= index:
                    return start_wall + sample / SR + offset
            return start_wall + sample / SR

        def consume(window):
            nonlocal seg_parts, seg_samples, seg_start_sample, last_speech_sample, speech_samples
            nonlocal previous_cutoff, seg_id, last_partial_sample, carry_overlap
            is_active = detector.feed(window)
            pos = detector.total_samples
            spans = [sp for sp in detector.last_spans if sp["end"] > previous_cutoff]
            if seg_start_sample is None:
                if not spans:
                    return
                # Recover audio before the first qualified speech event. The
                # timestamp from VAD already includes the requested speech pad.
                seg_start_sample = max(previous_cutoff, spans[0]["start"])
                seg_parts = [detector.slice_recent(seg_start_sample)]
                seg_samples = len(seg_parts[0])
                last_speech_sample = max(seg_start_sample, spans[-1]["end"])
                speech_samples = max(0, last_speech_sample - seg_start_sample)
                last_partial_sample = pos
                carry_overlap = 0
            else:
                seg_parts.append(window)
                seg_samples += len(window)
                if spans:
                    last_speech_sample = max(last_speech_sample, spans[-1]["end"])
                    speech_samples = max(speech_samples, last_speech_sample - seg_start_sample)
            seg_len = seg_samples / SR
            sil = max(0, pos - last_speech_sample) / SR
            reason = None
            if (sil >= args.silence and seg_len >= args.min_seg) or sil >= args.silence * 2.5:
                reason = "silence"
            elif seg_len >= (min(args.max_seg, 30.0) if args.max_seg > 0 else 30.0):
                reason = "maxlen"
            if reason:
                segment_audio = np.concatenate(seg_parts)
                if reason == "maxlen" and sil < .20:
                    cut = _choose_quiet_cut(segment_audio, SR)
                    overlap = min(int(.25 * SR), cut)
                    submit("final", segment_audio[:cut], seg_id, stamp(seg_start_sample),
                           stamp(seg_start_sample + cut), reason)
                    leftover = segment_audio[cut-overlap:]
                    seg_start_sample += cut - overlap
                    seg_parts = [leftover] if len(leftover) else []
                    seg_samples = len(leftover)
                    carry_overlap = overlap
                    previous_cutoff = seg_start_sample
                    last_partial_sample = pos
                else:
                    if seg_samples > carry_overlap:
                        submit("final", segment_audio, seg_id, stamp(seg_start_sample),
                               stamp(max(seg_start_sample, last_speech_sample)), reason)
                    previous_cutoff = pos
                    seg_parts = []
                    seg_samples = 0
                    seg_start_sample = last_speech_sample = None
                    carry_overlap = 0
                seg_id += 1
            elif (not args.no_partial and
                  (pos - last_partial_sample) / SR >= args.partial_every and seg_len >= 1.0):
                last_partial_sample = pos
                if seg_len <= 8.0:  # Avoid costly stale Partial inference on long speech.
                    submit("partial", np.concatenate(seg_parts), seg_id, stamp(seg_start_sample),
                           stamp(max(seg_start_sample, last_speech_sample)))

        # WASAPI loopback may stop callbacks when output playback goes idle.
        # Finish qualified speech from elapsed wall time, without synthesizing
        # silence samples or advancing the ASR/SRT sample clock.
        last_audio_processed_at = time.monotonic()

        def flush_idle_speech():
            nonlocal seg_parts, seg_samples, seg_start_sample, last_speech_sample
            nonlocal speech_samples, previous_cutoff, seg_id, carry_overlap, frame_buf
            if seg_start_sample is None:
                return
            # A final sub-512-sample VAD fragment belongs to this segment.
            # Consume it exactly once before retiring the idle Final, or the
            # shutdown drain may reopen the retired turn and publish a duplicate.
            if len(frame_buf):
                pending_tail = frame_buf
                frame_buf = np.empty(0, dtype=np.float32)
                consume(pending_tail)
            if seg_start_sample is None or seg_samples <= carry_overlap:
                return
            idle_sec = max(0., time.monotonic() - last_audio_processed_at)
            captured_silence = max(0, detector.total_samples - last_speech_sample) / SR
            total_silence = captured_silence + idle_sec
            length = seg_samples / SR
            if not ((total_silence >= args.silence and length >= args.min_seg)
                    or total_silence >= args.silence * 2.5):
                return
            submit("final", np.concatenate(seg_parts), seg_id,
                   stamp(seg_start_sample),
                   stamp(max(seg_start_sample, last_speech_sample)), "silence_idle")
            previous_cutoff = detector.total_samples
            seg_parts = []
            seg_samples = 0
            seg_start_sample = last_speech_sample = None
            speech_samples = 0
            carry_overlap = 0
            seg_id += 1

        def process_chunk(chunk):
            nonlocal frame_buf, run_peak
            if not len(chunk):
                return
            pk = float(np.abs(chunk).max())
            run_peak = max(pk, run_peak * 0.998)
            if run_peak > 1e-4:
                chunk = chunk * min(args.max_gain, 0.5 / run_peak)
            frame_buf = np.concatenate((frame_buf, chunk))
            # A capture callback may deliver more than one evaluation window.
            # Do not silently discard the remaining samples.
            while len(frame_buf) >= win_samples:
                window = frame_buf[:win_samples]
                consume(window)
                frame_buf = frame_buf[win_samples:]

        try:
            stream = start_capture(pa, dev, audio_q, stop, overflowed, capture_errors)
            while not stop.is_set():
                if args.duration and time.time() - t_run0 > args.duration:
                    break
                try:
                    chunk = audio_q.get(timeout=0.2)
                except queue.Empty:
                    try:
                        active = stream.is_active() if hasattr(stream, "is_active") else True
                    except Exception as exc:
                        if status:
                            status("⚠ 音频采集中断：状态查询失败")
                        raise RuntimeError("音频采集状态查询失败") from exc
                    if not active:
                        if status:
                            status("⚠ 音频采集中断")
                        raise RuntimeError("WASAPI 音频采集中断")
                    if idle_no_pcm_since[0] is None:
                        idle_no_pcm_since[0] = last_audio_processed_at
                    flush_idle_speech()
                    continue
                if idle_no_pcm_since[0] is not None:
                    gap = max(0., time.monotonic() - idle_no_pcm_since[0])
                    if gap >= .5:
                        at = detector.total_samples
                        clock_anchors.append((at, clock_anchors[-1][1] + gap))
                        print(f"Audio timeline gap +{gap:.2f}s after sample {at}", flush=True)
                    idle_no_pcm_since[0] = None
                process_chunk(audio_processor(chunk))
                last_audio_processed_at = time.monotonic()
        finally:
            stop.set()
            cleanup_errors = []
            resampler_tail = np.empty(0, dtype=np.float32)
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception as exc:
                    cleanup_errors.append(exc)
                try:
                    stream.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
            try:
                resampler_tail = audio_processor.flush()
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                while True:
                    try:
                        chunk = audio_q.get_nowait()
                    except queue.Empty:
                        break
                    process_chunk(audio_processor(chunk))
                if len(resampler_tail):
                    process_chunk(resampler_tail)
                if len(frame_buf):
                    consume(frame_buf)
                    frame_buf = np.empty(0, dtype=np.float32)
                if hasattr(detector, 'finish'):
                    detector.finish()
                    if seg_start_sample is not None:
                        last_speech_sample = max(seg_start_sample,
                            min(last_speech_sample, detector.last_speech_end))
            except Exception as exc:
                cleanup_errors.append(exc)
                # Preserve buffers even when VAD/segmentation itself has failed.
                # The segment and pending window can overlap after a detector
                # failure; recovery deliberately favors retaining those samples.
                buffers = [np.concatenate(seg_parts) if seg_parts else np.empty(0, dtype=np.float32), frame_buf]
                while True:
                    try:
                        buffers.append(audio_processor(audio_q.get_nowait()))
                    except queue.Empty:
                        break
                if len(resampler_tail):
                    buffers.append(resampler_tail)
                raw = np.concatenate(buffers) if buffers else np.empty(0, dtype=np.float32)
                if len(raw):
                    spooled_jobs.append(dict(kind="final", audio=raw, seg=seg_id,
                                             t_q=time.time(), t_start=stamp(0),
                                             t_end=stamp(detector.total_samples),
                                             reason="processing_error", text=None))
                seg_parts = []
                seg_samples = 0
                seg_start_sample = None
            if overflowed.is_set():
                print("Audio capture overrun: stopped recording; a device/queue gap may exist.", flush=True)
                if status:
                    status("Audio capture overrun; see log")
            # Invalidate partials and enqueue tail BEFORE closing Final queue.
            # A final queued before shutdown must finish before this tail result.
            with cv:
                partial_stopping[0] = True
                partial_slot[0] = None
                partial_generation[0] += 1
                cv.notify_all()
            if seg_start_sample is not None:
                if seg_samples > carry_overlap:
                    submit("final", np.concatenate(seg_parts), seg_id, stamp(seg_start_sample),
                           stamp(max(seg_start_sample, last_speech_sample)), "shutdown")
            remaining_turn = turn_assembler.flush()
            if remaining_turn is not None:
                submit("final", remaining_turn["audio"], remaining_turn["seg"],
                       remaining_turn["t_start"], remaining_turn["t_end"],
                       remaining_turn["reason"], remaining_turn.get("text"))
            with cv:
                final_closed[0] = True
                cv.notify_all()
            if spooled_jobs:
                try:
                    saved = _preserve_final_jobs(spooled_jobs, here)
                    print(f"⚠ Final 队列积压：{len(saved)} 段未识别音频已保存至 asr_recovery/；请稍后重新识别。", flush=True)
                except Exception as save_error:
                    print(f"⚠ Final 队列积压且恢复音频保存失败: {save_error!r}", flush=True)
            # Finite exit grace even if an ASR backend hangs indefinitely. On
            # timeout, export unfinished Final audio for a later manual replay.
            deadline = time.monotonic() + 45.0
            for th in threads:
                th.join(timeout=max(0., deadline - time.monotonic()))
            if any(th.is_alive() for th in threads):
                with cv:
                    shutdown_abandoned[0] = True
                    pending = ([final_inflight[0]] if final_inflight[0] else []) + list(final_q)
                    cv.notify_all()
                if pending:
                    try:
                        saved = _preserve_final_jobs(pending, here)
                        print(f"⚠ 超时退出，已保存 {len(saved)} 段未完成的 Final 音频至 asr_recovery/，请妥善保管。", flush=True)
                    except Exception as e:
                        print(f"⚠ 超时退出，未能保存 {len(pending)} 段 Final 音频：{e!r}", flush=True)
                print("⚠ ASR 线程超时；已停止等待。未完成任务无法保证输出字幕。", flush=True)
            if spill_errors:
                raise RuntimeError("ASR asynchronous spill failed") from spill_errors[0]
            if capture_errors:
                raise RuntimeError("WASAPI callback failed") from capture_errors[0]
            if overflowed.is_set():
                raise RuntimeError("WASAPI audio discontinuity/queue overrun: audio may be incomplete")
            if cleanup_errors:
                raise RuntimeError("Audio cleanup/processing failed: " + repr(cleanup_errors[0])) from cleanup_errors[0]

    finally:
        try:
            if dbg is not None:
                dbg.f.close()
        finally:
            try:
                if be is not None and hasattr(be, "close"):
                    be.close()
            finally:
                pa.terminate()


def _validate_vad_window(value):
    import math
    if not math.isfinite(value) or int(SR * value) < 1:
        raise ValueError("--vad-win must be positive and at least one audio sample")



# ============================ 入口 ============================
# GUI launcher adapter. Backend wrappers preserve configurable generation settings.
# Local llama-server launcher: honor UI ctx/slots and disable the RAM prompt cache.
_LC_RUNTIME_CTX_SIZE = 8192
_LC_RUNTIME_MAX_TOKENS = 200
_LC_RUNTIME_TEMPERATURE = 0.0
_LC_RUNTIME_CONTEXT_HISTORY = 1
_LC_RUNTIME_CONTEXT_CHARS = 80

class _ASRCancelled(RuntimeError):
    """An explicitly cancelled ASR request; never retry it as a transport fault."""


_ASR_DNS_SLOTS = threading.BoundedSemaphore(2)


def _asr_resolve(host, port, token):
    """Bound resolver work, including cancelled calls still inside the OS."""
    import ipaddress
    import socket
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            address = None
    if address is not None:
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        sockaddr = (str(address), port) if address.version == 4 else (str(address), port, 0, 0)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr)]
    slots = _ASR_DNS_SLOTS
    while not slots.acquire(timeout=.04):
        token.check()
    done, result = threading.Event(), []
    def resolve():
        try:
            result.append((socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM), None))
        except BaseException as exc:
            result.append((None, exc))
        finally:
            # A cancelled caller cannot release a still-running resolver slot.
            slots.release()
            done.set()
    try:
        token.check()
        worker = threading.Thread(target=resolve, name='asr-dns', daemon=True)
        worker.start()
    except BaseException:
        slots.release()
        raise
    while not done.wait(.04):
        token.check()
    token.check()
    addresses, error = result[0]
    if error is not None:
        raise error
    return addresses


def _asr_connect(address, timeout, source_address, token):
    """Resolve and connect without an uninterruptible create_connection wait."""
    import errno
    import select
    import socket
    host, port = address
    addresses = _asr_resolve(host, port, token)
    deadline = min(token.deadline, time.monotonic() + timeout)
    error = OSError("ASR host resolved to no usable addresses")
    pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036, 10037}
    for family, kind, protocol, _, target in addresses:
        sock = socket.socket(family, kind, protocol)
        try:
            token.check()
            if source_address:
                sock.bind(source_address)
            sock.setblocking(False)
            code = sock.connect_ex(target)
            if code and code not in pending:
                raise OSError(code, os.strerror(code))
            while code:
                token.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ASR connection timed out")
                _, writable, exceptional = select.select([], [sock], [sock], min(.04, remaining))
                if writable or exceptional:
                    code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if code:
                        raise OSError(code, os.strerror(code))
                    break
            token.check()
            token.register(sock)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
        except BaseException:
            sock.close()
            raise
    token.check()
    raise error


def _asr_json_request(url, data=None, *, timeout=60., cancel_events=(), headers=None):
    """Use the bundled polling transport for a total, cancellable IO budget.

    Only warmup observes the capture-stop Event. Final drain requests observe
    backend close instead, allowing queued speech to finish after capture ends.
    """
    from translation.api import (CancellationToken, TranslationCancelled,
                                 _TrackedHTTPConnection, _TrackedHTTPSConnection)
    from urllib.request import Request, build_opener, HTTPHandler, HTTPSHandler, ProxyHandler
    from urllib.parse import urlsplit
    from urllib.error import URLError, HTTPError
    import ipaddress
    import socket

    class RequestToken(CancellationToken):
        def check(self):
            if any(event is not None and event.is_set() for event in cancel_events):
                self.cancel()
                raise _ASRCancelled("ASR request cancelled")
            super().check()

    token = RequestToken()
    token.start_request(timeout)
    body = None if data is None else json.dumps(data).encode('utf-8')
    request_headers = {'Content-Type': 'application/json'}
    request_headers.update(headers or {})
    request = Request(url, data=body, headers=request_headers)
    class ResolverConnection:
        def __init__(self, *args, **kwargs):
            super().__init__(*args, cancel_token=token, **kwargs)
            self._create_connection = lambda address, timeout, source_address=None: _asr_connect(
                address, timeout, source_address, token)
    class HTTPConnection(ResolverConnection, _TrackedHTTPConnection):
        pass
    class HTTPSConnection(ResolverConnection, _TrackedHTTPSConnection):
        pass
    class HTTP(HTTPHandler):
        def http_open(self, request):
            return self.do_open(HTTPConnection, request)
    class HTTPS(HTTPSHandler):
        def https_open(self, request):
            return self.do_open(HTTPSConnection, request)
    host = urlsplit(url).hostname
    # Local ASR audio must stay on loopback even when an HTTP proxy is set.
    local = bool(host and host.rstrip('.').lower() == 'localhost')
    if not local and host:
        try:
            address = ipaddress.ip_address(host)
            mapped = getattr(address, 'ipv4_mapped', None)
            local = address.is_loopback or bool(mapped and mapped.is_loopback)
        except ValueError:
            try:
                # Match legacy numeric forms accepted by the socket API, such
                # as 127.1, without consulting DNS or an external proxy.
                local = ipaddress.ip_address(socket.inet_aton(host)).is_loopback
            except OSError:
                pass
    handlers = [ProxyHandler({})] if local else []
    opener = build_opener(*handlers, HTTP(), HTTPS())
    try:
        # Connection attempts have a short independent limit. Response reads
        # use the token's total deadline, even if bytes arrive continuously.
        with opener.open(request, timeout=min(2., timeout)) as response:
            payload = response.read(4 * 1024 * 1024 + 1)
        token.check()
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError("ASR server response exceeds 4 MiB")
        return json.loads(payload.decode('utf-8'))
    except TranslationCancelled as exc:
        raise _ASRCancelled("ASR request cancelled") from exc
    except HTTPError as exc:
        # We classify ASR errors by status and never consume their body. Close
        # the original response as well as the token's duplicated socket.
        exc.close()
        token.check()
        raise
    except URLError as exc:
        token.check()
        if isinstance(exc.reason, TimeoutError):
            raise exc.reason from exc
        raise
    finally:
        token.finish_request()


class _LCContextHistoryMixin:
    @property
    def last(self):
        return "\n".join(getattr(self, "_lc_history", []))

    @last.setter
    def last(self, text):
        if not text:
            self._lc_history = []
        else:
            history = getattr(self, "_lc_history", [])
            history.append(str(text))
            self._lc_history = history[-getattr(self, "_lc_context_history", 1):]

class _LCContextBackend(_LCContextHistoryMixin, LlamaBackend):
    supports_cancel = True
    supports_token_budget = True

    def __init__(self, server_exe, model, mmproj, lang, hotwords, use_context, port=8765, url=None, slots=2, ctx_size=None, cancel=None):
        self.cancel = cancel
        self._request_closed = threading.Event()
        self.request_timeout = 60.
        self._restart_lock = threading.Lock()
        self._server_generation = 1
        self._retry_after = 0.
        self._server_cmd = None
        requested_ctx = _LC_RUNTIME_CTX_SIZE if ctx_size is None else ctx_size
        ctx_size = requested_ctx
        import subprocess, urllib.request, socket, json
        self.urllib = urllib.request
        self.lang = QWEN_LANG.get(lang, lang) if lang else None
        self.hot = hotwords or ""
        self.use_context = use_context
        self._lc_context_history = _LC_RUNTIME_CONTEXT_HISTORY
        self._lc_context_chars = _LC_RUNTIME_CONTEXT_CHARS
        self._lc_max_tokens = _LC_RUNTIME_MAX_TOKENS
        self._lc_temperature = _LC_RUNTIME_TEMPERATURE
        self.last = ""
        self.proc = None
        self.job = None
        self._lifecycle_lock = threading.RLock()
        if url is None:
            # 端口上已经有一个跑着同一个模型的 server（上次没关干净）就直接复用；被别的东西占着就换端口
            existing = self._probe(f"http://127.0.0.1:{port}", model)
            # A running server may use a different -c/-np even when the model matches.
            # Start a dedicated server so UI settings are never silently ignored.
            if existing != "none":
                with socket.socket() as sk:
                    sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            cmd = [server_exe, "-m", model, "--mmproj", mmproj, "-ngl", "99", "--port", str(port),
                   "-c", str(ctx_size), "--cache-ram", "0", "--no-webui", "-np", str(slots), "--log-disable"]
            self._server_cmd = list(cmd)
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                self._bind_job(self.proc)
            except BaseException:
                self.close()
                raise
        else:
            self.url = url
            print("ASR external server: local ctx, slots and cache-ram flags do not apply.", flush=True)
        try:
            self._wait_health()
        except BaseException:
            self.close()
            raise

    def _wait_health(self, startup=True, timeout=180.):
        if startup and self.cancel is not None and self.cancel.is_set():
            raise _ASRCancelled("ASR startup cancelled")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self, '_request_closed', None) is not None and self._request_closed.is_set():
                raise _ASRCancelled("ASR backend closed")
            if startup and self.cancel is not None and self.cancel.is_set():
                raise _ASRCancelled("ASR startup cancelled")
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited: {self.proc.returncode}")
            try:
                self._json_request(self.url + "/health", timeout=min(2., max(.01, deadline-time.monotonic())), startup=startup)
                return
            except _ASRCancelled:
                raise
            except Exception:
                pass
            if startup and self.cancel is not None:
                self.cancel.wait(.5)
            else:
                self._request_closed.wait(.5)
        raise RuntimeError("llama-server health timeout")

    def _json_request(self, url, data=None, *, timeout=60., startup=False):
        events = [getattr(self, '_request_closed', None)]
        if startup:
            events.append(getattr(self, 'cancel', None))
        return _asr_json_request(url, data, timeout=timeout, cancel_events=events)

    def warmup(self):
        self.transcribe(np.zeros(SR, dtype=np.float32), _startup=True)

    def close(self):
        self._request_closed.set()
        super().close()

    def _stop_owned_server(self):
        # Reap our child without permanently cancelling the backend. Public
        # close() sets the cancellation event before entering this same lock.
        super().close()

    def _recover_server(self, generation, startup=False):
        """At most one restart per failed generation, never restart a borrowed server."""
        if not self._server_cmd or self._request_closed.is_set():
            return False
        with self._restart_lock:
            if self._request_closed.is_set():
                return False
            if generation != self._server_generation:
                return True
            if time.monotonic() < self._retry_after:
                return False
            try:
                self._wait_health(startup=startup, timeout=2.)
                return False  # A busy/live server must not be killed for one request.
            except _ASRCancelled:
                raise
            except Exception:
                pass
            self._retry_after = time.monotonic()+5.
            import subprocess
            with self._lifecycle_lock:
                if self._request_closed.is_set():
                    return False
                self._stop_owned_server()
                if self._request_closed.is_set():
                    return False
                if startup and self.cancel is not None and self.cancel.is_set():
                    raise _ASRCancelled("ASR startup cancelled before restart")
                try:
                    self.proc = subprocess.Popen(self._server_cmd, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.job = self._bind_job(self.proc)
                except BaseException:
                    if self.proc is not None:
                        self._stop_owned_server()
                    raise
            try:
                self._wait_health(startup=startup)
            except BaseException:
                self._stop_owned_server()
                raise
            self._server_generation += 1
            return True

    def transcribe(self, audio, _startup=False, max_tokens=None):
        import json
        ctx = self.hot
        if self.use_context and self.last:
            ctx = (ctx + "\n" + self.last[-getattr(self, "_lc_context_chars", 80):]).strip()
        msgs = [{"role": "system", "content": ctx},
                {"role": "user", "content": [{"type": "input_audio",
                                              "input_audio": {"data": self._wav_b64(audio), "format": "wav"}}]}]
        options = {"messages": msgs,
                   "max_tokens": (getattr(self, "_lc_max_tokens", _LC_RUNTIME_MAX_TOKENS)
                                   if max_tokens is None else int(max_tokens)),
                   "temperature": getattr(self, "_lc_temperature", _LC_RUNTIME_TEMPERATURE),
                   "cache_prompt": True}
        if self.lang:
            # Qwen3-ASR language forcing is an assistant continuation, not a
            # system instruction or post-hoc language filter. llama.cpp needs
            # an API version supporting continue_final_message.
            msgs.append({"role": "assistant", "content": f"language {self.lang}<asr_text>"})
            options["continue_final_message"] = True
            options["add_generation_prompt"] = False
        try:
            generation = getattr(self, '_server_generation', 1)
            for attempt in range(2):
                try:
                    out = self._json_request(self.url + "/v1/chat/completions", options,
                                             timeout=getattr(self, 'request_timeout', 60.), startup=_startup)
                    break
                except Exception as exc:
                    if (attempt or not _is_retryable_asr_error(exc) or
                            not (self._recover_server(generation, startup=True) if _startup
                                 else self._recover_server(generation))):
                        raise
        except Exception as exc:
            from urllib.error import HTTPError
            if self.lang and isinstance(exc, HTTPError) and exc.code in (400, 422):
                raise RuntimeError(
                    "llama-server 拒绝固定语言请求；请升级到支持 "
                    "continue_final_message 的 llama.cpp 版本，或在 GUI 中选择 auto。"
                ) from exc
            raise
        choice = out["choices"][0]
        if choice.get("finish_reason") == "length":
            raise _ASROutputTruncated("ASR token output truncated; increase max_tokens or reduce max_seg")
        text = choice["message"]["content"]
        # In prefill mode the server usually returns only generated content;
        # older builds may echo the prefill, so tolerate both formats.
        if "<asr_text>" in text:
            _, text = text.split("<asr_text>", 1)
        return _clean(text)

class _LCQwenModel(_LCContextHistoryMixin, QwenBackend):
    """Preserve original HF backend source while configuring generation limit and context."""
    def __init__(self, model_name, lang, hotwords, use_context):
        self._inference_lock = threading.Lock()
        import torch, transformers
        transformers.logging.set_verbosity_error()
        from qwen_asr import Qwen3ASRModel
        self.model = Qwen3ASRModel.from_pretrained(
            model_name, dtype=torch.bfloat16, device_map="cuda:0",
            max_inference_batch_size=4, max_new_tokens=_LC_RUNTIME_MAX_TOKENS)
        self.lang = QWEN_LANG.get(lang, lang) if lang else None
        self.hot = hotwords or ""
        self.use_context = use_context
        self._lc_context_history = _LC_RUNTIME_CONTEXT_HISTORY
        self._lc_context_chars = _LC_RUNTIME_CONTEXT_CHARS
        self.last = ""

    def transcribe(self, audio, context=None):
        with self._inference_lock:
            ctx = context
            if ctx is None:
                ctx = self.hot
                if self.use_context and self.last:
                    ctx = (ctx + "\n" + self.last[-getattr(self, "_lc_context_chars", 80):]).strip()
            result = self.model.transcribe(audio=(audio, SR), context=ctx, language=self.lang)
            return _clean(result[0].text) if result else ""

    def warmup(self):
        self.transcribe(np.zeros(SR, dtype=np.float32))


_HF_WORKER_CONFIG = '_LIVE_CAPTION_HF_WORKER_CONFIG'


def _hf_worker_entry():
    """Private HF process entry, loaded from this same pyw without a GUI."""
    import hmac
    from multiprocessing import shared_memory
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    config = json.loads(os.environ.pop(_HF_WORKER_CONFIG))
    global _LC_RUNTIME_MAX_TOKENS
    _LC_RUNTIME_MAX_TOKENS = int(config['max_tokens'])
    expected = ('Bearer '+config['token']).encode('ascii')
    model = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(2.)

        def reply(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass  # A cancelled parent closes its socket before we reply.

        def authenticated(self):
            supplied = self.headers.get('Authorization', '').encode('utf-8')
            if not hmac.compare_digest(supplied, expected):
                self.reply(403, {'error': 'forbidden'})
                return False
            return True

        def do_GET(self):
            if not self.authenticated():
                return
            if self.path != '/health':
                self.reply(404, {'error': 'unknown endpoint'})
                return
            self.reply(200, {'ready': model is not None, 'pid': os.getpid(), 'session': config['session']})

        def do_POST(self):
            if not self.authenticated():
                return
            if self.path != '/transcribe':
                self.reply(404, {'error': 'unknown endpoint'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024*1024:
                    raise ValueError('invalid HF request size')
                payload = json.loads(self.rfile.read(size).decode('utf-8'))
                if payload.get('operation') == 'warmup':
                    model.warmup()
                    text = ''
                elif payload.get('operation') == 'transcribe':
                    count = payload['samples']
                    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                        raise ValueError('invalid sample count')
                    region = shared_memory.SharedMemory(name=payload['shared_memory'], create=False)
                    try:
                        if count > region.size//4:
                            raise ValueError('audio exceeds its shared-memory region')
                        audio = np.ndarray((count,), dtype='<f4', buffer=region.buf).copy()
                    finally:
                        region.close()
                    if not np.isfinite(audio).all():
                        raise ValueError('audio must contain finite samples')
                    context = payload.get('context', '')
                    if not isinstance(context, str):
                        raise ValueError('context must be text')
                    text = model.transcribe(audio, context=context)
                else:
                    raise ValueError('invalid HF operation')
                self.reply(200, {'text': text})
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, {'error': str(exc)[:300]})
            except Exception as exc:
                # Diagnostics must not log captured audio or its transcript.
                print('HF inference failed: '+type(exc).__name__, file=sys.stderr, flush=True)
                self.reply(500, {'error': type(exc).__name__})

    server = ThreadingHTTPServer(('127.0.0.1', int(config['port'])), Handler)
    server.daemon_threads = True
    try:
        model = _LCQwenModel(config['model'], config['lang'], '', False)
        server.serve_forever(poll_interval=.1)
    finally:
        server.server_close()


class _LCQwenBackend(_LCContextBackend):
    """Cancellable HF ownership: the GUI process never loads model weights."""
    name = 'Qwen3-ASR'
    supports_cancel = True

    def __init__(self, model_name, lang, hotwords, use_context, cancel=None):
        import secrets
        import socket
        self.cancel = cancel
        self._request_closed = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self.proc = self.job = self._stderr = None
        self._hf_restart_lock = threading.Lock()
        self._hf_generation = 1
        self._hf_retry_after = 0.
        self._server_cmd = None  # Never use the llama restart command for HF.
        self._worker_token = secrets.token_urlsafe(32)
        self._worker_session = secrets.token_hex(16)
        self._lc_context_history = _LC_RUNTIME_CONTEXT_HISTORY
        self._lc_context_chars = _LC_RUNTIME_CONTEXT_CHARS
        self.lang = QWEN_LANG.get(lang, lang) if lang else None
        self.hot = hotwords or ''
        self.use_context = use_context
        self.last = ''
        self.request_timeout = 60.
        if cancel is not None and cancel.is_set():
            raise _ASRCancelled('HF startup cancelled')
        # Launch the base interpreter directly: Windows venv redirectors can
        # otherwise leave the actual interpreter outside the owned Job.
        python = _LCPath(getattr(sys, '_base_executable', sys.executable))
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')
        if not python.is_file():
            raise RuntimeError('HF worker Python interpreter is missing')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        config = dict(model=model_name, lang=lang, max_tokens=_LC_RUNTIME_MAX_TOKENS,
                      port=port, token=self._worker_token, session=self._worker_session)
        encoded = json.dumps(config)
        if len(encoded)>8192:
            raise ValueError('HF model configuration is too large')
        child_env = dict(os.environ)
        child_env[_HF_WORKER_CONFIG] = encoded
        child_env['_LIVE_CAPTION_PYW_BOOTSTRAPPED'] = '1'
        child_env['PYTHONIOENCODING'] = 'utf-8'
        entry = ("import sys,site,runpy;sys.prefix=sys.exec_prefix=sys.argv[2];"
                 "[site.addsitedir(p) for p in site.getsitepackages([sys.prefix])];"
                 "runtime=runpy.run_path(sys.argv[1],run_name='_live_caption_hf_worker');"
                 "runtime['_hf_worker_entry']()")
        self._hf_command = [str(python), '-u', '-S', '-c', entry, os.path.abspath(__file__), sys.prefix]
        self._hf_env = child_env
        try:
            self._start_hf_worker(startup=True)
            self._wait_health()
        except _ASRCancelled:
            self.close()
            raise
        except BaseException as exc:
            detail = ''
            if self._stderr is not None and self.proc is not None and self.proc.poll() is not None:
                self._stderr.seek(0)
                detail = self._stderr.read()[-2000:].decode('utf-8', errors='replace')
            self.close()
            if detail:
                raise RuntimeError('HF worker startup failed: '+detail) from exc
            raise

    def _start_hf_worker(self, startup):
        import subprocess
        import tempfile
        with self._lifecycle_lock:
            if self._request_closed.is_set() or (startup and self.cancel is not None and self.cancel.is_set()):
                raise _ASRCancelled('HF startup/recovery cancelled')
            self._stderr = tempfile.TemporaryFile(mode='w+b')
            self.proc = subprocess.Popen(self._hf_command, env=self._hf_env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self._stderr,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self._bind_job(self.proc)

    def _stop_hf_worker(self):
        with self._lifecycle_lock:
            try:
                self._stop_owned_server()
            finally:
                stream, self._stderr = self._stderr, None
                if stream is not None:
                    stream.close()

    def _restart_hf_worker(self, generation, startup):
        with self._hf_restart_lock:
            if self._request_closed.is_set() or (startup and self.cancel is not None and self.cancel.is_set()):
                raise _ASRCancelled('HF recovery cancelled')
            if generation != self._hf_generation:
                return True
            if time.monotonic() < self._hf_retry_after:
                return False
            self._hf_retry_after = time.monotonic()+5.
            try:
                # Only this backend's private process is replaced. It also
                # releases a hung model lock; no shared HF server is involved.
                with self._lifecycle_lock:
                    self._stop_hf_worker()
                    self._start_hf_worker(startup=startup)
                self._wait_health(startup=startup)
            except BaseException:
                self._stop_hf_worker()
                raise
            self._hf_generation += 1
            return True

    def _request_hf(self, payload, startup=False):
        generation = self._hf_generation
        for attempt in range(2):
            try:
                return self._json_request(self.url+'/transcribe', payload,
                                          timeout=self.request_timeout, startup=startup)
            except Exception as exc:
                if (attempt or not _is_retryable_asr_error(exc) or
                        not self._restart_hf_worker(generation, startup)):
                    raise

    def _json_request(self, url, data=None, *, timeout=60., startup=False):
        events = (self._request_closed, self.cancel if startup else None)
        result = _asr_json_request(url, data, timeout=timeout, cancel_events=events,
            headers={'Authorization': 'Bearer '+self._worker_token})
        if url.endswith('/health') and (result.get('session') != self._worker_session or not result.get('ready')):
            raise RuntimeError('HF worker identity/readiness mismatch')
        return result

    def warmup(self):
        with self._inference_lock:
            self._request_hf({'operation': 'warmup'}, startup=True)

    def transcribe(self, audio):
        from multiprocessing import shared_memory
        with self._inference_lock:
            context = self.hot
            if self.use_context and self.last:
                context = (context+'\n'+self.last[-self._lc_context_chars:]).strip()
            samples = np.asarray(audio, dtype='<f4')
            if samples.ndim != 1 or not len(samples):
                raise ValueError('HF audio must be a nonempty mono sample array')
            # Share exact float samples in RAM instead of expanding long audio
            # into base64/JSON or writing private capture audio to a temp file.
            region = shared_memory.SharedMemory(create=True, size=samples.nbytes)
            try:
                view = np.ndarray(samples.shape, dtype='<f4', buffer=region.buf)
                try:
                    view[:] = samples
                finally:
                    del view
                payload = {'operation': 'transcribe', 'context': context,
                           'shared_memory': region.name, 'samples': len(samples)}
                result = self._request_hf(payload)
            finally:
                region.close()
                region.unlink()
            text = result.get('text')
            if not isinstance(text, str):
                raise ValueError('HF worker returned invalid text')
            return _clean(text)

    def close(self):
        self._request_closed.set()
        self._stop_hf_worker()

# Keep the adapter aliases; run_asr itself now consumes configurable VAD options.
LlamaBackend = _LCContextBackend
QwenBackend = _LCQwenBackend

def _apply_asr_config(args, settings, is_explicit, here, status=None):
    """Apply the same persisted recognition settings in both GUI and console modes."""
    mapping = {
        "context_history": ("context_history", "--context-history", 1, 5),
        "context_chars": ("context_chars", "--context-chars", 40, 1000),
        "ctx_size": ("llama_ctx", "--ctx-size", 2048, 16384),
        "slots": ("slots", "--slots", 1, 4),
        "max_tokens": ("max_tokens", "--max-tokens", 64, 1024),
        "temperature": ("temperature", "--temperature", 0, 1),
        "partial_every": ("partial_every", "--partial-every", .2, 2),
        "silence": ("silence", "--silence", .2, 2),
        "vad_win": ("vad_win", "--vad-win", .1, 1),
        "vad_threshold": ("vad_threshold", "--vad-threshold", .1, .9),
        "vad_min_speech_ms": ("vad_min_speech_ms", "--vad-min-speech-ms", 50, 1000),
        "vad_min_silence_ms": ("vad_min_silence_ms", "--vad-min-silence-ms", 50, 2000),
        "vad_speech_pad_ms": ("vad_speech_pad_ms", "--vad-speech-pad-ms", 0, 1000),
        "min_seg": ("min_seg", "--min-seg", .2, 5),
        "max_seg": ("max_seg", "--max-seg", 0, 30),
        "max_gain": ("max_gain", "--max-gain", 1, 100),
    }
    if not isinstance(settings, dict):
        settings = {}
    # Meaningful CLI options also have persisted UI counterparts.
    extra_fields = {
        "backend": ("asr_backend", "--backend"),
        "device": ("asr_device", "--device"),
        "model": ("asr_model_path", "--model"),
        "mmproj": ("asr_mmproj_path", "--mmproj"),
        "llama_server": ("asr_llama_server_path", "--llama-server"),
        "llama_port": ("asr_llama_port", "--llama-port"),
        "llama_url": ("asr_llama_url", "--llama-url"),
        "hotwords": ("asr_hotwords", "--hotwords"),
        "hotwords_file": ("asr_hotwords_file", "--hotwords-file"),
        "debug": ("asr_debug_dir", "--debug"),
    }
    for attr, (key, flag) in extra_fields.items():
        if key not in settings or is_explicit(flag):
            continue
        value = settings[key]
        if attr == "backend":
            if str(value).lower() in ("llama", "hf"):
                args.backend = str(value).lower()
        elif attr == "device":
            try:
                index = int(value)
                args.device = index if index >= 0 else None
            except (TypeError, ValueError, OverflowError):
                pass
        elif attr == "llama_port":
            try:
                port = int(value)
                if 1 <= port <= 65535:
                    args.llama_port = port
            except (TypeError, ValueError, OverflowError):
                pass
        elif isinstance(value, str) and value.strip():
            setattr(args, attr, value.strip())
    for attr, (key, flag, low, high) in mapping.items():
        if is_explicit(flag) or key not in settings or not hasattr(args, attr):
            continue
        try:
            value = type(getattr(args, attr))(settings[key])
            if not np.isfinite(float(value)):
                continue
            setattr(args, attr, max(low, min(high, value)))
        except (ValueError, TypeError, OverflowError):
            pass
    if getattr(args, "max_seg", None) == 0:
        # No user-defined cut, but cap at 30 s internally to avoid unbounded RAM.
        print("⚠ max-seg=0: 连续语音仍执行 30s 安全切段，避免无限内存增长。", flush=True)
        args.max_seg = float("inf")
    elif 0 < getattr(args, "max_seg", 30) < getattr(args, "min_seg", 0):
        args.max_seg = args.min_seg
    if not is_explicit("--no-context") and "context_enabled" in settings:
        setting = settings["context_enabled"]
        args.context = setting.strip().lower() not in ("", "0", "false", "no", "off") if isinstance(setting, str) else bool(setting)
    if not is_explicit("--lang") and hasattr(args, "lang") and "asr_language" in settings:
        language = str(settings["asr_language"]).strip().lower()
        if language in ("auto", "ja", "zh", "en"):
            args.lang = None if language == "auto" else language
    # CLI --lang auto must mean auto-detect, just like GUI selection "auto".
    if str(getattr(args, "lang", "")).strip().lower() == "auto":
        args.lang = None
    if args.model is None and getattr(args, "backend", "llama") == "hf":
        # The restored upstream HF backend expects a Transformers model, not GGUF.
        args.model = "Qwen/Qwen3-ASR-1.7B"
    if args.model is None:
        models = {"Q8_0": "Qwen3-ASR-1.7B-Q8_0.gguf", "BF16": "Qwen3-ASR-1.7B-bf16.gguf"}
        selected = str(settings.get("asr_model", "Q8_0")).upper()
        name = models.get(selected, models["Q8_0"])
        target = os.path.join(here, "models", name)
        fallback = os.path.join(here, "models", models["Q8_0"])
        if selected == "BF16" and not os.path.isfile(target) and os.path.isfile(fallback):
            print("BF16 不存在，已回退 Q8_0。", flush=True)
            if status:
                status("⚠ BF16 不存在，已回退 Q8_0")
            target = fallback
        args.model = target
    global _LC_RUNTIME_CTX_SIZE, _LC_RUNTIME_MAX_TOKENS, _LC_RUNTIME_TEMPERATURE
    global _LC_RUNTIME_CONTEXT_HISTORY, _LC_RUNTIME_CONTEXT_CHARS
    _LC_RUNTIME_CTX_SIZE = int(getattr(args, "ctx_size", 8192))
    _LC_RUNTIME_MAX_TOKENS = int(getattr(args, "max_tokens", 200))
    _LC_RUNTIME_TEMPERATURE = float(getattr(args, "temperature", 0))
    _LC_RUNTIME_CONTEXT_HISTORY = int(getattr(args, "context_history", 1))
    _LC_RUNTIME_CONTEXT_CHARS = int(getattr(args, "context_chars", 80))
    checks = [("max_tokens", _LC_RUNTIME_MAX_TOKENS, 64, 1024),
              ("stable_count", getattr(args, "stable_count", 3), 2, 10),
              ("temperature", _LC_RUNTIME_TEMPERATURE, 0, 1),
              ("context_history", _LC_RUNTIME_CONTEXT_HISTORY, 1, 5),
              ("context_chars", _LC_RUNTIME_CONTEXT_CHARS, 40, 1000),
              ("vad_threshold", getattr(args, "vad_threshold", .45), .1, .9),
              ("vad_min_speech_ms", getattr(args, "vad_min_speech_ms", 100), 50, 1000),
              ("vad_min_silence_ms", getattr(args, "vad_min_silence_ms", 200), 50, 2000),
              ("vad_speech_pad_ms", getattr(args, "vad_speech_pad_ms", 100), 0, 1000),
              ("ctx_size", _LC_RUNTIME_CTX_SIZE, 2048, 16384),
              ("slots", getattr(args, "slots", 2), 1, 4),
              ("partial_every", getattr(args, "partial_every", .4), .2, 2.0),
              ("silence", getattr(args, "silence", .85), .2, 2.0),
              ("vad_win", getattr(args, "vad_win", .2), .1, 1.0),
              ("min_seg", getattr(args, "min_seg", 1.), .2, 5.0),
              ("max_seg", getattr(args, "max_seg", 6.), 0, 30.0),
              ("max_gain", getattr(args, "max_gain", 60.), 1., 100.)]
    for name, value, low, high in checks:
        if name == "max_seg" and value == float("inf"):
            continue
        if not np.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid ASR {name}: {value!r} (range {low}..{high})")
    if getattr(args, "backend", "llama") == "hf" and not is_explicit("--temperature"):
        args.temperature = 0.0
        _LC_RUNTIME_TEMPERATURE = 0.0
    if getattr(args, "backend", "llama") == "hf" and _LC_RUNTIME_TEMPERATURE:
        raise ValueError("HF backend cannot accept temperature other than 0")
    for name, default in (("punct_silence", .40),
                          ("hesitation_silence", 1.10)):
        if getattr(args, name, default) != default:
            print(f"ASR warning: {name} is unsupported by upstream; ignored.", flush=True)
    print(f"ASR configured: model={getattr(args, 'model', None)} lang={getattr(args, 'lang', None)} "
          f"max_tokens={_LC_RUNTIME_MAX_TOKENS} temperature={_LC_RUNTIME_TEMPERATURE} "
          f"VAD={getattr(args, 'vad_threshold', .45)}/{getattr(args, 'vad_min_speech_ms', 100)}/"
          f"{getattr(args, 'vad_min_silence_ms', 200)}/{getattr(args, 'vad_speech_pad_ms', 100)} "
          f"ctx={_LC_RUNTIME_CTX_SIZE} slots={getattr(args, 'slots', 2)}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="llama", choices=["llama", "hf"], help="llama=llama.cpp GGUF / hf=transformers")
    ap.add_argument("--list", action="store_true", help="列出输出设备后退出")
    ap.add_argument("--device", type=int, default=None, help="输出设备 index（默认=系统默认输出）")
    ap.add_argument("--model", default=None, help="GGUF 模型路径（默认 models/Qwen3-ASR-1.7B-Q8_0.gguf）")
    ap.add_argument("--mmproj", default=None, help="mmproj GGUF 路径（默认 models/mmproj-Qwen3-ASR-1.7B-bf16.gguf）")
    ap.add_argument("--llama-server", default=None, help="llama-server.exe 路径（默认按 LLAMA_SERVER 环境变量 / 脚本旁 llama/ 目录 / PATH 查找）")
    ap.add_argument("--llama-port", type=int, default=8765)
    ap.add_argument("--llama-url", default=None, help="连接已经在跑的 llama-server，而不是自己起一个")
    ap.add_argument("--debug", default=None, help="目录：留存每段音频 wav + log.jsonl（时延/文本）供分析")
    ap.add_argument("--lang", default=None, help="语言代码 zh/en/ja…，不填=自动检测")
    ap.add_argument("--hotwords", default=None, help="逗号分隔的专有名词，如 'BLG,T1,峡谷先锋'")
    ap.add_argument("--hotwords-file", default=None, help="热词文件（默认读脚本旁边的 hotwords.txt，每行/逗号分隔，# 注释）")
    ap.add_argument("--no-context", dest="context", action="store_false", help="不把上一句喂给模型当上下文")
    ap.add_argument("--context-history", type=int, default=1, help="作为上下文保留的最终字幕句数")
    ap.add_argument("--context-chars", type=int, default=80, help="上下文最多保留多少字符")
    ap.add_argument("--ctx-size", type=int, default=8192, help="llama-server 总 context size")
    ap.add_argument("--max-tokens", type=int, default=200, help="单次 ASR 最大输出 token 数")
    ap.add_argument("--temperature", type=float, default=0.0, help="ASR 解码温度")
    ap.add_argument("--partial-every", type=float, default=0.4, help="说话中多少秒刷新一次临时结果")
    ap.add_argument("--stable-count", type=int, default=3, help="Partial 前缀连续确认次数；重复冲突后允许纠正")
    ap.add_argument("--punct-silence", type=float, default=0.40, help="旧选项兼容：原版不支持标点动态静音，非默认值警告并忽略")
    ap.add_argument("--hesitation-silence", type=float, default=1.10, help="旧选项兼容：原版不支持犹豫词动态静音，非默认值警告并忽略")
    ap.add_argument("--silence", type=float, default=0.85, help="静音多少秒算一句结束")
    ap.add_argument("--vad-win", type=float, default=0.2, help="VAD 判定窗口秒数（越小句尾反应越快）")
    ap.add_argument("--vad-threshold", type=float, default=0.45, help="Silero VAD 语音阈值")
    ap.add_argument("--vad-min-speech-ms", type=int, default=100, help="VAD 最短语音毫秒")
    ap.add_argument("--vad-min-silence-ms", type=int, default=200, help="VAD 最短静音毫秒")
    ap.add_argument("--vad-speech-pad-ms", type=int, default=100, help="VAD 语音前后 padding 毫秒")
    ap.add_argument("--slots", type=int, default=2, help="llama-server 并行槽位数")
    ap.add_argument("--min-seg", type=float, default=1.0, help="短于这个秒数的句子先不切，等下一段一起")
    ap.add_argument("--max-seg", type=float, default=6.0, help="最长强制切段秒数；设为 0 时仍有 30 秒安全上限")
    ap.add_argument("--max-gain", type=float, default=60.0, help="自动增益上限倍数")
    ap.add_argument("--out", default=None, help="追加写入 txt")
    ap.add_argument("--srt", default=None, help="追加写入 srt")
    ap.add_argument("--no-partial", action="store_true", help="只输出最终结果")
    ap.add_argument("--console", action="store_true", help="不开悬浮字幕窗，只在终端显示")
    ap.add_argument("--font-size", type=int, default=30, help="字幕字号")
    ap.add_argument("--font", default="Microsoft YaHei")
    ap.add_argument("--width", type=float, default=0.8, help="字幕条宽度占屏幕比例")
    ap.add_argument("--bottom", type=int, default=70, help="字幕条距屏幕底部像素")
    ap.add_argument("--alpha", type=float, default=0.82, help="字幕条不透明度 0~1")
    ap.add_argument("--hold", type=float, default=6.0, help="没新话时字幕保留几秒后清空")
    ap.add_argument("--duration", type=float, default=None, help="运行多少秒后自动退出（测试用）")
    args = ap.parse_args()
    try:
        _validate_vad_window(args.vad_win)
    except ValueError as exc:
        ap.error(str(exc))

    if args.list:
        pa = pyaudio.PyAudio(); list_devices(pa); pa.terminate(); return

    # Output sinks must read saved paths BEFORE ConsoleSink construction.
    initial_settings_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "live-caption-settings.json")
    try:
        with open(initial_settings_path, "r", encoding="utf-8") as f:
            initial_saved = json.load(f)
        if not isinstance(initial_saved, dict):
            initial_saved = {}
    except (OSError, ValueError, TypeError):
        initial_saved = {}
    for name, key, flag in (("out", "asr_output_txt", "--out"),
                            ("srt", "asr_output_srt", "--srt")):
        if key in initial_saved and not any(
                a == flag or a.startswith(flag + "=") for a in _LC_ORIGINAL_ARGS):
            value = initial_saved[key]
            if isinstance(value, str) and value.strip():
                setattr(args, name, value.strip())
    if not any(a == "--no-partial" for a in _LC_ORIGINAL_ARGS):
        args.no_partial = bool(initial_saved.get("asr_no_partial", args.no_partial))

    stop = threading.Event()
    console = overlay = translator = th = None
    worker_failure = [None]
    try:
        console = ConsoleSink(args.out, args.srt, show_partial=args.console and not args.no_partial)

        def cli_explicit(option):
            return any(a == option or a.startswith(option + "=") for a in _LC_ORIGINAL_ARGS)

        here = os.path.dirname(os.path.abspath(__file__))
        if args.console:
            settings_path = os.path.join(here, "live-caption-settings.json")
            _apply_asr_config(args, initial_saved, cli_explicit, here)
            try:
                run_asr(args, console, stop)
            except KeyboardInterrupt:
                stop.set()
            print("\n已退出。")
            return

        overlay = OverlaySink(args.font_size, args.width, args.bottom, args.alpha, 2, args.hold, args.font)
        _apply_asr_config(args, overlay._current_settings(), cli_explicit, here, status=overlay.status)
        print(f"ASR 主模型: {os.path.basename(args.model)}", flush=True)
        print(
            f"识别参数: ctx={args.ctx_size} slots={args.slots} context={args.context_history}句/{args.context_chars}字 "
            f"VAD={args.vad_threshold:.2f} partial={args.partial_every:.2f}s "
            f"silence={args.silence:.2f}s "
            f"seg={args.min_seg:.1f}-{args.max_seg:.1f}s",
            flush=True,
        )
        translator = GoogleTranslateSink(overlay, target="zh-CN")
        if not translator.settings.log_only:
            print(f"翻译隐私提示：当前引擎 {translator.settings.engine}，请确认其服务地址；字幕原文可能发送至第三方。", flush=True)
        sink = MultiSink(console, overlay, translator)

        def worker():
            error = None
            try:
                run_asr(args, sink, stop, status=overlay.status)
            except Exception as exc:
                error = exc
                worker_failure[0] = exc
                print("识别线程出错:", repr(exc), flush=True)
            finally:
                _lc_worker_finished(overlay, error)

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        try:
            overlay.run()
        except KeyboardInterrupt:
            pass
        stop.set()
        # ASR owns its Final workers. Keep output sinks alive until it has drained
        # them and returned; otherwise a delayed result can target a closed sink.
        th.join()
        if worker_failure[0] is not None:
            raise RuntimeError("ASR worker terminated abnormally") from worker_failure[0]
        print("\n已退出。")
    finally:
        stop.set()
        try:
            if th is not None and th.is_alive():
                th.join()
        finally:
            try:
                if translator is not None:
                    translator.close()
            finally:
                try:
                    if console is not None:
                        console.close()
                finally:
                    if overlay is not None and not overlay.closed:
                        overlay.close()


def _lc_worker_finished(overlay, error):
    """Keep fatal ASR failures visible instead of silently closing the GUI."""
    if error is None:
        overlay.request_close()
    else:
        details = f"{type(error).__name__}: {str(error)[:250]}"
        overlay.status("识别出错；请检查 live-caption-pyw.log，点击 × 退出。" + details)


def _lc_report_fatal_error(message, directory=None):
    """Log and display .pyw startup errors without reading a missing stdin."""
    from datetime import datetime
    target = directory or os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(target, "live_caption.log"), "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{message}\n")
    except OSError:
        pass
    print(message, flush=True)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,
                "实时字幕启动失败。详情请查看 live_caption.log。\n\n" + str(message)[-1800:],
                "live-caption", 0x10)
        except Exception:
            pass


 
# ===== BYTE-EXACT TERRYKIDDY ASR RUNTIME (upstream unmodified) =====
# Complete upstream live_caption.py is stored here losslessly; all ASR
# calls are routed into the original module. Existing UI/settings and Google
# / Google2 translation code is not patched or modified.
import base64 as _terry_base64, hashlib as _terry_hashlib
import zlib as _terry_zlib, types as _terry_types
_TERRY_PIN = '794fea1fd50f34e55a6e1e1537aa1659af61fea1'
_TERRY_SOURCE_HASH = '37e925c5c9aef56ad4d171c1f2af3d1a100166fac0c2f26b653b78cb0958812b'
_TERRY_RAW = _terry_zlib.decompress(_terry_base64.b85decode('c-qZ<Yj+bzw&-{Niawo{l~$12l1WJ7M9ekdB+Qt{U=r?J<JGGrwX6<Ox6<9hc04}Ic>v~NOePS*D?kDXlLSnLBoG_Jf0=Ph@~8ZTyLVM}clAp)Idj%Io(Ynwt9DiG+O^+R{oH@_w`{Awq#OO3aa3qdJ2lhjdrru`m=nroMK>k}g_cvzy^1A$qA2#&^`n|nR-2A)8gg@5_~+bt!M*oo`{FO{Q>Xqlccydx=k6z;w(oxJUcJ-)+uip0C!NcmxL@C2zy9D~a|_+oAKLTh+`AXuUoZUjc)or02lwiI;jR7e{Py^adu*Zm)33ihKH1kN2<s<KtY0}kC>$CZ9ooNJsGDZ9q?V5eQgd2uRdjQ*sXFCa2AZusT3Ek2*Zt;0;oTj%oocDB3H?In`_pTypFy|nhYz7=0N7nR?#_QdDExU+Gq&c2Mi1l)@+&)F434E5cGa{Rnq_B&Qnj!RhJeF?N&S3!?a|%#=O?;Pue!Ha*Ot!;lQrFLYF4hUS`%9C=+@sJUm4Zwnq>;_4($@$`4jHqZRoB2#goph<w3#yaS>+fE?-#x_)hzyAKE|PqjPUxy9r&pi%ad(pS!2-{A=z*_};z$F+N><$Odrk3}AKb*X!+*pSkn*J1g^@@0Vb1FrKxQU;6qw=kK&1To{Dj=&}jXrOCl4jS1m{nh^baPN4Jh7sAI1qJI+rbGe%7Oqy237Q`JRZ)Fb_vhIVM-TM#SWAp3hPiH&J57t)ZI_DpD=6@DRhwiBxYmYu_Up?ubTj(w=K%X!(n6!KR5>SfZfs>snXQMIbwj(6`K_BxM-4n|I6i@?H*M0IC{OsO);@&#Z3t&Y%s+Tq4l{momnG^24%RC%OH!9jxrWd%fY1k%EVG`08fKDkLbiTWYYrOa!%$_bSrI}uk&6Xn=rlS!o!Ef90e#5sux|jfWP*{I_qkZE9rLE4lEAa2y>OyDr9#Ct0<#KoR9!F|d+{Fj$=kK-WKNp7Wvamik2b2BR+((Swb~;vl%g&&fFaf>MG@)<U15MSkHSf1+d*3X-%%1iO9lhZfJ2gvFk-vBa@3*v;)>pO6hEP!*4eJW*k@q`GAXK!vqxSV7lk)$Bo$u@#5_XM@2;GNE?vqcDx>k-s1>_l;Q4wlYfiel0Z&Pmy-LH=Ufe6M?y`rnRa;sEVZTKh*!2a?JD~3VJfF8NN`YKvgP}E9Ap%s-%y>3ex+=qftQf*Br0wQGKPc`{HQ#T~QfZVjSsy>w!#1XwD!iTLpTCNF$QztYV3n(lxL!jtYTvi{7ZdY_mLeQ)i{IfKtWf|lrFfYMwl?2_uCb&Hk>X)L!!sl6R2fYLTXI-=IBVBWF@DHAXw9qZn7!&so9ei6H2jC@Pi`UH7n)C;&B~NIML~BB4Tu-dGnrf?#V@W13U=cA3C{>_it?ZcAv>54~jUZMJh6!X5Uzt8`Ahzbf@~N#ljOXn)mABv7q3qf{I<#YCSQ-7xo{`;q|1_!`8al9d_ujW~s0A3SN1#Tl0h>^@1*7Q|g=7&vVg-GDqXz&Zg>CtKp0JTSH{CB!BO5t(8w6YX#>39p6NH3|J^lm`T<c$bN+n^>nuH{A-4)8pY^kX-q%|$wfVl!RCP{f_CP=<qrA#ve-LU0xtmy#lYEy^#G*w(krDj4$P@?EY)l^E;iZk8RJh=?(PdlPew5hVzbcB7Q!<J=Q0YEma_Q@x}6G6DK(TSPYdBSE(6?9Ff=XXqau>?aI6Is=LEvMOXc9Cl0cxFsA)CPR<w@C#z2?OTMJp^?^Gc-#Da*{ZzYpB){7z7BiH*$gtdd10w^_4HW_il+uz1}DRa>eUV(CWYlfK!GiTm=wgGnKIy$jDz9pB)$Q7q)>Pf*{RcEhu3yN7Y8Nt{njW_riE)R(Sh^EUDj6r*<1K&z&{ZFrX)F3(C+8j&qi+AHy&#Y9Qg>Al+_fVhva!AfJAB=Pz|1USGew*m<zhS-DPU$CC<he%uR$(h`sd1eX;E^x|IA@c7Q}CL0uVzjx7bNk|<*w*?#wT?=1sQ~uU+C{xny5jGRnKE`6Pu1`Pp((fW5+M`b#0HR*<eubGE>$6@WrIxD0&=0f>6ntb00SN`0W(I-!)Pn-e-_LZ_K*OK}Ky;?g)zk2QvW#VrceZdqvk8H*jY$L>%tCh*MY6Se6~u!DzsU!5RUAFQ%?T^W-gEYyu3M;4q`o<2K@D6M9yz3i@E=9iCXA90C-NIH?E>J%p7A~5hwROB)B&ZVA%(`56$)9Qg0xr^t94U#whjR2f4`;Hb!WOLzWwKjxMCb_H|amZ)%?v4$nE1$T2?_$c_?j|h8ZS}0F%Ied0Zxv2DXp4#@PbBquPMTO>F7clr4r3rCe(nM<g;NzXgm!K;BW;4EjWPF|@$_+<-ID&0;iVXscOG3BJ{J!v-Zslk!=?hn5up_n?zCK?!k+Z>q34v8*YpV2Dj<LAM!(D_OM(tAHwOfjmZJJF4Xn6}e?m76dynD-e#Nybmkvm`#Rikk52hP-9GtGEfi|q?T>*wPH-zt7h3y&EGsgPnlq~%Ox-o6i``VG*t_>rcyRx|5DwRum~KN>a^?#5x`hM-BLh9kur(PC*i_og@9x3G_ik23l?Dt#)W7CExOdIR$*-ZSSX3}IOIA9>}(eb;a(Be6jDk}1?1;omw<?o8=7iJ>XdF53jqX%a2YTvS++Eluwt1Qr6f!T*qqVQ7&2|LS*aK>n?Mw3qpMCaXz(V5jRT|0SS;7Fz%w1KXj>?bU_8ZxR!htFCE1I#LcqLHc#(A{U&V2@ezpSkzCzJiMA`r%c~QvAg-n)>k?e2ey2WxS!;M|0^htk6jC9ymBxM_`fK&wk_V{wd`rlX`tTr6Qv4Q8588tZ-z@=|M;RDmC*hSb1QeMs@4==MtLL_R4`HFtDVOAukBD30AVJqmgv{nO}1a+W6R@jygO|mM^)CThdmD$0W2K%(hXLbfaHuSP(GPpv2zfb^Wc`(1VGAsJ>X(zR$M|I;!s)PcLq@c}Fq~rvKaOh{voP^(&qpNj73Q++@B^IGC4-(oSBC;sz04s{b0VZac6eo44P7xAgj4Aryypi+*=haCC#|<Ba8TT_>(NJj5`Eaq5XKOCwKT;<AQ~~Z`kVZao8E}SMN1JlOTngn&Y-CB?$}a*Lpi$lVpL5^9#})!L7zh~0V6UP+Hfe!TEQyCLtVEh^f%+nBHbSFWe$Y80Pp}ftSvX+Tddse*P1tW6OuVWap=rav3faE+1*8>EHz`A=W92%6B6yODbiddEIf6ThxjAIRoRl)qg;gc1CO-f6;PZP1pC9EwdFI5}3~7ya1de(Z2#w5~(t1oyi0KqwDVs?g;9&!8GQV0Bt%3HE1ej9g7O;fl&)|vQ=}b@?I-Fswi!A`GHKS#)fO5I*Lhu|tJKR%f%Eo-yh-htom)-;dd0hu4L>8V~VpR3fiV!4k=Dv>WM1(V7pc>Zzt1^X~c?K3A@elWA@svUCJE~cAbvj^>qA2n%XLN#odwl(?Pu<6hoa6a&`|4c#^ZRR$=DIh}psW7TBkFu4?(|c%yz}_)?)^n~@r%xf_t9_okDJh>yL1gb{TF^(Tm7v4=-m3{bLbMpp53R%)^D!3SMRJnTIno5?c6!-&R=m?mixO;&jB=mKt1sE+4|h&?$Z<0=_j;LU09#{8wj@c)gRE#1hYAf+gCwHP;C7Jt+*8!!lYhtY6?i{axpKzniWc><Cu-&EAU9IH)|@Ef_i{k-7dnHnpv+Dx1q2@I7M*}5kY3v5&ojSt?9h&=ea455N#HHJI;}mhcW`AHSk+ss@=kxl~FgcLr-XtJPDddy$-y!Y+9N;syX{Dv#i<nuBJXTsajxJ+P1`LZp;=LCLg+Kgu5eFNLU=87lWGzDSRKw$U4Y>#($$c6LAJL@FV<yU>Kf|&+sF%N@$kC;}A-Mr?`gfDfu3$g4`Iji?Q*LJO1ii_vxo=k8Zh(x8XbLL@S@Qzq}f5%%qZnyZ|MS7=8>J$5C2M<hH$pW0iQPp$4%~!q3RwuK3Ra&DJy>f?@gKktjU&3_n8kWfS(WrB^gdqn=DD%(_V(^p5JKmZRB{m;*zhVcHI``i*Lmr4iX?h*Si<tEx|+-Bbdd<heZg<Av2EYENv$NSRervCEdG8D!T<p#8?4)HHpf=0s;c$!scn62xP$Wh1rd-bFt$`4ZKGi1yPLSt6y}vIxGBjN~^>6p3m>uTK}T0LG7u!Y47{7i4@zu>`~%mCIG7X=z6{godg}A)h4^3J4kPfo-p%#p-oops`Yqc2W;Ikw?^$R+kWmRO2X$D7<kkUQ_XZ@QYuWtngmTcJ%6WQ7mi7c5q#da?P}gqAjLaIGjL2hR015Mrx_3icEm6%$6x9yaY3As+FnYKt48d57#{gUL}9eFmPkTf%D^r5j^{_ak4T-S|l4bSy6=DgTvM9^`bZ>l6KQY34}*JlQiXxn}S>^#M;(?KGLv7!*WF$mlFgUkSo$~9LP};jq4w_%S9S<^N{-r`HLZ!AV{JV`dZR3<YpaRFqT%IDi%=o!MEumG5P#u(8MSkslq}@FGCmEC?o}xqY2H4s<i-Bod!ios_K^QF#R*6P)$}Qh)P9q!7Wdg!*@ixf;Msqq@pCgv7-fi!Psws49pd_i=H`9vD68wC}vVy7IJ$`l#h{C4Pd>Q-reQ4Vw<|!C{<KJ8|2u(lu2#6$86c!p_-=EgAOP0X9UTtIxMx?)Hc%(Y1On$$1Iz5Nj$Vi*)=>ed~jGfw0rNaeTTfxNV~)Z-4<9;X1&hVhXzsw_tD4hk8|XEpndtP%G58iw$x`AEYx$t`uAVB%ipxmoa|h^?%w+nXc_cF(E6afbMGQ3oW52m6a@Tt3k##5{&{uIy>(d_5QvsI-#zgiY>W27SLEkOzY`(>0{$g8G!S%Uka9%3LoXwk)LS^gkX++M=xB?uHP2fxWL9zKm*mUsB=P4N9+Me#UJQg<PkUnpxs?-YUe$<X3#kV3gl6J^r+ss1a&~H_HoIkJ3jUj(<u)k#3c{m(PFQ<-t$pT0Q2M)1e#PLxsXO5TDZY4(PHI~6(Z-d=B>5%>8-+6^WpMaj_kfJqs7%C#t#Jx}u%Q{z#e6nOWy;^jh-yuqQiyt$6B7Cd0P=L&^J(4`47fsC&(X<mnThrdW1Gd&=}0MA!JsF?4$^wz6=b4sfQV`6hV-hpdxHvF1Ze<CSd)oYPuMQxsV^xPa=CK5BGCJYJ`;wQs1rq-@G4MbIG!GqDjdQrhqnm3_dlkgDvtg1^hQzRh4`E$5d|}Oz8RMe^9>UJ0Uxf6L9Hf0rM~-349QMtY-wO=f%%c>bO^P6pJqyg@#UM=5iX6)JPOV8%urjUr;85V=M$Vg<BPPKUe}0`v59~j*uoV$VUNkYPlBX|IjQPVPQnaVYIY7cnoa`1lBKCfqOKO~1%qUOpTXp~9}}9dM<*GcC{8LBb~6oj#Ig)IGAU=~!PseFsCAo7ggG{zO@bTc_-x2nb1+eZ4x7;u|8t8lhV8~PW4VFB@lDN?v!S2CjwXR6g7F03pn?G}1*~i>RmvpVvVrd*p&2k511xEY7nU%J(DX4`DmujssKS-Nxy@{UEaT?oF>`+6+XE*_@*FV$6qzJ9!E~jJX<uYEDLb2=K)7>aC^1VjRy7+!$5^f~?n7i4V!125G!AMEZVpb^DaDP-YdJt#qEntL&R|3`27)*00<lEwDlAL1;3D<V$Ov&&>c<2nmAJY(-~NrdI?*6IcUCCK1GeBEKh{2Vs(s<7_O+Yshd040i*R6Wp_90}?@S8u9lGe)2-<5+DbKHcP)tRQo)U;*pgpSI1iQ!R!$3#@)&bW>0{w2psFK)1d<$-3Q-d{{p&mYhVpxv$SxkX$U%Sm#;`jm}y>s?{!XT3!bqN#SG-m-lMk>H|*D)J3(|hMqn{6<*m^$#;0x$bHhL^IT=}ZQnX^PLlEdwp2Wh7E50n<=*1G9)WjFIp}m_&79PiaixCMprZFe;!XS>|-I-jL$(4pMJecw|qj?&v+E$1mFO)Q)8}VIguU{M=-4lqBJze<Oep!H2hl2pj6Dr>a?@^lYFh46gMi;!Z^Q1{Q~T1wCU;e?Bz4R~Z@F`xc;NM*N^A4hrJV8XDCaYN?um1;fTfUAJpk@SchVe^i^QSLKL_C4VwOr8DsNbW0;ZRKqc8o$x}6RSTcL3B06CKvqcow=}Ck0cbY<9)%uQpH>SC4zz4rr!|@m7VI}Ir!~<^>Ov<Kb~|dFwNOc)LzY_7%lO+N6Fpc{b^LWy$6xR2nuCE2IxW3IKo}LA1VWkhNe>TH?4Zf#eq_!+WFbiWp%a0wLFQj`AAzVM?A2ZTsD0r+o{qTrW#{9&-KB+o>OpZAZ?gy);p8;L5&%vQoHa}^5J^l1S(Y)8^-o-6g_ey;N=7u=&w^a0g;_QHfVl`)P$<{Ui3#9Rm}0FQ)vS_f>&~>Ik)*|77n214_hGz>YFl0(Wc)DvJvhklRwUzwIfkl`1SeDiU;sdd6^Wl6O>`o|mP;i4sIZMBhGCXZLv0qtatj!Ceq*0`(jOSVn8c+aWok+Z6TfLZ@}(?!W@wX&W5TlA#etW%MfDMtaB+V>krY(A8u%|s2_bb-(m`q@gyf&R0k#lTjku+Rn%u8=42-JvKD|t;R-@HSlnVsAGFj|0h!{;G30Or;@A^QqOj{Z~kdR-N6-Eza=)_v;1oJeIX$GBgFe#Z5$Xx$rXUfwSYh2*WlbHN;QCs&X7tvM;6bU~tC&Lguj~7-Q%e|T(94Gk}dXuA6U%^eAKKWp?WP>O|7s*qOB)=);IwsOtWBGBJ8Xzzw%U5?jE;0hzg&Jiebu5(2&89%U<!sG@{U%7ILjJXv3R{Hs`K9)m4}*hlBr&+*G%D;s0Unxr`{2R-LVuYi5P~2DgfviC!dyYc!;C>MuHXIk>&}f2+e_z#x8D@PZ0qUw?(J3g#0__DA%Q9R!%SyNbmWRQr7;EtAC0DEzSomSfoTIwqxkA8+g^rptL~+G)lCOBU7Um2tx}W5(Mk2XUXqy~1{#@wTljp>HY0i>t%;i`)Z~Bth7OzYFz;x(HF@x!SxQL5hN*f<^HB#g2q<^)YY;;2!+&&E7TZ@(f;3ut^j-Jir=4qa?zuDcCq3zc1V`5v4`Mw!)&Aio*i`PZ2kx)`a8Jzr_V}cG|HsaipW0_{ySElP=kH*4$XDN^FYxjwYmcsWZ$EVxZXn$IKeiXXqJ2jcLEUz!PRvYa%Pe6kQO$9hgZ=%5fmdWOD&)f84AKDRMG*pv9X$#Fp~nUj@>8@@#7$91?;x&;vJ@>kLqMB*^$yL2A&L6@*7aayHs|#TjoEZ+nk6PXfhKjQ#z<VIA5sPrRqP{!sj|;LLgP}B=uc53!O_>rwnKy;K{7{pxYec1SYiC%gtBr2O@%R@Er_{>=rac~m!tea3>bx&1H}`SvDaP`NezT8lGI!Yzh+X!yqF_u-m8Vz253ve%uQ;g7E$+dMw0;{d?iCFfQpl==r*1)7SkMObjJleN8V4eL1AhL4zmV}+{0bN@9uqPWQ05dRVnGwvuP=1P_dDrSL^D8U4(Ta2kUqiDDFHkJalka*}E?Y8|EXy%)pvQT2O#nNdD}4xrMl8hkv;MVdw5ip}(f7b*C0+k7>!N)bAYjyg}tRgaSzFjg3f3^#KpZ51NtdjSiFmHW-oS<>RUhkgZ`Q?+ldAGIjmqt{ceb(|1tZalNtG>ihJNe7JKQfmVZ0Wa8-0q#yL~wT$?=Au+^p+;dCrsXOkiAG<%@1|Cf_4M5Hd&)dMAMd5j_Q>wC*VGG8Ige;O>*U)<<zub#7HXIU2i0;o1+t+S~0%er1Q*{BB&ST>NY*0J8PtUtoz7$?C03R<%R9bZYzPx_%9~q%vcmcWk3lg5DC&EeaPyp=s;iseq;h|nj6hsR*)>e<>;kYlB+Vh{Tt^5EiJ0o(^m8lk}Hh4-z-?MUE;G<wGG4ccSZH);hr~%U6(8>-l!w5%4;Q|PeExTFQ9Z4L7vI6qrOeTXTsS*g3>7F9uNlZ^uOx-`;)`2x?+MC%qq#|r`L=)JDYQ>iD?2%fLGIB*jii&1Ya7T~vd@>O*1PVQ^N9{a(%)(>18DkBiBoduJ5%a$%iP8f=WiF-C|ASmXFE=!&W>x~a_4y`YnIUp*pE~8yVEg=&?$aw&brSvvI@!L`d$8IhL-z-G(z$aQ9@5>3_eGpJ);|4%`_tds%OCdxowl#d(W@dDxbDO2pfte)dWsX1Bd=ZORP$%dN+q^BE%$0o0@=mt9oaeniyY2jJ)&8LR^J-Vi=al$?tRM6eedi&IDBAKVvR-}t5wEBTnVeDRCTRhu@%$|B-m}NNm*&tO%VMis@dovGe~lgcmT+2UlWgD!dmXcqgO~OhcMGTUc3(%GFtq5+ycB>fg?Cb3?<D94<J@b`VW_8#uHGsV`y~u&SyrJxL>na2Jt&UAqQc*j_3^?d-jp&<1j0b`ap!6sM`}ol1N&<i5iHQ^+`tFb!gv#U8J8qU<Ec>jYFn&1k~73%^B4{(D<IvqoOz)_OnNw+8i9HJ5+Y`qZ&hpH?`>4Ht27tT1ESHI;D&KmTtl_Pw&JvOP={?xu#WGbr@|*rKHs#{=Wx@_wE|r_3Q+&L$&oXne1+Z9-jj;hfWDSkpb;C@d*@hyOK?YU}}%nFs<nrDQt3qfSNV{K&VbV66y^Iu<|=PLpXkiDKNt5rB*PoFL@`b%^I|(4EPW`oJf*G!rT(D1#SE#i9eMSkuC>&0uTR84X~>r{<v?4vTw&9hj$)SMt1MneNg$+?vW8?-(CeD>>SxQIvi!ScrJhg?2lQ3*<^*+{5hAVEv+iyh%$Z|V!>pE8PdJidU(pgxLqYW?pW_bwyjSXY`qSek?wtJetT%|u90CP{bZ#ET5Mg5h<e_)C8MZh)lgr6b>Xtw9$z3Xf-_VgQfb(}_zNBaxc-TI?7{lQyFzZeCsV0hOcn2_szg*%c2z<ke}&G<r+6uXryhG6<Kj~H<5f@#*B)QSs}k;<c9$1D;d~MmFfF3GHkwY<qxwJS(ZoY$lK947^#+Ys|8=`PZ97_nHfONNV_1^EZinJX$b@eS1GNqtZ~T>>{bhx3C`v=an^%<W-V*|9vp|8{>G0KX4oB{z@x5)4mBYhjRnc>}3NwDa**8?%BC+c2VpM-?_SD=a)uT#j+e=c^70omtbooLRU=FBnZhI+o!Hy2Qf7;RP-TQcR7WHXtLXR=yeLIsSk?72m5zQM70>eAaEEO`Z(;_@*PSZ{YydrWm8IL+kqX#01$n;npJ-aNSlfGOBGGN|nJjY$YG}x^JuWWmTKU)ubps+0?J7x)IAjJ=C)A`DP8*-LalS-(RA64skXedzVHx*Lgyy?B82+tBM;Eb~W4co0@6J9?yBU)yi#4qV~L&cBuX<!5rV9v%RThIpgS-1rNL@eP$dV-zc;hFW;P~$=95xxGRm&g#_1<*g02-qn+5@%<}X9HPUGD#jTG4m>|M$?vNL?Aw!Ty8Z;5CIS{JVrxfP&R-k3q-&MQNBT!fiiL?K`g7FBQfR)g_t-nn~@pG@kIH)x3J7iJwQG5VK>jwLO<*$hYNL}(uGqk;;6Gi4YYO5vWqj~PKx2&L55tl36^!4NWp%bnm8NT(@8cm3@b2<x8;*8V|wI#b2c&a`;*IK$~6=3b`r<O^5bJ3e&Lr-8snV9Ex2;(#In2kmHW*{Lck_j@IGLX?Ym#MKR+Sz+e73SC|XwWEHfJC;Bp~l=8E82BD3jBa&(cX2)E<l7rM0YW}Fz8=S^=hd^O+N^6~D{-R_+^Jh{4hy!-T0``*v){4Km>l0*}{r}OTu%kILd_7_X**B`9?damES^qu?leQG|hJ^HGB_GC(oE|RU0mJ5^Uz8l<R$_r7G9L;Lz2D0@3Qqs#Vt<;(rF(>?ZAzuFkMZ&J#2hg#ww1{yUa(WVRb@vODO|Vzk`3$7XI=TceJaw19=|??l<(GbXIG17z@14AM8*dO?{Ah#Yx|I#MGmS+-3EL3i2N(<p4xO5|+)$6;AxT@RWN8pYF^@z}y3>3q|4Ik`!aHar*3AjhUtP?k-OI4S4GMvDBZt@NJT0xv#8EV#-1D1&_~)sG)J><9szCxLwl*;XpvPW-FE5M_=3lDJ%E*1=?z%v|2wy!&(3k~ynRKn=-==x|_h=t<N0!Hk+`)?ms}VA(%E3~|oD3q10@4PAi=x_PU1tZEm<ac-HK+!60`#$TtcpTB(+bu5rJClO+FnSx)^c<j-zckie+h{B=yiu9BSU+Jl+ob>?+zb8Y6~q4g3PiQx#)dio^V;4(!{8>o7y_uh>c91tm)-i*un03me5yLMn+3U9^zqT!jXf!AFFy@lgi1r5tY*}DC6!Yiw+RjPj~4d`agpl1`LKC0+gGRHoe8O{pBa_%5wMHuidk!0Z8`~^o6!Be&|O{`-RSxd+zE5U`1kTY_C24dH3Fv&g#9+*(LWYU{znNpI8+5HNo`K`On$)XrVv^ku3ggcPOJ3FGMe?)`XWXNMrVx2}>{B!+TtccqghYd({=hAcplr%jf+7R!02Q-fy*J<ENyMW{QI6iADZm+~9i9$Qt4INWvhwKm~6EMW#USp^34&h&-n^wcv**$)6K5n4uEYY}p{NW&$fAXaT!yY|?8;kALE5EDy9Lj%8s54%_2fM0!I5NtW=NjC(T;;WOy1XD^{P-dwU`QV*V9krkPc(8~@<FFT@5+Y$kg*?cnPlkb4sHolvQ*DToOOjL_P^t4KV97)a`F3h}?#l!_mJX~OBiM+9A0!2?m4Tt-`D7d#4x=SnWuPfbePo@B$8OUy%%}9S3JY2}kY>oE9LGN5gm=t0ntivT~Y$*5t)Z7R8+-u_*31A9*!IsS7QgE`h0mgtlVVNyVb_cDO9!(Yn8qM*0hgt0qMufLSbnD>#(qi&F{_sX|4Ahp~`qQW58A19(k?;Ah<S|!3D)`k&mt2o)kM4F)|G4(+>Gg|0xo1~8e_w9jc+8UsNxGpd!D^B8MZyfSBukjyHRs*z#w!6Nh*AYXr&$WSJX}7iRy;|9N2H)kE7ufcq2%%%fcY*4F^IL2B~_q>((tn*y^xJruWMyetB<iDye^I1kG6k$2tV1jt&n3EX*YM04MTtPw})0m?;7?QycjKZPbrr5xT(~y6Th!CLoO_3{ETKnCGc5~2SDjy?CK+$7Yjp!r(Ws|3P$dp<Bb_CrK^NI1dsE07f2Vqj5aZr0Z;1j3PYGD#vY>zldNL|t^=0!O4`yNQ6a)~0P>LBARWHgKKJKWmApJL(W-J9EzOzn;nH6l0;ctH5%_pfeiXi+@^tD(cXgG8+h<bZv$KNaYXKR&9IVIPRb*F1qUZD+X1K2?!~jYO>4JATOR;r{<si};ULqp<?I$5}?Qu7T3P`2``AxHj`r<he$xBRDu|JGzaRdC4#<Ss^^52ip6zB=q<Wk%vhPhrCqpd^CjP13OCNo!InEB8lV5;ECcfw30A81f6RA+6lKi$W3?zg8qS3Y&m;q6g}jf80@OxdHKe_mV^Xk(B7Y>Jf<CKh^zw8Xe0{L1)zftu7oT4(r}?k&uW<PJB3;yWbZUH>0mrk1z6cw1_pIq5DhVm9s$_H6CZPc*I}!SBw6OZ1v9sBy)MKs=sXTRtuf2x!88ySlczfM*1L{RIto|3F%qTrlI^uP^U3jf##!f?<~&6;aT6#>o$u&-?z}Pu@u{q+Qf+ae`D^B1a!705D1MS*|c1YJ*21@R-OfpFE0nL6S?|Z;p4ae2(w3xco``#zWNWFWk=x)VYu$uS2-)-ha|PeY<_}2DxqQ?)voy?);^2uIQQ0)ek|AV^-<eJDqdK1DhPodj%Gc#-iAn?>zihKoh-XYn9mD6^w^Nqn>Bxz?LM3UtlKUAjYT0$X7fAhv$90GV&b@0r}`LB*oMmWF;B$5QhQ}>6e(}R%9_1t&DZd7VIxP97%eh?%HuoTS9;NKs+icGIc^D!=lYeQ*z!NEa)80Rz|#FlD<d|VaKYzAFbFIcOO?lr6GYEzp;~#LT9zOsW=6a&X)|d4hn=(o(@;$N6*0G+Cn9A-JcHxJEpDxV*2y=!gc0Wh=_AS$6~ODdEbs{9f4ufQ&k+bq@9Q-KA7^+1Y?LaKnnA=oqy0c{k~s6>TpyhXXo?sKqe_;kOP~N1{gmAAB4SGOSx%+;#Q5G&`(Xyq;OmGRG&}XWIaNOiBV&bwuz6EDfM)X5z6=`lAseksT?$rC|tBCv~gn1HW46>1d5rlcVc<{{3VR~f*oT?c!@P9c3vk(Lhw4{Fj@;e&>H5hV0e3YA*`lvQ)mBS=Iks$>=pqc^}tpM9TsGJi*DobY-q1VMz;}fjqe}tz+-F&ZLk40*iO8BLM(ar{Qn!b)+RutZ?0@?gvDq{HoixC?zlvPe@0^8B%%K|6S>ET2~7)c6ysDMAClKgdNA`jfrWm^=@iTl;jV`Sm5Bs-P`P3yt5@CS>ybpyK!F(xMu=+{m%hwzu(>Ts_FVYv@22(@+fPo7I2i+l?jZRlIrs)Lr^t&c3~Xu*)1a%96*g$Y#uRQ9IEi5oV%)FBDa~NT(VLn03vpVd2J9qy{|oc;1L~p|C?f@-2IO77gQw*pu@WkmW;~6GLKM9KH3Q95Fw%q1xCVpH81oury~Z>QRBZ-%>g2UG`Xx=jNMuX=jW1NANVpA{iCVSU)ToNJnUg+oCISunB3umkonD<*R6G|z*95IAv*{qw1YRTwNx>%f-{$3)UMBZRd48u#i>ALS4ZB#tusnGNj22J0@()SOo;7Ns4M*zJsjyc7B;F<K-{ilk_chR2lv-8#E(4kyl23bq0nmY>SN!|5@h81CnmgS_I1C+ktT?z_Jul~d{7vGkI5yGyq1}7g<))(tgcqr0$l9~vQEbsSMUc$DfMd}c@WA9*KS3_RJ9m~HsUucT=evt|jDe(ZBj4gIChC=J8A?-AkI<2OOpsij=YI8N=gKLtIsLc6w7*zlFZQ|qVEx1*9yq`J$=cJK<OOhNSmFIeG(I2Pcjq3{OgAt>+gDE_czS=gqd=#L@z9WX+LVa*?Px|{3774#?zV?3Zsww>L}^K0b%G8T_Oc4pTfIb$(AkyEUQm@N`wVlkhwy9S00;$P%u_Ad5*bxS#yi!bG0$Lu@oDo&HYmb-)?Rz<)tCVCD{L1ETJEKEB+$<n$+dFS1Eo$W6DqzI2XFz#r3We8MQCrp{L5&rVJLWvJ;_qY<MCJ#h){252yfuV*(5><(4b%TZEO%AjQbD95Z-uS*q`NX_>yVXr9?lw<O4^BnIYaCwk|kMx+)ZxTdAEGx$xQ3775^#Q(YcIN<3H_1hOYQ0Ty9PNKiRgl&x9(8fzTpZAmqJP8wP-C?(mt2?kM&XW`mG{Y?C>v(W6)kECrlguZ9Tnolf!Q9K6kc)&puM@hJj9^jQjU*y0ux}xr$qx=_G>H4)x=!&{^ef`4IwMXaaQw($8`mS^RF5Ztn|2eDk1aoI-39SZoFq`J_vOE8aJAWc#N~<*eCCO%%aH!jvkC;hhmNZ4D={+M7Tbsy`YoHR^yeJIFFDE5cvCc(SQA}RqP*=bxpsW`#r~)m-xCa0$#2Ye~A+La=Q$nJmJt+y__d#HS5bEpn?lGz2S%KbIxS5f1QW;b!m`s?;GYKd3eR`n^F4OL^0-;-PaM6uR!Df9;k_<EHa4k_=Vz0({7S$(_e+b1ibtkl{e%#71eO^P&XMb0C#ooeu>!Tgjtm!Bf7UYEUhH*}oid$BTVi(v%a~c)%-bm)$*>z4lhv*0c?fmsKL?a>UNcw>||K&|)(|>o<>t3n%T94mxda$V`ruZ@8NO;wDf#{L>=w?l{^kXc!w*|_3eFAbpJCC=8xF4BNK1qT#$Sgt<HS{#8LOwJz@6E0y3@H-aMYB#y+U8%C;vTyVI`PK&6?li4<iCTG2HD7qisd0|qSeq0XFvY3q>6^PBnwwmfMUvuf;XyTrZdLtNkV1OZ+t+V+!Zu<TdOzU^r|u=s7c;RtFlfck-m5lFT5vVmZAtmWoe27hPH^Q$Mt4Wq%W=J-?a(qIlKBj6)cU_WBvki1|2VdXMuQTUsKqjm%WnLnqg)qdAVmX{-#en`JLJ$3xtb7M}GDiOH3|~1^H~XDmoMCAV^^`$oP8et#{s(+^658Xs%U@eAqI*dlD}clV?dl4+<EJX77KN=(K2%)$UIVjbmi3Rvl!?((bthuU$eEHq&El-klUZ2FHT!+!)j0g|hZ~K;<&)g7;q28MM!w3?;h>p)|OD!OyA-r_{j{d`q*?zJ9xX@<~s6pd(<HZN#Y%Z|B*dZOckq(C*VOG2BOedE}UW^vV=G4K{a%p3q05_u-G-pC3~5x!0yCwI))!^im2hcP?CV?|#u<T5R9@nckPg@}vFKVL);;D|+q86?gu4`^$4Z@TD4QebXHEwH3T*N%)}F54885`p>y<h|_Yxz4?8SU5fwpT>IzK>0NlqGb!Dy&*2*;V4Ulh&y#z0J_G9>h`zhDfT7oKJ`!HoG4fXSU?J;1xY@n`&^<Q4e*W|e8~2yPY??H@k}Mti(n6m)yGtuR0d}AKii^vqIbvq!(qi}KnSOrAQ2LzEesE{~#HpTyX_z^daX^M-1EUIlhxu?7tvR0&BQ>2m<=?aCe*RHs<q~5JtDlkb(|AfEwP&Vz<cNmk?Fyo@1-Qw}F9pl~Q+Mgw+M~O8G~xcIop1l<&OfBD<!b+MlXq9Acj;-jX`Q|7FFvzhSWlgM7wIgifs@_=7{@uV(Na4Y@H%*RXqVv5-*WF=W}d5W|JJ^6A7AM3%PI06F^ufHiwo}Zc`SMQea}TCX)}q8Wgv+K3N#0TrbIxuJ$SWl<j{khJRkCY6h^q$@2)MM?LNH(EQQVm$duW0$L2|bGkLx8-IG8TxMcT!#uF4jKkU)BI@P1^Z9(63Y1&uk&}{UH!?Pb@JCJL&@VEwjshcp6|Ie*?0e<vED)x^)p0okvgAwqKM4<fjOXu9V-iQU|FO{3Gx~Fcq$1j2UCInf`Nvm#c3I<l|wfzHQqL<pW_#M@H_()MY*fM<;X>XWF6_4lm_xQnHAZab%ff%E&aHTKWOYaC@rjWDgJqB^=-|DXrg-_oa)EgSFvc=wbm)4LR`bgdNuZBm_zIM~Sx9To`<1XBwuODnLonL#>LsZfCfh11xm3$0%_u)1Et%mN$i=CftqyzUZKTa9@Kny;t+xpyM`-`(M1tI^Rg<c}BUfFp6^6yHdovb~*hKj|B8@Q3roozol*7<uc^`1GqQ@RT847^e<;1cJ~(!J|n8UrdrcZr^$_j!XwPNqV~#IV7xZO@hOIp)!D;f?B~%;MJtti=+EZK<i^C2=7u*MAK|pJ#oCa?^&7;)Lm-*bal|+-rZSbDF$=lRC7}2M~Ixua{y-+IkrP5!Xg!YOMBPx(gtj;LYlL@u`UE|0iv_WU5wWH@@4<YBi%TLXCSI7`&J-fkoaA8=(2p<-_B3(R0{zE+&2RCt!<v2PnlB_rg2!n`~wFo$fpKgY1*6Fp%{QOw=pvD}rDFES<y){F}5T9~o=}v8)e1(z5|#6Ef!+RCd=|=677<z~^1|PG9H?#b@i0p?V_;zq7>;%7^GUDZCyItM^61=P~T~-L<?#&5h~HcP~YVEb5ebIkdw<beBeReSJEf=EQt$MInw<MM1ZUqOkaOXz<kG(%0J|=3OXlsbvkl2?3R0*xxUfssBX*K+#JS;s+d`=_plQKdO=Fvrdj)Po$p#Me2&Y)wk-ut@yA(y-_pa7d8(*|CikJjokAU!FylxEcI03Yl%y<u{3$76x;cDNhnouqC-WK=*&s?%9q{MZ#>`7>+}WofTaHqc4EJS'))
if _terry_hashlib.sha256(_TERRY_RAW).hexdigest() != _TERRY_SOURCE_HASH:
    raise RuntimeError('Original TerryKiddy ASR payload SHA-256 mismatch')
_TERRY_ORIGINAL = _terry_types.ModuleType('_terrykiddy_upstream_asr')
_TERRY_ORIGINAL.__file__ = __file__
_TERRY_ORIGINAL.__package__ = None
exec(compile(_TERRY_RAW, 'TerryKiddy/live-caption@' + _TERRY_PIN + '/live_caption.py', 'exec'), _TERRY_ORIGINAL.__dict__)
# ===== ASR UI PARAMETER ADAPTER: upstream bytes remain untouched =====
import ast as _ui_ast
import copy as _ui_copy

def _ui_install_asr_adapters():
    """Compile narrow runtime overrides from the byte-exact TerryKiddy original."""
    if getattr(_TERRY_ORIGINAL, "_ui_configured", False):
        return
    _tree = _ui_ast.parse(_TERRY_RAW.decode("utf-8"))
    _defs = {n.name: n for n in _tree.body
             if isinstance(n, (_ui_ast.ClassDef, _ui_ast.FunctionDef))}

    def _rewrite(cls_name, method_name, transform):
        parent = _defs[cls_name] if cls_name else None
        members = parent.body if parent else _tree.body
        matches = [n for n in members if isinstance(n, _ui_ast.FunctionDef)
                   and n.name == method_name]
        if len(matches) != 1:
            raise RuntimeError("Upstream ASR function changed: " + method_name)
        node = _ui_copy.deepcopy(matches[0])
        node.decorator_list = []
        found = transform(node)
        if not found:
            raise RuntimeError("Upstream ASR constant changed: " + method_name)
        snippet = _ui_ast.fix_missing_locations(_ui_ast.Module(body=[node], type_ignores=[]))
        local = {}
        exec(compile(snippet, "<terrykiddy-ui-parameter-adapter>", "exec"),
             _TERRY_ORIGINAL.__dict__, local)
        if cls_name:
            setattr(getattr(_TERRY_ORIGINAL, cls_name), method_name, local[method_name])
        else:
            setattr(_TERRY_ORIGINAL, method_name, local[method_name])

    def _replace_literal(node, text, expr):
        matches = 0
        for child in _ui_ast.walk(node):
            for field, value in _ui_ast.iter_fields(child):
                if isinstance(value, _ui_ast.Constant) and value.value == text:
                    setattr(child, field, _ui_ast.parse(expr, mode="eval").body)
                    matches += 1
                elif isinstance(value, list):
                    for i, entry in enumerate(value):
                        if isinstance(entry, _ui_ast.Constant) and entry.value == text:
                            value[i] = _ui_ast.parse(expr, mode="eval").body
                            matches += 1
        return matches == 1

    def _replace_context(node):
        matches = 0
        for child in _ui_ast.walk(node):
            for field, value in _ui_ast.iter_fields(child):
                if (isinstance(value, _ui_ast.Subscript) and
                        isinstance(value.value, _ui_ast.Attribute) and
                        value.value.attr == "last" and
                        isinstance(value.slice, _ui_ast.Slice)):
                    setattr(child, field,
                            _ui_ast.parse("_ui_previous_captions(self)", mode="eval").body)
                    matches += 1
        return matches == 1

    def _llama_request(node):
        found = {"max_tokens": 0, "temperature": 0}
        for child in _ui_ast.walk(node):
            if isinstance(child, _ui_ast.Dict):
                for i, key in enumerate(child.keys):
                    if isinstance(key, _ui_ast.Constant) and key.value in found:
                        field = key.value
                        child.values[i] = _ui_ast.Name(
                            id=("_TERRY_UI_MAX_TOKENS" if field == "max_tokens"
                                else "_TERRY_UI_TEMPERATURE"), ctx=_ui_ast.Load())
                        found[field] += 1
        return _replace_context(node) and found == {"max_tokens": 1, "temperature": 1}

    def _qwen_init(node):
        count = 0
        for child in _ui_ast.walk(node):
            if isinstance(child, _ui_ast.Call):
                for kw in child.keywords:
                    if kw.arg == "max_new_tokens":
                        kw.value = _ui_ast.Name(id="_TERRY_UI_MAX_TOKENS", ctx=_ui_ast.Load())
                        count += 1
        return count == 1

    def _vad_parameters(node):
        mapping = {
            "threshold": "vad_threshold",
            "min_speech_duration_ms": "vad_min_speech_ms",
            "min_silence_duration_ms": "vad_min_silence_ms",
            "speech_pad_ms": "vad_speech_pad_ms",
        }
        counts = {key: 0 for key in mapping}
        for child in _ui_ast.walk(node):
            if (isinstance(child, _ui_ast.Call) and
                    isinstance(child.func, _ui_ast.Name) and
                    child.func.id == "VadOptions"):
                for kw in child.keywords:
                    if kw.arg in mapping:
                        kw.value = _ui_ast.parse("args." + mapping[kw.arg], mode="eval").body
                        counts[kw.arg] += 1
        return all(v == 1 for v in counts.values())

    def _ui_previous_captions(backend):
        if not backend.use_context:
            return ""
        history = getattr(backend, "_terry_final_history", [])
        if not history and getattr(backend, "last", ""):
            history = [backend.last]
        max_lines = max(1, min(5, _TERRY_ORIGINAL._TERRY_UI_CONTEXT_HISTORY))
        max_chars = max(1, _TERRY_ORIGINAL._TERRY_UI_CONTEXT_CHARS)
        return "\n".join(history[-max_lines:])[-max_chars:]

    _TERRY_ORIGINAL.__dict__["_ui_previous_captions"] = _ui_previous_captions
    _TERRY_ORIGINAL._TERRY_UI_CTX_SIZE = 8192
    _TERRY_ORIGINAL._TERRY_UI_MAX_TOKENS = 200
    _TERRY_ORIGINAL._TERRY_UI_TEMPERATURE = 0.0
    _TERRY_ORIGINAL._TERRY_UI_CONTEXT_HISTORY = 1
    _TERRY_ORIGINAL._TERRY_UI_CONTEXT_CHARS = 80

    for kind in ("LlamaBackend", "QwenBackend"):
        backend_cls = getattr(_TERRY_ORIGINAL, kind)
        old_setattr = backend_cls.__setattr__
        def capture_final(self, name, value, setter=old_setattr):
            if name == "last" and isinstance(value, str) and value:
                hist = list(getattr(self, "_terry_final_history", ()))
                hist.append(value)
                object.__setattr__(self, "_terry_final_history", hist[-5:])
            setter(self, name, value)
        backend_cls.__setattr__ = capture_final

    _rewrite("LlamaBackend", "__init__",
             lambda fn: _replace_literal(fn, "8192", "str(_TERRY_UI_CTX_SIZE)"))
    _rewrite("LlamaBackend", "transcribe", _llama_request)
    _rewrite("LlamaBackend", "_prompt", _replace_context)
    _rewrite("QwenBackend", "__init__", _qwen_init)
    _rewrite("QwenBackend", "transcribe", _replace_context)
    _rewrite(None, "run_asr", _vad_parameters)
    _TERRY_ORIGINAL._ui_configured = True


_ui_install_asr_adapters()

# ===== VAD STREAMING BOUNDARY REPAIR (does not modify upstream source bytes) =====
# Applied after the prior ASR parameter adapter and before the public run_asr wrapper.
import ast as _sv_ast
import copy as _sv_copy

def _sv_trim_final_audio(audio, speech_samples, pad_samples):
    """Keep actual speech plus requested trailing audio, not arbitrary endpoint wait."""
    stop_at = max(0, min(len(audio), speech_samples + pad_samples))
    return audio[:stop_at].copy()

def _sv_install():
    tree = _sv_ast.parse(_TERRY_RAW.decode("utf-8"))
    matches = [n for n in tree.body if isinstance(n, _sv_ast.FunctionDef) and n.name == "run_asr"]
    if len(matches) != 1:
        raise RuntimeError("Unexpected TerryKiddy ASR entry point")
    fn = _sv_copy.deepcopy(matches[0])
    def parse_stmt(text):
        return _sv_ast.parse(text).body
    def fmt(node):
        return _sv_ast.unparse(node)
    def is_name(node,name):
        return isinstance(node,_sv_ast.Name) and node.id==name

    # Keep the previous adapter's four VAD settings.
    vad_matches = 0
    for n in _sv_ast.walk(fn):
        if isinstance(n,_sv_ast.Call) and is_name(n.func,"VadOptions"):
            keys={"threshold":"vad_threshold",
                  "min_speech_duration_ms":"vad_min_speech_ms",
                  "min_silence_duration_ms":"vad_min_silence_ms",
                  "speech_pad_ms":"vad_speech_pad_ms"}
            assert {kw.arg for kw in n.keywords}==set(keys)
            for kw in n.keywords:
                kw.value = _sv_ast.parse("args."+keys[kw.arg],mode="eval").body
            vad_matches+=1
    if vad_matches!=1:raise RuntimeError("TerryKiddy VadOptions changed")

    # Define bounded pre-roll, tail padding, and minimum silence from UI.
    init=0
    for i,n in enumerate(fn.body):
        if isinstance(n,_sv_ast.Assign) and any(is_name(t,"speech_samples") for t in n.targets):
            fn.body[i+1:i+1]=parse_stmt("""
_sv_pad_samples = max(0, int(SR * args.vad_speech_pad_ms / 1000.0))
_sv_min_silence_s = max(0.0, args.vad_min_silence_ms / 1000.0)
_sv_pre_audio = np.zeros(0, dtype=np.float32)
""")
            init+=1;break
    if init!=1:raise RuntimeError("No speech_samples initialization")

    guard=0;start=0;final=0;silence=0
    for n in list(_sv_ast.walk(fn)):
        if not isinstance(n,_sv_ast.If):continue
        code=fmt(n.test)
        if code=="seg_start is None" and len(n.body)==1 and isinstance(n.body[0],_sv_ast.Continue):
            n.body[:0]=parse_stmt("""
if _sv_pad_samples:
    _sv_pre_audio = np.concatenate((_sv_pre_audio, win))[-_sv_pad_samples:]
""")
            guard+=1
        elif code=="seg_start is None" and n.body and isinstance(n.body[0],_sv_ast.Assign) and "last_partial" in fmt(n.body[0]):
            n.body+=parse_stmt("""
buf = _sv_pre_audio.copy()
seg_start -= len(_sv_pre_audio) / SR
_sv_pre_audio = np.zeros(0, dtype=np.float32)
""")
            start+=1
        elif "sil >= args.silence" in code and "reason = 'silence'" in fmt(n.body):
            expected="sil >= args.silence and seg_len >= args.min_seg or sil >= args.silence * 2.5"
            if code!=expected:raise RuntimeError("Unexpected silence endpoint: "+code)
            n.test=_sv_ast.parse("""(
    (sil >= max(args.silence, _sv_min_silence_s) and seg_len >= args.min_seg)
    or sil >= max(args.silence * 2.5, _sv_min_silence_s)
)""",mode="eval").body
            silence+=1
    for n in _sv_ast.walk(fn):
        if not isinstance(n,_sv_ast.Call) or not is_name(n.func,"submit"):continue
        if len(n.args)>2 and isinstance(n.args[0],_sv_ast.Constant) and n.args[0].value=="final":
            if not is_name(n.args[1],"buf"):continue
            n.args[1]=_sv_ast.parse("""(
    _sv_trim_final_audio(buf, speech_samples, _sv_pad_samples)
    if reason == "silence" else buf
)""",mode="eval").body
            final+=1
    if (guard,start,final,silence)!=(1,1,1,1):
        raise RuntimeError("TerryKiddy streaming layout changed: "+str((guard,start,final,silence)))
    g=_TERRY_ORIGINAL.__dict__
    g["_sv_trim_final_audio"]=_sv_trim_final_audio
    compiled=_sv_ast.fix_missing_locations(_sv_ast.Module(body=[fn],type_ignores=[]))
    local={}
    exec(compile(compiled,"<TerryKiddy-stream-VAD-adapter>","exec"),g,local)
    _TERRY_ORIGINAL.run_asr=local["run_asr"]
    _TERRY_ORIGINAL._sv_boundary_installed=True

_sv_install()



def run_asr(args, sink, stop, status=None):
    # UI-facing settings are applied only in this independent adaptation layer.
    _TERRY_ORIGINAL._TERRY_UI_CTX_SIZE = int(getattr(args, "ctx_size", 8192))
    _TERRY_ORIGINAL._TERRY_UI_MAX_TOKENS = int(getattr(args, "max_tokens", 200))
    _TERRY_ORIGINAL._TERRY_UI_TEMPERATURE = float(getattr(args, "temperature", 0))
    _TERRY_ORIGINAL._TERRY_UI_CONTEXT_HISTORY = int(getattr(args, "context_history", 1))
    _TERRY_ORIGINAL._TERRY_UI_CONTEXT_CHARS = int(getattr(args, "context_chars", 80))
    if getattr(args, "max_seg", None) == float("inf"):
        # The saved zero value was converted to inf in _apply_asr_config.
        # Enforce the user-visible documented 30-second safety cutoff.
        args.max_seg = 30.0
    if getattr(args, "llama_url", None) and _TERRY_ORIGINAL._TERRY_UI_CTX_SIZE != 8192:
        print("ASR warning: --ctx-size needs a freshly launched llama-server; the external server retains its own value.", flush=True)
    return _TERRY_ORIGINAL.run_asr(args, sink, stop, status)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        _lc_report_fatal_error(traceback.format_exc())
        raise SystemExit(1)

# Embedded translation/*.py source below (including hardening patches).
# Indentation and if False keep the original modules readable in this one file.

if False:  # Embedded translation/text.py
    # <<< INLINE translation/text.py BEGIN >>>
    """Translation-only Python port of upstream TextUtil.cs and RegexPatterns.cs."""
    import re

    PUNC_EOS = ".?!。？！"
    PUNC_COMMA = ",，、—\n"
    SHORT_THRESHOLD = 10
    MEDIUM_THRESHOLD = 40
    VERYLONG_THRESHOLD = 220
    SIM_THRESHOLD = 0.6
    NOTICE = re.compile(r"^\[.+\] ")


    def cj(ch):
        n = ord(ch)
        return any(a <= n <= b for a,b in ((0x4E00,0x9FFF),(0x3400,0x4DBF),
            (0x3000,0x303F),(0x3040,0x309F),(0x30A0,0x30FF),
            (0x31F0,0x31FF),(0x3200,0x32FF),(0x3300,0x33FF)))


    def replace_newlines(text, threshold=MEDIUM_THRESHOLD):
        parts = text.split("\n")
        for i in range(len(parts)-1):
            part = parts[i].strip()
            if part:
                part += ("。" if cj(part[-1]) else ". ") if len(part.encode("utf-8")) >= threshold else ("——" if cj(part[-1]) else "—")
            parts[i] = part
        parts[-1] = parts[-1].strip()
        return "".join(parts)


    def preprocess(text):
        # Translator.SyncLoop upstream ordering.
        text = re.sub(r"([A-Z])\s*\.\s*([A-Z])(?![A-Za-z]+)",r"\1\2",text)
        text = re.sub(r"([A-Z])\s*\.\s*([A-Z])(?=[A-Za-z]+)",r"\1 \2",text)
        text = re.sub(r"\s*([.!?,])\s*",r"\1 ",text)
        text = re.sub(r"\s*([。！？，、])\s*",r"\1",text)
        return replace_newlines(text)


    def last_eos(text):
        return max((text.rfind(c) for c in PUNC_EOS),default=-1)


    def latest_caption(text):
        if not text:
            return ""
        eos = last_eos(text[:-1] if text[-1] in PUNC_EOS else text)
        latest = text[eos+1:]
        if eos > 0 and len(latest.encode("utf-8")) < SHORT_THRESHOLD:
            latest = text[last_eos(text[:eos])+1:]
        return latest


    def extract_original_caption(text):
        latest = latest_caption(text)
        eos = last_eos(latest)
        return latest[:eos+1] if eos >= 0 else latest


    def shorten(text, maximum=VERYLONG_THRESHOLD):
        while len(text.encode("utf-8")) >= maximum:
            i = next((i for i,c in enumerate(text) if c in PUNC_EOS + PUNC_COMMA),-1)
            if i < 0 or i+1 >= len(text):
                break
            text = text[i+1:]
        return text


    def _utf16_units(text):
        """C# string.Length/IndexOf use UTF-16 code units, unlike Python len()."""
        raw=text.encode("utf-16-le","surrogatepass")
        return [raw[i]|(raw[i+1]<<8) for i in range(0,len(raw),2)]


    def _similarity_units(a,b):
        if a[:len(b)]==b or b[:len(a)]==a:
            return 1.0
        if len(a)>len(b):
            a,b=b,a
        old=list(range(len(a)+1))
        for j,right in enumerate(b,1):
            cur=[j]
            for i,left in enumerate(a,1):
                cur.append(min(cur[-1]+1,old[i]+1,old[i-1]+(left!=right)))
            old=cur
        return 1.0-old[-1]/max(1,len(b))


    def similarity(a,b):
        return _similarity_units(_utf16_units(a),_utf16_units(b))


    def similar_to_last(a,b):
        if not b:
            return False
        left,right=_utf16_units(a),_utf16_units(b)
        n=min(len(left),len(right))
        return _similarity_units(left[:n],right[:n])>SIM_THRESHOLD
    # <<< INLINE translation/text.py END >>>

if False:  # Embedded translation/history.py
    # <<< INLINE translation/history.py BEGIN >>>
    """SQLite history and upstream-compatible search, pagination and import."""
    import csv
    import sqlite3
    import threading
    from contextlib import contextmanager
    from dataclasses import dataclass
    from datetime import datetime
    from pathlib import Path
    import time
    from .text import similar_to_last, NOTICE, PUNC_EOS, cj


    @dataclass(frozen=True)
    class TranslationEntry:
        timestamp: str
        source: str
        translated: str
        target: str
        engine: str


    def _timestamp(value):
        """Migrate historical C# Unix timestamps and deprecated date strings."""
        raw = str(value)
        try:
            return datetime.fromtimestamp(float(raw)).isoformat(timespec="seconds")
        except (ValueError, OverflowError, OSError):
            try:
                return datetime.fromisoformat(raw).isoformat(timespec="seconds")
            except ValueError:
                return raw


    class HistoryStore:
        def __init__(self,path):
            self.path=str(path)
            self.lock=threading.RLock()
            Path(self.path).parent.mkdir(parents=True,exist_ok=True)
            with self.lock,self._db() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS translation_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    source_text TEXT NOT NULL,translated_text TEXT NOT NULL,
                    target_language TEXT NOT NULL,api_used TEXT NOT NULL)""")
                db.execute("""CREATE TABLE IF NOT EXISTS translation_migrations(
                    migration TEXT PRIMARY KEY)""")
                # Finals remain here until history and acknowledgement commit in
                # one transaction. No user text is sent anywhere by this journal.
                db.execute("""CREATE TABLE IF NOT EXISTS translation_pending(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_text TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_error TEXT NOT NULL DEFAULT '')""")
                fields={row[1] for row in db.execute('PRAGMA table_info(translation_pending)')}
                for col,definition in (
                    ('attempts', 'INTEGER NOT NULL DEFAULT 0'),
                    ('next_attempt', 'REAL NOT NULL DEFAULT 0'),
                    ('status', "TEXT NOT NULL DEFAULT 'pending'"),
                    ('last_error', "TEXT NOT NULL DEFAULT ''")):
                    if col not in fields:
                        db.execute(f'ALTER TABLE translation_pending ADD COLUMN {col} {definition}')
                db.execute("""CREATE TABLE IF NOT EXISTS translation_carry(
                    id INTEGER PRIMARY KEY CHECK(id=1), source_text TEXT NOT NULL)""")
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='TranslationHistory'").fetchone():
                    marker=db.execute("""SELECT 1 FROM translation_migrations
                                           WHERE migration='import_upstream'""").fetchone()
                    if not marker:
                        # Never destroy the original upstream table or import twice.
                        for row in db.execute("""SELECT Timestamp,SourceText,TranslatedText,
                                                  TargetLanguage,ApiUsed FROM TranslationHistory
                                                  ORDER BY Id ASC""").fetchall():
                            db.execute("""INSERT INTO translation_history(
                                timestamp,source_text,translated_text,target_language,api_used
                                ) VALUES(?,?,?,?,?)""",(_timestamp(row[0]),*row[1:]))
                        db.execute("""INSERT INTO translation_migrations(migration)
                                      VALUES('import_upstream')""")

        @contextmanager
        def _db(self):
            connection=sqlite3.connect(self.path,timeout=5)
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

        @staticmethod
        def _entry(row):
            return TranslationEntry(*row)

        def last(self):
            with self.lock,self._db() as db:
                row=db.execute("""SELECT timestamp,source_text,translated_text,
                     target_language,api_used FROM translation_history
                     ORDER BY id DESC LIMIT 1""").fetchone()
                return self._entry(row) if row else None

        def log(self,source,translated,target,engine,overwrite=None):
            with self.lock,self._db() as db:
                return self._log(db,source,translated,target,engine,overwrite)

        @staticmethod
        def _log(db,source,translated,target,engine,overwrite):
            last=db.execute("""SELECT id,source_text FROM translation_history
                               ORDER BY id DESC LIMIT 1""").fetchone()
            if overwrite is None:
                overwrite=bool(last and similar_to_last(source,last[1]))
            if overwrite and last:
                db.execute("DELETE FROM translation_history WHERE id=?",(last[0],))
            db.execute("""INSERT INTO translation_history(
                    timestamp,source_text,translated_text,target_language,api_used)
                    VALUES(?,?,?,?,?)""",(datetime.now().isoformat(timespec="seconds"),
                    source,translated,target,engine))
            return bool(overwrite and last)

        def accept_translation_final(self, source, continuation=False):
            from .text import preprocess
            with self.lock,self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT source_text FROM translation_carry WHERE id=1').fetchone()
                pending=(row[0] if row else '')+source
                if continuation:
                    while len(pending)>=8192:
                        db.execute('INSERT INTO translation_pending(source_text) VALUES(?)',
                                   (preprocess(pending[:8192]),))
                        pending=pending[8192:]
                elif pending:
                    db.execute('INSERT INTO translation_pending(source_text) VALUES(?)',(preprocess(pending),))
                    pending=''
                db.execute('INSERT OR REPLACE INTO translation_carry(id,source_text) VALUES(1,?)',(pending,))
                return pending

        def flush_translation_carry(self):
            from .text import preprocess
            with self.lock,self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT source_text FROM translation_carry WHERE id=1').fetchone()
                if row and row[0]:
                    db.execute('INSERT INTO translation_pending(source_text) VALUES(?)',(preprocess(row[0]),))
                db.execute('DELETE FROM translation_carry WHERE id=1')

        def pending_translation(self):
            # Skip deferred poison jobs without deleting them or starving newer
            # speech. Each Final is either pending, committed or inspectable failed.
            with self.lock,self._db() as db:
                return db.execute("""SELECT id,source_text FROM translation_pending
                    WHERE status='pending' AND next_attempt<=?
                    ORDER BY id LIMIT 1""",(time.time(),)).fetchone()

        def defer_translation(self, identifier, error):
            with self.lock,self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT attempts FROM translation_pending WHERE id=?',
                               (identifier,)).fetchone()
                if row is None:return None
                attempts=row[0]+1
                status='failed' if attempts>=6 else 'pending'
                delay=min(300., 2.0**min(attempts,8))
                db.execute("""UPDATE translation_pending SET attempts=?, status=?,
                    next_attempt=?, last_error=? WHERE id=?""",
                    (attempts,status,time.time()+delay,str(error)[:400],identifier))
                return status

        def failed_translation_count(self):
            with self.lock,self._db() as db:
                return db.execute("SELECT COUNT(*) FROM translation_pending WHERE status='failed'").fetchone()[0]

        def retry_failed_translations(self):
            with self.lock,self._db() as db:
                count=db.execute("SELECT COUNT(*) FROM translation_pending WHERE status='failed'").fetchone()[0]
                db.execute("""UPDATE translation_pending SET status='pending',
                    attempts=0, next_attempt=0, last_error='' WHERE status='failed'""")
                return count

        def pending_translation_count(self):
            with self.lock,self._db() as db:
                return db.execute('SELECT COUNT(*) FROM translation_pending').fetchone()[0]

        def complete_translation(self, identifier, translated, target, engine):
            with self.lock,self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT source_text FROM translation_pending WHERE id=?',(identifier,)).fetchone()
                if row is None:
                    return False
                self._log(db,row[0],translated,target,engine,False)
                db.execute('DELETE FROM translation_pending WHERE id=?',(identifier,))
                return True

        def delete_last(self):
            with self.lock,self._db() as db:
                db.execute("""DELETE FROM translation_history WHERE id IN (
                              SELECT id FROM translation_history ORDER BY id DESC LIMIT 1)""")

        def recent(self,limit=10):
            with self.lock,self._db() as db:
                rows=db.execute("""SELECT timestamp,source_text,translated_text,
                    target_language,api_used FROM translation_history ORDER BY id DESC LIMIT ?""",
                    (max(0,min(100000,int(limit))),)).fetchall()
            return [self._entry(row) for row in reversed(rows)]

        def search(self,term="",page=1,page_size=50):
            """Return (newest-first rows, maximum page), matching upstream semantics."""
            page_size=max(1,int(page_size))
            page=max(1,int(page))
            term="%"+str(term)+"%"
            with self.lock,self._db() as db:
                count=db.execute("""SELECT COUNT(*) FROM translation_history
                    WHERE source_text LIKE ? OR translated_text LIKE ?""",(term,term)).fetchone()[0]
                rows=db.execute("""SELECT timestamp,source_text,translated_text,
                    target_language,api_used FROM translation_history
                    WHERE source_text LIKE ? OR translated_text LIKE ?
                    ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?""",
                    (term,term,page_size,(page-1)*page_size)).fetchall()
            return [self._entry(row) for row in rows],max(1,(count+page_size-1)//page_size)

        def count(self,term=""):
            query="%"+str(term)+"%"
            with self.lock,self._db() as db:
                return int(db.execute("""SELECT COUNT(*) FROM translation_history
                       WHERE source_text LIKE ? OR translated_text LIKE ?""",
                       (query,query)).fetchone()[0])

        def clear(self):
            """Atomically forget history, unacknowledged Finals and carried text."""
            with self.lock,self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM translation_history")
                db.execute("DELETE FROM translation_pending")
                db.execute("DELETE FROM translation_carry")
                db.execute("DELETE FROM sqlite_sequence WHERE name IN ('translation_history','translation_pending')")

        def export_csv(self,path):
            """Export ALL records, independent of GUI page size (UTF-8 BOM)."""
            with self.lock,self._db() as db:
                rows=db.execute("""SELECT timestamp,source_text,translated_text,
                    target_language,api_used FROM translation_history
                    ORDER BY timestamp DESC,id DESC""").fetchall()
            with open(path,"w",newline="",encoding="utf-8-sig") as fp:
                writer=csv.writer(fp)
                writer.writerow(("Timestamp","TimestampFull","SourceText",
                                 "TranslatedText","TargetLanguage","ApiUsed"))
                for row in rows:
                    writer.writerow((row[0][:16].replace("T"," "),row[0].replace("T"," "),
                                     *row[1:]))
            return len(rows)

        def contexts(self,limit=2):
            return [r for r in self.recent(min(10,limit))
                    if r.translated!="N/A" and "[ERROR]" not in r.translated
                    and "[WARNING]" not in r.translated]

        def previous_text(self,limit=1,translated=True,entries=None):
            pieces=[]
            for entry in (self.contexts(limit) if entries is None else entries[-limit:] if limit>0 else []):
                # Upstream Caption.GetPreviousText suppresses invalid log cards.
                if (entry.translated=="N/A" or "[ERROR]" in entry.translated
                        or "[WARNING]" in entry.translated):
                    continue
                value=NOTICE.sub("",entry.translated) if translated else entry.source
                if not value:
                    continue
                if pieces and pieces[-1][-1] not in PUNC_EOS:
                    pieces[-1]+="。" if cj(pieces[-1][-1]) else ". "
                pieces.append(value)
            text="".join(pieces)
            if text and text[-1] not in PUNC_EOS:
                text+="。" if cj(text[-1]) else "."
            if text and ord(text[-1])<128:
                text+=" "
            return text
    # <<< INLINE translation/history.py END >>>

if False:  # Embedded translation/api.py
    # <<< INLINE translation/api.py BEGIN >>>
    """Google and Google2 adapters from upstream LiveCaptions-Translator.

    No other translation providers are supported. Transport remains cancellable and
    uses the same upstream Google / Google2 endpoints and response contracts.
    """
    import os
    import http.client
    import socket
    import select
    import ssl
    import threading
    from contextlib import contextmanager
    import json
    import math
    import re
    import time
    from dataclasses import dataclass, field
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen, HTTPHandler, HTTPSHandler, build_opener

    ENGINES = ("Google", "Google2")
    GOOGLE_URL = "https://clients5.google.com/translate_a/t"
    GOOGLE2_URL = "https://dictionaryextension-pa.googleapis.com/v1/dictionaryExtensionData"
    # Public Chrome extension client key from upstream TranslateAPI.cs.
    GOOGLE2_KEY = "AIzaSyA6EEtrDCfBkHV8uU2lgGY-N383ZgAOo7Y"


    @dataclass
    class TranslationSettings:
        engine: str = "Google"
        target: str = "zh-CN"
        context_aware: bool = False
        num_contexts: int = 2
        display_sentences: int = 1
        max_sync_interval: int = 3
        max_idle_interval: int = 50
        show_latency: bool = False
        timeout: float = 8.0
        log_only: bool = True
        configs: dict = field(default_factory=dict)
        config_indices: dict = field(default_factory=dict)

        @classmethod
        def from_file(cls, path):
            from pathlib import Path
            file = Path(path)
            if not file.is_file():
                return cls()
            data = json.loads(file.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("translation settings must be an object")
            # Preserve the original settings file until the user saves it. Ignore
            # retired provider credentials in memory; never migrate them into a
            # Google request or forward them to either provider.
            data = dict(data)
            retired = data.get("engine", "Google") not in ENGINES
            if retired:
                print("已移除的翻译引擎：", repr(data.get("engine")),
                      "；已切换 Google 并暂停翻译，请在设置中确认。", flush=True)
                data["engine"] = "Google"
                data["log_only"] = True
            for field_name in ("configs", "config_indices"):
                mapping = data.get(field_name, {})
                if isinstance(mapping, dict):
                    data[field_name] = {name: value for name, value in mapping.items()
                                        if name in ENGINES}
            instance = cls(**{key: value for key, value in data.items()
                              if key in cls.__dataclass_fields__})
            if not isinstance(instance.configs, dict):
                raise ValueError("configs must be an object")
            if not isinstance(instance.config_indices, dict):
                raise ValueError("config_indices must be an object")
            for name in ("context_aware", "log_only", "show_latency"):
                if type(getattr(instance, name)) is not bool:
                    raise ValueError(name+" must be boolean")
            for name in ("num_contexts", "display_sentences"):
                value = getattr(instance, name)
                if type(value) is not int or not 0 <= value <= 10:
                    raise ValueError(name+" must be an integer in 0..10")
            for name in ("max_sync_interval", "max_idle_interval"):
                value = getattr(instance, name)
                if type(value) is not int or not 0 <= value <= 1000:
                    raise ValueError(name+" must be an integer in 0..1000")
            if (type(instance.timeout) not in (int, float)
                    or not math.isfinite(instance.timeout) or instance.timeout <= 0):
                raise ValueError("timeout must be a finite positive number")
            if not isinstance(instance.target, str) or not instance.target.strip():
                raise ValueError("target must be a nonempty string")
            for name, profiles in instance.configs.items():
                if not isinstance(profiles, dict) and not (
                        isinstance(profiles, list) and profiles
                        and all(isinstance(profile, dict) for profile in profiles)):
                    raise ValueError("configuration must be an object or nonempty list of objects")
            for name, index in instance.config_indices.items():
                if type(index) is not int or index < 0:
                    raise ValueError("configuration index must be a nonnegative integer")
                profiles = instance.configs.get(name)
                if isinstance(profiles, list) and index >= len(profiles):
                    raise ValueError("invalid configuration index for " + name)
            return instance

        def config(self):
            if self.engine not in ENGINES:
                raise ValueError("Unsupported translation engine: " + str(self.engine))
            configurations = self.configs.get(self.engine, {}) or {}
            if isinstance(configurations, list):
                index = int(self.config_indices.get(self.engine, 0))
                if not 0 <= index < len(configurations):
                    raise ValueError("invalid configuration index for " + self.engine)
                configurations = configurations[index]
            if not isinstance(configurations, dict):
                raise ValueError("configuration must be an object or a list")
            def resolve(value):
                if isinstance(value, str) and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", value):
                    key = value[2:-1]
                    if key not in os.environ:
                        raise ValueError("Missing translation environment variable: " + key)
                    return os.environ[key]
                return value
            # Google ignores config parameters; Google2 supports a custom key.
            if self.engine == "Google2":
                return {key: resolve(value) for key, value in configurations.items()
                        if key in ("api_key", "ApiKey")}
            return {}


    def _config_google2_key(config):
        return config.get("api_key", config.get("ApiKey", GOOGLE2_KEY)) or GOOGLE2_KEY


    class TranslationCancelled(Exception):
        """An obsolete translation task cancelled its active HTTP transport."""


    class CancellationToken:
        """The per-task cancellation source, mirroring .NET CTS ownership.

        Cancellation shuts down tracked TCP/TLS sockets, unblocking concurrent
        response reads rather than merely suppressing a finished translation.
        """
        def __init__(self):
            self._lock = threading.Lock()
            self._cancelled = False
            self._deadline = None
            self._sockets = set()

        @property
        def cancelled(self):
            with self._lock:
                return self._cancelled

        @property
        def deadline(self):
            with self._lock:
                return self._deadline

        def start_request(self, timeout):
            with self._lock:
                self._deadline = time.monotonic() + float(timeout)
            self.check()

        def check(self):
            with self._lock:
                cancelled = self._cancelled
                deadline = self._deadline
            if cancelled:
                raise TranslationCancelled("translation request cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("timed out")

        def register(self, sock):
            if sock is None:
                return
            # urllib/http.client closes its connection socket immediately after
            # response headers, while a buffered makefile continues reading the
            # body. Hold an OS-level duplicate that remains open for cancellation.
            duplicate = sock.dup()
            with self._lock:
                cancelled = self._cancelled
                if not cancelled:
                    self._sockets.add(duplicate)
            if cancelled:
                try:
                    duplicate.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                duplicate.close()
                raise TranslationCancelled("translation request cancelled")

        def finish_request(self):
            with self._lock:
                sockets = tuple(self._sockets)
                self._sockets.clear()
                self._deadline = None
            for sock in sockets:
                sock.close()

        def cancel(self):
            with self._lock:
                self._cancelled = True
                sockets = tuple(self._sockets)
                self._sockets.clear()
            for sock in sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass



    class _PollingHTTPResponse(http.client.HTTPResponse):
        """Poll cancellability before each underlying socket recv.

        Windows can leave a blocking recv in progress after shutdown of a
        duplicated socket. Wrapping the unbuffered SocketIO readinto (before
        response headers) avoids that kernel-level cancellation race while
        preserving the stdlib's redirect, chunked decoding and buffering.
        """
        def __init__(self, sock, *, cancel_token, **kwargs):
            super().__init__(sock, **kwargs)
            raw = self.fp.raw
            receive = raw.readinto
            deadline = cancel_token.deadline or (
                time.monotonic() + (sock.gettimeout() or 8))

            def cancellable_readinto(buffer):
                want_write = False
                while True:
                    cancel_token.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("timed out")
                    transport = raw._sock
                    if want_write:
                        _, ready, _ = select.select(
                            [], [transport], [], min(0.04, remaining))
                    elif isinstance(transport, ssl.SSLSocket) and transport.pending():
                        ready = [transport]
                    else:
                        ready, _, _ = select.select(
                            [transport], [], [], min(0.04, remaining))
                    if ready:
                        cancel_token.check()
                        try:
                            n = receive(buffer)
                        except ssl.SSLWantWriteError:
                            want_write = True
                            continue
                        except (ssl.SSLWantReadError, BlockingIOError):
                            want_write = False
                            continue
                        if n is None:
                            continue
                        return n

            raw.readinto = cancellable_readinto


    class _TrackedConnection:
        """Mixin registers TCP connection immediately after connect, before IO."""
        def __init__(self, *args, cancel_token, **kwargs):
            self._cancel_token = cancel_token
            super().__init__(*args, **kwargs)
            self.response_class = lambda sock, **kw: _PollingHTTPResponse(
                sock, cancel_token=cancel_token, **kw)
            original = self._create_connection

            def tracked_connect(*connect_args, **connect_kwargs):
                cancel_token.check()
                sock = original(*connect_args, **connect_kwargs)
                cancel_token.register(sock)
                return sock

            self._create_connection = tracked_connect

        def send(self, data):
            """Nonblocking cancellable send of headers and request bodies."""
            if self.sock is None:
                self.connect()
            if hasattr(data, "read"):
                for block in iter(lambda: data.read(self.blocksize), b""):
                    self.send(block)
                return
            if isinstance(data, str):
                data = data.encode("iso-8859-1")
            remaining_bytes = memoryview(data)
            while remaining_bytes:
                self._cancel_token.check()
                deadline = self._cancel_token.deadline
                remaining_time = (deadline - time.monotonic()
                                  if deadline is not None else self.timeout)
                if remaining_time <= 0:
                    raise TimeoutError("timed out")
                try:
                    written = self.sock.send(remaining_bytes)
                    if written <= 0:
                        raise ConnectionError("translation HTTP socket closed")
                    remaining_bytes = remaining_bytes[written:]
                except ssl.SSLWantReadError:
                    select.select([self.sock], [], [], min(.04, remaining_time))
                except (BlockingIOError, ssl.SSLWantWriteError):
                    select.select([], [self.sock], [], min(.04, remaining_time))


    class _TrackedHTTPConnection(_TrackedConnection, http.client.HTTPConnection):
        def connect(self):
            self._cancel_token.check()
            super().connect()
            self._cancel_token.check()
            self.sock.setblocking(False)


    class _TrackedHTTPSConnection(_TrackedConnection, http.client.HTTPSConnection):
        def connect(self):
            self._cancel_token.check()
            # Equivalent to HTTPSConnection.connect, but register the SSL socket
            # before TLS handshake to make a blocked handshake cancellable too.
            http.client.HTTPConnection.connect(self)
            host = self._tunnel_host or self.host
            try:
                ssl_sock = self._context.wrap_socket(
                    self.sock, server_hostname=host, do_handshake_on_connect=False)
                self.sock = ssl_sock
                # SSLSocket cannot be dup()'ed. Its underlying TCP handle is
                # already tracked, and TLS negotiation itself is cooperatively
                # polled to honor cancellation during handshake.
                ssl_sock.setblocking(False)
                deadline = self._cancel_token.deadline or (
                    time.monotonic() + self.timeout)
                try:
                    while True:
                        self._cancel_token.check()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("timed out")
                        try:
                            ssl_sock.do_handshake()
                            break
                        except ssl.SSLWantReadError:
                            select.select([ssl_sock], [], [], min(0.04, remaining))
                        except ssl.SSLWantWriteError:
                            select.select([], [ssl_sock], [], min(0.04, remaining))
                finally:
                    # Keep TLS reads nonblocking: all SocketIO recv attempts run
                    # through the cancellable select/readinto guard.
                    ssl_sock.setblocking(False)
                self._cancel_token.check()
            except BaseException:
                self.close()
                raise


    class _CancellableHTTPHandler(HTTPHandler):
        def __init__(self, token):
            super().__init__()
            self._token = token

        def http_open(self, req):
            return self.do_open(
                lambda host, **kw: _TrackedHTTPConnection(
                    host, cancel_token=self._token, **kw), req)


    class _CancellableHTTPSHandler(HTTPSHandler):
        def __init__(self, token):
            super().__init__()
            self._token = token

        def https_open(self, req):
            return self.do_open(
                lambda host, **kw: _TrackedHTTPSConnection(
                    host, cancel_token=self._token, **kw), req)


    def _request(url, *, data=None, headers=None, timeout=8.0, form=False, cancel_token=None):
        request_headers=dict(headers or {})
        payload=None
        if data is not None:
            if form:
                payload=urlencode(data).encode("utf-8")
                request_headers.setdefault("Content-Type","application/x-www-form-urlencoded")
            else:
                payload=json.dumps(data,ensure_ascii=False).encode("utf-8")
                request_headers.setdefault("Content-Type","application/json")
        request=Request(url,data=payload,headers=request_headers,
                        method="POST" if data is not None else "GET")
        if cancel_token is not None:
            cancel_token.start_request(timeout)
            opener = build_opener(_CancellableHTTPHandler(cancel_token),
                                  _CancellableHTTPSHandler(cancel_token))
            open_request = opener.open
        else:
            open_request = urlopen
        try:
            with open_request(request, timeout=timeout) as result:
                payload = result.read()
                if cancel_token is not None:
                    cancel_token.check()
                return json.loads(payload.decode("utf-8"))
        except HTTPError as error:
            if cancel_token is not None and cancel_token.cancelled:
                raise TranslationCancelled("translation request cancelled") from error
            raise ValueError(f"HTTP Error - {error.code}") from error
        except Exception as error:
            if cancel_token is not None and cancel_token.cancelled:
                raise TranslationCancelled("translation request cancelled") from error
            raise
        finally:
            if cancel_token is not None:
                cancel_token.finish_request()


    def google_translate(text, target="zh-CN", timeout=8.0):
        url = GOOGLE_URL+"?"+urlencode({"client":"dict-chrome-ex", "sl":"auto", "tl":target, "q":text})
        return _request(url, timeout=timeout)[0][0]


    class TranslationAPI:
        def __init__(self, settings=None, requester=None):
            self.settings = settings or TranslationSettings()
            self.requester = requester or _request
            self._cancellation_local = threading.local()

        @contextmanager
        def cancellation(self, token):
            previous = getattr(self._cancellation_local, "token", None)
            self._cancellation_local.token = token
            try:
                if token is not None:
                    token.check()
                yield
            finally:
                self._cancellation_local.token = previous

        def _r(self, url, **kwargs):
            kwargs.setdefault("timeout", self.settings.timeout)
            token = getattr(self._cancellation_local, "token", None)
            if token is not None:
                token.check()
                if self.requester is _request:
                    kwargs["cancel_token"] = token
            return self.requester(url, **kwargs)

        def translate(self, text, contexts=()):
            engine = self.settings.engine
            # This guard is intentionally outside the HTTP exception handler.
            # Deleted providers cannot issue requests, even with manual overrides.
            if engine not in ENGINES:
                raise ValueError("Unsupported translation engine: " + str(engine))
            try:
                if engine == "Google":
                    url = GOOGLE_URL+"?"+urlencode({"client":"dict-chrome-ex", "sl":"auto",
                                                  "tl":self.settings.target, "q":text})
                    return self._r(url)[0][0]
                cfg = self.settings.config()
                url = GOOGLE2_URL+"?"+urlencode({"language":self.settings.target,
                                "key":_config_google2_key(cfg), "term":text, "strategy":"2"})
                reply = self._r(url, headers={"x-referer":
                    "chrome-extension://mgijmajocgfcbeboacabfgobmjgjcoja"})
                if "translateResponse" not in reply:
                    return "[ERROR] Translation Failed: Unexpected API response format"
                return reply["translateResponse"]["translateText"]
            except TranslationCancelled:
                raise
            except Exception as exc:
                reason = str(getattr(exc, "reason", ""))
                if isinstance(exc, TimeoutError) or (
                        isinstance(exc, URLError) and "timed out" in reason.lower()):
                    return ("[ERROR] Translation Failed: The request was canceled due to timeout (> 8 seconds), "
                            "please use a faster API or check network connection.")
                return "[ERROR] Translation Failed: " + str(exc)
    # <<< INLINE translation/api.py END >>>

if False:  # Embedded translation/engine.py
    # <<< INLINE translation/engine.py BEGIN >>>
    """Python implementation of upstream Translator/TranslationTaskQueue scheduling.

    The public sink accepts existing ASR partial/final strings unchanged. The
    ASR workers, VAD, model inference and audio path are entirely independent.
    """
    import queue
    from contextlib import nullcontext
    from collections import deque
    import re
    import threading
    import time
    import weakref
    from pathlib import Path

    from .api import (TranslationAPI, TranslationSettings,
                      CancellationToken, TranslationCancelled)
    from .history import HistoryStore
    from .text import (PUNC_EOS, SHORT_THRESHOLD, NOTICE, preprocess,
                       extract_original_caption, latest_caption, similar_to_last)

    ROOT = Path(__file__).resolve().parent.parent
    ACTIVE_SINKS = weakref.WeakSet()


    class GoogleTranslateSink:
        """Backward-compatible translation sink for Google and Google2."""
        PUNC_EOS = PUNC_EOS
        SHORT_THRESHOLD = SHORT_THRESHOLD
        MAX_SYNC_INTERVAL = 3
        MAX_IDLE_INTERVAL = 50
        SYNC_TICK = 0.025
        TRANSLATE_TICK = 0.040
        DISPLAY_TICK = 0.040
        COMPLETE_SENTENCE_CHOKE = 0.720

        def __init__(self, overlay, target="zh-CN", settings=None, api=None,
                     history=None, root=None):
            self.overlay = overlay
            self.closed = False
            root = Path(root or ROOT)
            config_invalid = False
            if settings is None:
                try:
                    settings = TranslationSettings.from_file(root / "translation-settings.json")
                except (OSError, ValueError, TypeError) as exc:
                    # Fail closed: a broken local/private configuration must never
                    # silently enable the default remote Google translation engine.
                    print("翻译配置无效，已暂停翻译；请检查翻译设置：", repr(exc), flush=True)
                    settings = TranslationSettings(log_only=True)
                    config_invalid = True
                    notify = getattr(overlay, "status", None)
                    if callable(notify):
                        try:
                            notify("⚠ 翻译配置无效：翻译已暂停，请在设置中修复。")
                        except Exception as report_error:
                            print("翻译配置警告显示失败：", repr(report_error), flush=True)
            self._config_invalid = config_invalid
            # Old caller always passed target='zh-CN'; don't override a custom target.
            if settings.target == "zh-CN" and target != "zh-CN":
                settings.target = target
            self.settings = settings
            self.target = settings.target
            self.api = api or TranslationAPI(settings)
            self.history = history or HistoryStore(root / "translation-history.sqlite3")
            # Custom history adapters keep the original queue protocol. The
            # production SQLite store owns the durable Final FIFO and carry.
            self._final_store = self.history if isinstance(self.history, HistoryStore) else None
            if self._final_store is not None:
                self._final_store.flush_translation_carry()
                # Old databases do not record a Final flag. Conservatively protect
                # their last committed row when a new process starts with Partial.
                self._history_last_is_final = self.history.last() is not None
            self._settings_generation = 0

            self.state_lock = threading.Lock()
            self.observed_text = ""
            self.finalized_text = ""
            self.context_entries = deque(maxlen=10)
            self.original_caption = ""
            self.idle_count = 0
            self.sync_count = 0
            self.pending_text_queue = queue.Queue(maxsize=1 if self._final_store is not None else 0)
            self.task_lock = threading.Lock()
            self.commit_lock = threading.RLock()
            self.ingress_lock = threading.Lock()
            self._final_active = False
            self.tasks = []
            self.next_task_id = 0
            self.output_lock = threading.Lock()
            self.output_version = 0
            self.output = ("", "", False)

            self.sync_thread = threading.Thread(target=self._sync_loop,daemon=True,
                                                name="translation-sync")
            self.translate_thread = threading.Thread(target=self._translate_loop,daemon=True,
                                                     name="translation-dispatch")
            self.display_thread = threading.Thread(target=self._display_loop,daemon=True,
                                                   name="translation-display")
            self.sync_thread.start()
            self.translate_thread.start()
            self.display_thread.start()
            ACTIVE_SINKS.add(self)

        @classmethod
        def _extract_original_caption(cls, text):
            return extract_original_caption(text)

        def _observe(self,text):
            with self.state_lock:
                self.observed_text = text or ""

        def partial(self,text):
            # LiveCaptions provides cumulative text; Qwen3-ASR provides one segment.
            # Reconstruct the cumulative transcript on the TRANSLATION side only.
            with self.state_lock:
                self.observed_text = getattr(self,"finalized_text","") + (text or "")

        def final(self,text,a=None,b=None):
            # Preserve the existing sink API; unannotated Final means endpoint.
            return self.final_segment(text,a,b,None)

        def final_segment(self,text,a=None,b=None,reason=None):
            if not text or not text.strip():
                return
            # Preserve journal order without holding state_lock across SQLite.
            # The publisher still waits for durable acknowledgement before its
            # recovery WAV is discarded; UI/Partial threads never wait on disk.
            with self.ingress_lock:
                with self.state_lock:
                    if self.closed:
                        raise RuntimeError('translation sink is closed')
                    source=text.strip()
                    continuation=reason in ("maxlen", "turn_maxlen")
                    if not continuation and source[-1] not in PUNC_EOS:
                        source += "。"
                    store=getattr(self,"_final_store",None)
                if store is not None:
                    try:
                        pending=store.accept_translation_final(source,continuation)
                    except Exception as exc:
                        print('翻译 Final 本地保存失败：',repr(exc),flush=True)
                        raise
                with self.state_lock:
                    self.finalized_text=(getattr(self,"finalized_text","")+source)[-8192:]
                    self.observed_text=self.finalized_text
                    if store is None:
                        pending=getattr(self,"_pending_final_text","")+source
                        if continuation:
                            while len(pending)>=8192:
                                self.pending_text_queue.put((preprocess(pending[:8192]),True))
                                pending=pending[8192:]
                        elif pending:
                            self.pending_text_queue.put((preprocess(pending),True))
                            pending=""
                    self._pending_final_text=pending
                    if not continuation or not pending:
                        self._queued_final_caption=extract_original_caption(preprocess(self.finalized_text))

        def _sync_once(self):
            with self.state_lock:
                original = self.observed_text
                if not original:
                    return None
                full_text = preprocess(original)
                if not any(mark in full_text for mark in PUNC_EOS):
                    queue_ = getattr(self,"context_entries",None)
                    if queue_ is not None:
                        queue_.clear()
                latest = extract_original_caption(full_text)
                pending = None
                if latest != self.original_caption:
                    self.original_caption = latest
                    self.idle_count = 0
                    if latest and latest[-1] in self.PUNC_EOS:
                        self.sync_count = 0
                        pending = latest
                    elif len(latest.encode("utf-8")) >= self.SHORT_THRESHOLD:
                        self.sync_count += 1
                else:
                    self.idle_count += 1
                if (self.sync_count > getattr(self.settings, "max_sync_interval", self.MAX_SYNC_INTERVAL)
                        or self.idle_count == getattr(self.settings, "max_idle_interval", self.MAX_IDLE_INTERVAL)):
                    self.sync_count = 0
                    pending = self.original_caption
                # Direct Final delivery above already queued this exact caption.
                # Keep the return contract for cumulative-caption callers/tests.
                if pending and pending != getattr(self,"_queued_final_caption",None):
                    if getattr(self,"_final_store",None) is not None:
                        # Only Partial previews live here; old previews may be
                        # coalesced while every Final remains in SQLite.
                        while True:
                            try:self.pending_text_queue.get_nowait()
                            except queue.Empty:break
                    try:self.pending_text_queue.put_nowait(pending)
                    except queue.Full:pass  # another latest Partial already won
            return pending

        def _sync_loop(self):
            while not self.closed:
                self._sync_once()
                time.sleep(self.SYNC_TICK)

        def _translate_loop(self):
            while not self.closed:
                try:
                    self._translate_once()
                except Exception as exc:
                    # A failed SQLite read must not silently kill dispatch. Final
                    # text stays journaled and can be retried after the fault.
                    print('翻译调度失败，Final 已保留：',repr(exc),flush=True)
                    self._final_retry_after=time.monotonic()+1.
                time.sleep(self.TRANSLATE_TICK)

        def _translate_once(self):
            store=getattr(self,"_final_store",None)
            # Network work must never run in this dispatcher. One durable Final
            # is active at a time; its row stays in SQLite until committed.
            with self.task_lock:
                if self.closed or self._config_invalid or getattr(self,"_final_active",False):
                    return
                if store is not None and time.monotonic()<getattr(self,"_final_retry_after",0.):
                    return
                generation=getattr(self,"_settings_generation",0)
            try:
                row=store.pending_translation() if store is not None else None
            except Exception as exc:
                with self.task_lock:
                    self._final_retry_after=time.monotonic()+1.
                    with self.output_lock:
                        self.output=('', '[ERROR] 读取本地翻译队列失败，待译 Final 未删除', False)
                        self.output_version+=1
                print('翻译队列读取失败：',repr(exc),flush=True)
                return
            with self.task_lock:
                if (self.closed or self._config_invalid
                        or generation!=getattr(self,"_settings_generation",0)
                        or getattr(self,"_final_active",False)):
                    return
                pending_id=None
                if row:
                    pending_id,source=row
                    is_final=True
                else:
                    try:
                        source=self.pending_text_queue.get_nowait()
                    except queue.Empty:
                        source=""
                    is_final=isinstance(source,tuple)
                    if is_final:
                        source,is_final=source
                if source and self.settings.log_only:
                    try:
                        previous=self.history.last()
                        overwrite=bool(not is_final and previous
                            and not getattr(self,"_history_last_is_final",False)
                            and similar_to_last(source,previous.source))
                        if pending_id is not None:
                            committed=store.complete_translation(pending_id,"N/A","N/A","LogOnly")
                            if not committed:return
                        else:
                            self.history.log(source,"N/A","N/A","LogOnly",overwrite=overwrite)
                        self._history_last_is_final=bool(is_final)
                        self.overlay.translation(source,"[Paused]")
                    except Exception as exc:
                        if pending_id is not None:
                            self._final_retry_after=time.monotonic()+1.
                            self.overlay.translation(source,'[ERROR] Final 已保留本地，等待重试')
                        print("翻译历史保存失败：",repr(exc),flush=True)
                    return
            if source:
                self._enqueue_translation_task(source,final=is_final,pending_id=pending_id,
                                               generation=generation)

        def _enqueue_translation_task(self,source,final=False,pending_id=None,generation=None):
            with self.task_lock:
                if self.closed or (generation is not None and
                        generation!=getattr(self,"_settings_generation",0)):
                    return None
                if final and getattr(self,"_final_active",False):
                    return None
                durable_partial=not final and getattr(self,"_final_store",None) is not None
                if durable_partial and getattr(self,"_partial_active",False):
                    return None
                if durable_partial:
                    self._partial_active=True
                if final:
                    self._final_active=True
                self.next_task_id += 1
                task = {"id":self.next_task_id,"text":source,"cancelled":False,
                        "cancel_token":CancellationToken(),"final":final,"pending_id":pending_id}
                self.tasks.append(task)
            def run_task():
                try:
                    self._translation_worker(task)
                finally:
                    with self.task_lock:
                        if final:
                            self._final_active=False
                        elif durable_partial:
                            self._partial_active=False
                        if task in self.tasks:
                            self.tasks.remove(task)
            thread=threading.Thread(target=run_task,daemon=True,
                                    name=f"translation-{'final' if final else 'job'}-{task['id']}")
            task["thread"]=thread
            try:
                thread.start()
            except Exception:
                with self.task_lock:
                    if final:self._final_active=False
                    if durable_partial:self._partial_active=False
                    if task in self.tasks:self.tasks.remove(task)
                raise
            return task
        def _translation_worker(self,task):
            text = task["text"]
            choke = bool(text and text[-1] in PUNC_EOS)
            with self.state_lock:
                contexts = (list(self.context_entries)[-self.settings.num_contexts:]
                            if self.settings.context_aware and self.settings.num_contexts>0
                            else [])
                api=self.api
                settings=self.settings
            token=task.get("cancel_token")
            context=(api.cancellation(token) if isinstance(api,TranslationAPI)
                     else nullcontext())
            started = time.perf_counter() if getattr(settings, "show_latency", False) else None
            try:
                with context:
                    if settings.context_aware:
                        previous = self.history.previous_text(
                            settings.num_contexts,translated=False,entries=contexts)
                        raw = api.translate(f"{previous} 🔤 {text} 🔤",contexts)
                        if "[ERROR]" in raw or "[WARNING]" in raw:
                            translated = raw
                        else:
                            match = re.search(r"🔤\s*(.+?)\s*🔤",raw,re.DOTALL)
                            translated = match.group(1) if match else ""
                    else:
                        translated = api.translate(text,contexts).replace("🔤","")
                if started is not None:
                    translated = f"[{int((time.perf_counter() - started) * 1000):4d} ms] " + translated
            except TranslationCancelled:
                with self.task_lock:
                    if task in self.tasks:
                        self.tasks.remove(task)
                        if task.get('pending_id') is not None:
                            # Settings swap cancellation should not consume
                            # a retry; the next dispatcher can try this Final.
                            self._final_retry_after=time.monotonic()+1.
                            with self.output_lock:
                                self.output=(task['text'],'[ERROR] 翻译请求已取消，Final 已保留待重试',False)
                                self.output_version+=1
                            print('翻译 Final 请求已取消，已保留待重试',flush=True)
                return
            except Exception as error:
                translated = "[ERROR] Translation Failed: "+str(error)
            with self.task_lock:
                if task.get("cancelled") or task not in self.tasks or self.closed:
                    return
            self._on_task_completed(task,translated,choke)

        def _defer_pending_final(self, pending_id, reason):
            try:
                state=self._final_store.defer_translation(pending_id, reason)
            except Exception as exc:
                print('翻译重试状态持久化失败，原始 Final 仍在数据库中：',repr(exc),flush=True)
                self._final_retry_after=time.monotonic()+1.
                return
            if state == 'failed':
                print(f'⚠ 翻译 Final {pending_id} 连续失败 6 次，已隔离；'
                      '请在翻译设置中点击「重试失败任务」。',flush=True)
                notify=getattr(self.overlay,'status',None)
                if callable(notify):
                    notify('⚠ 有翻译任务连续失败，已隔离；可在翻译设置中重试。')

        def _on_task_completed(self,task,translated,choke):
            # Durable Final text already exists in SQLite before HTTP begins.
            # Publish a successful Chinese translation immediately, then commit
            # translation history outside the task/UI lock. If history commit
            # fails, the original pending Final survives for a bounded retry.
            with getattr(self,"commit_lock",nullcontext()):
                with self.task_lock:
                    if task.get("cancelled") or task not in self.tasks or self.closed:
                        return False
                    if (not task.get("final")
                            and any(other is not task and other.get("final")
                                    for other in self.tasks)):
                        self.tasks.remove(task)
                        return False
                    pending_id=task.get("pending_id")
                if pending_id is not None:
                    if not translated or '[ERROR]' in translated or '[WARNING]' in translated:
                        self._defer_pending_final(pending_id,translated or 'Empty translation')
                        with self.task_lock:
                            if task in self.tasks:
                                self.tasks.remove(task)
                            with self.output_lock:
                                self.output=(task['text'],translated or '[ERROR] Empty translation; Final 已保留',False)
                                self.output_version+=1
                        print('翻译 Final 未完成，已保留待重试：',translated,flush=True)
                        return False
                    # Do not make the screen wait for a disk transaction once the
                    # provider succeeded. Final's untranslated journal is durable.
                    with self.task_lock:
                        if task.get("cancelled") or task not in self.tasks or self.closed:
                            return False
                        index=self.tasks.index(task)
                        for older in self.tasks[:index]:
                            older["cancelled"]=True
                            token=older.get("cancel_token")
                            if token is not None:
                                token.cancel()
                        del self.tasks[:index+1]
                        self._history_last_is_final=True
                        with self.output_lock:
                            self.output=(task["text"],translated,choke)
                            self.output_version+=1
                    try:
                        previous=self.history.last()
                        committed=self._final_store.complete_translation(
                            pending_id,translated,self.settings.target,self.settings.engine)
                    except Exception as exc:
                        with self.task_lock:
                            self._final_retry_after=time.monotonic()+1.
                        # Keep the usable Chinese translation on-screen; the
                        # untranslated Final stays recoverable in SQLite.
                        print('翻译 Final 历史提交失败，原文保留待重试：',repr(exc),flush=True)
                        return False
                    if not committed:
                        return False
                    if previous:
                        with self.state_lock:
                            queue_=getattr(self,"context_entries",None)
                            if queue_ is not None:
                                queue_.append(previous)
                    return True

                try:
                    previous=self.history.last()
                except Exception as exc:
                    previous=None
                    print('翻译历史读取失败：',repr(exc),flush=True)
                overwrite=bool(previous and not getattr(self,"_history_last_is_final",False)
                               and similar_to_last(task["text"],previous.source))
                with self.task_lock:
                    if task.get("cancelled") or task not in self.tasks or self.closed:
                        return False
                    index=self.tasks.index(task)
                    for older in self.tasks[:index]:
                        older["cancelled"]=True
                        token=older.get("cancel_token")
                        if token is not None:
                            token.cancel()
                    del self.tasks[:index+1]
                    if not overwrite and previous:
                        with self.state_lock:
                            queue_=getattr(self,"context_entries",None)
                            if queue_ is not None:
                                queue_.append(previous)
                    self._history_last_is_final=False
                    with self.output_lock:
                        self.output=(task["text"],translated,choke)
                        self.output_version+=1
                try:
                    self.history.log(task["text"],translated,self.settings.target,
                                     self.settings.engine,overwrite=overwrite)
                except Exception as exc:
                    print("翻译历史保存失败：",repr(exc),flush=True)
                return True

        def _display_loop(self):
            seen=0
            next_error_log=0.0
            while not self.closed:
                if self.settings.log_only:
                    time.sleep(self.DISPLAY_TICK)
                    continue
                with self.output_lock:
                    version=self.output_version
                    result=self.output
                if version!=seen:
                    try:
                        source,translated,choke=result
                        with self.state_lock:
                            show=getattr(self.settings,"display_sentences",0)
                            previous=list(getattr(self,"context_entries",[]))[-show:] if show > 0 else []
                        if show>0 and "[ERROR]" not in translated and "[WARNING]" not in translated:
                            prefix=self.history.previous_text(show,entries=previous)
                            if prefix:
                                translated=prefix+translated
                        # A settings swap or a newer result invalidates this output.
                        with self.task_lock:
                            with self.output_lock:
                                current = version == self.output_version
                            if current and not self.closed and not self.settings.log_only:
                                self.overlay.translation(source,translated)
                                seen=version  # A failing UI/history call must remain retryable.
                    except Exception as exc:
                        # Retry the newest version, but throttle persistent failures
                        # so a locked/broken database cannot grow the log forever.
                        now=time.monotonic()
                        if now >= next_error_log:
                            print('翻译显示失败，将重试当前结果：',repr(exc),flush=True)
                            next_error_log=now+5.0
                time.sleep(self.DISPLAY_TICK)

        def clear_history(self):
            """Purge historical data without old asynchronous requests restoring it."""
            with self.ingress_lock, self.commit_lock, self.task_lock:
                with self.state_lock:
                    # Do not cancel existing tasks unless the database purge succeeds.
                    self.history.clear()
                    self._settings_generation += 1
                    self._final_retry_after = 0.
                    for task in self.tasks:
                        task["cancelled"] = True
                        token = task.get("cancel_token")
                        if token is not None:
                            token.cancel()
                    self.tasks.clear()
                    while True:
                        try:
                            self.pending_text_queue.get_nowait()
                        except queue.Empty:
                            break
                    self.observed_text = ""
                    self.finalized_text = ""
                    self.original_caption = ""
                    self._pending_final_text = ""
                    self._queued_final_caption = None
                    self._history_last_is_final = False
                    self.idle_count = self.sync_count = 0
                    self._partial_active = False
                    self.context_entries.clear()
                    with self.output_lock:
                        self.output = ("", "", False)
                        self.output_version += 1
            self.overlay.translation("", "")

        def update_settings(self,settings):
            """Apply configuration without leaking results from the old engine.

            Cancel active HTTP connections for obsolete translation tasks,
            without changing or restarting ASR/VAD/audio workers.
            """
            with self.commit_lock, self.task_lock:
                self._settings_generation=getattr(self,"_settings_generation",0)+1
                self._final_retry_after=0.
                for task in self.tasks:
                    task["cancelled"]=True
                    token=task.get("cancel_token")
                    if token is not None:
                        token.cancel()
                self.tasks.clear()
                pending=getattr(self,"pending_text_queue",None)
                if pending is not None:
                    while True:
                        try:
                            pending.get_nowait()
                        except queue.Empty:
                            break
                with self.state_lock:
                    changed=(self.settings.engine!=settings.engine or
                             self.settings.target!=settings.target)
                    self.settings=settings
                    self.target=settings.target
                    self._config_invalid = False  # Explicitly applied valid settings.
                    if isinstance(self.api,TranslationAPI):
                        # Do not mutate a request object another worker may be
                        # using. Rebuild it with the same transport hook.
                        self.api=TranslationAPI(settings,requester=self.api.requester)
                    else:
                        self.api.settings=settings
                    if changed:
                        if getattr(self,"_final_store",None) is None:
                            self._queued_final_caption = None
                        self.context_entries.clear()
                        if hasattr(self,"original_caption"):
                            self.original_caption=""
                        if hasattr(self,"idle_count"):
                            self.idle_count=0
                        if hasattr(self,"sync_count"):
                            self.sync_count=0
                with self.output_lock:
                    self.output=("", "", False)
                    self.output_version+=1
                overlay=getattr(self,"overlay",None)
                if overlay is not None:
                    overlay.translation("","[Paused]" if settings.log_only else "")

        def set_log_only(self,value):
            self.settings.log_only=bool(value)
            if value:
                self.overlay.translation("","[Paused]")

        def close(self):
            ACTIVE_SINKS.discard(self)
            with self.ingress_lock, self.commit_lock, self.task_lock:
                with self.state_lock:
                    if not self.closed:
                        store=getattr(self,"_final_store",None)
                        if store is not None:
                            try:
                                store.flush_translation_carry()
                                self._pending_final_text=''
                            except Exception as exc:
                                # Carry was persisted when accepted and remains
                                # recoverable even if this final flush cannot write.
                                print('翻译尾段暂未入队，已保留本地 carry：',repr(exc),flush=True)
                        self.closed=True
                for task in self.tasks:
                    task["cancelled"]=True
                    token=task.get("cancel_token")
                    if token is not None:
                        token.cancel()
                self.tasks.clear()
            for worker in (self.sync_thread,self.translate_thread,self.display_thread):
                if worker is not threading.current_thread():
                    worker.join(timeout=0.5)
    # <<< INLINE translation/engine.py END >>>

if False:  # Embedded translation/gui.py
    # <<< INLINE translation/gui.py BEGIN >>>
    """Tkinter translation settings for Google / Google2; existing history UI retained."""
    import json
    from dataclasses import asdict
    from pathlib import Path
    from .api import ENGINES, TranslationSettings
    from .history import HistoryStore

    ROOT = Path(__file__).resolve().parent.parent
    FIELDS = ("api_key",)
    PASSWORD = {"api_key"}
    LANGUAGES = ("zh-CN", "zh-TW", "en-US", "en-GB", "ja-JP", "ko-KR", "fr-FR",
                 "th-TH", "ru-RU", "es-ES", "pt-BR", "tr-TR", "ar-SA")


    def normalize_profile(values):
        return {key: value.strip() for key, value in values.items() if value.strip()}


    def open_settings(parent, config_path=None):
        import tkinter as tk
        from tkinter import ttk, messagebox
        path = Path(config_path or ROOT / "translation-settings.json")
        settings = TranslationSettings.from_file(path)
        win = tk.Toplevel(parent)
        win.title("翻译设置 | Google / Google2")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)
        engine = tk.StringVar(value=settings.engine)
        target = tk.StringVar(value=settings.target)
        context = tk.BooleanVar(value=settings.context_aware)
        log_only = tk.BooleanVar(value=settings.log_only)
        num_contexts = tk.IntVar(value=settings.num_contexts)
        show_history = tk.IntVar(value=settings.display_sentences)
        max_sync = tk.IntVar(value=settings.max_sync_interval)
        max_idle = tk.IntVar(value=settings.max_idle_interval)
        show_latency = tk.BooleanVar(value=settings.show_latency)
        api_key = tk.StringVar()
        profiles = {}
        for name in ENGINES:
            raw = settings.configs.get(name, {})
            profiles[name] = [dict(value) for value in raw] if isinstance(raw, list) else [dict(raw or {})]
            if not profiles[name]:
                profiles[name] = [{}]
        indices = {name: max(0, min(int(settings.config_indices.get(name, 0)),
                                    len(profiles[name])-1)) for name in ENGINES}
        selected = [settings.engine, indices[settings.engine]]
        profile_index = tk.StringVar()

        ttk.Label(body, text="翻译引擎").grid(row=0, column=0, sticky="w")
        engine_box = ttk.Combobox(body, textvariable=engine, values=ENGINES,
                                  state="readonly", width=28)
        engine_box.grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        ttk.Label(body, text="配置档案").grid(row=1, column=0, sticky="w")
        profile_row = ttk.Frame(body)
        profile_row.grid(row=1, column=1, sticky="w", padx=8, pady=3)
        profile_box = ttk.Combobox(profile_row, textvariable=profile_index,
                                   state="readonly", width=8)
        profile_box.pack(side="left")
        ttk.Label(body, text="目标语言").grid(row=2, column=0, sticky="w")
        ttk.Combobox(body, textvariable=target, values=LANGUAGES, width=28).grid(
            row=2, column=1, padx=8, pady=3)
        ttk.Checkbutton(body, text="启用翻译上下文", variable=context).grid(
            row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(body, text="仅记录模式（暂停翻译）", variable=log_only).grid(
            row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="上下文条数").grid(row=5, column=0, sticky="w")
        tk.Spinbox(body, from_=0, to=10, textvariable=num_contexts, width=8).grid(
            row=5, column=1, sticky="w", padx=8)
        ttk.Label(body, text="历史显示句数").grid(row=6, column=0, sticky="w")
        tk.Spinbox(body, from_=0, to=10, textvariable=show_history, width=8).grid(
            row=6, column=1, sticky="w", padx=8)
        api_key_label = ttk.Label(body, text="Google2 自定义 API Key（可选）")
        api_key_entry = ttk.Entry(body, textvariable=api_key, width=41, show="*")
        api_key_label.grid(row=7, column=0, sticky="w")
        api_key_entry.grid(row=7, column=1, padx=8, pady=4)
        ttk.Label(body, text="密钥支持 ${ENV_VAR} 环境变量引用。",
                  foreground="#666666").grid(row=8, column=0, columnspan=2, sticky="w")
        advanced = ttk.Frame(body)
        advanced.grid(row=9, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(advanced, text="同步间隔").pack(side="left")
        tk.Spinbox(advanced, from_=0, to=1000, textvariable=max_sync,
                   width=5).pack(side="left", padx=(3,9))
        ttk.Label(advanced, text="静止间隔").pack(side="left")
        tk.Spinbox(advanced, from_=0, to=1000, textvariable=max_idle,
                   width=5).pack(side="left", padx=(3,9))
        ttk.Checkbutton(advanced, text="显示翻译耗时", variable=show_latency).pack(side="left")

        def remember():
            name, index = selected
            profiles[name][index] = normalize_profile({'api_key': api_key.get()}) if name == 'Google2' else {}

        def display():
            name, index = selected
            profile_box['values'] = [str(i+1) for i in range(len(profiles[name]))]
            profile_index.set(str(index+1))
            if name == 'Google2':
                cfg = profiles[name][index]
                api_key.set(str(cfg.get('api_key', cfg.get('ApiKey', ''))))
                api_key_label.grid()
                api_key_entry.grid()
            else:
                api_key.set('')
                api_key_label.grid_remove()
                api_key_entry.grid_remove()

        def switch_engine(event=None):
            remember()
            selected[:] = [engine.get(), indices[engine.get()]]
            display()

        def switch_profile(event=None):
            remember()
            index = int(profile_index.get())-1
            indices[selected[0]] = index
            selected[1] = index
            display()

        def add_profile():
            remember()
            name = selected[0]
            profiles[name].append(dict(profiles[name][selected[1]]))
            selected[1] = len(profiles[name])-1
            indices[name] = selected[1]
            display()

        def delete_profile():
            name = selected[0]
            if len(profiles[name]) <= 1:
                messagebox.showinfo("至少保留一份", "每个引擎至少需要一份配置。", parent=win)
                return
            del profiles[name][selected[1]]
            selected[1] = min(selected[1], len(profiles[name])-1)
            indices[name] = selected[1]
            display()

        engine_box.bind('<<ComboboxSelected>>', switch_engine)
        profile_box.bind('<<ComboboxSelected>>', switch_profile)
        ttk.Button(profile_row, text="复制新增", command=add_profile).pack(side="left", padx=6)
        ttk.Button(profile_row, text="删除当前", command=delete_profile).pack(side="left")
        display()

        def retry_failed():
            try:
                count = HistoryStore(ROOT / "translation-history.sqlite3").retry_failed_translations()
                messagebox.showinfo("翻译任务重试", f"已重新排队 {count} 个失败任务。", parent=win)
            except Exception as exc:
                messagebox.showerror("翻译任务重试失败", str(exc), parent=win)
        ttk.Button(body, text="重试失败任务", command=retry_failed).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=4)

        def save():
            try:
                remember()
                settings.engine = engine.get()
                if settings.engine not in ENGINES:
                    raise ValueError("仅支持 Google 和 Google2")
                settings.target = target.get().strip() or "zh-CN"
                settings.context_aware = bool(context.get())
                settings.log_only = bool(log_only.get())
                settings.num_contexts = max(0, min(10, int(num_contexts.get())))
                settings.display_sentences = max(0, min(10, int(show_history.get())))
                settings.max_sync_interval = int(max_sync.get())
                settings.max_idle_interval = int(max_idle.get())
                if not 0 <= settings.max_sync_interval <= 1000:
                    raise ValueError("同步间隔应在 0..1000 之间")
                if not 0 <= settings.max_idle_interval <= 1000:
                    raise ValueError("静止间隔应在 0..1000 之间")
                settings.show_latency = bool(show_latency.get())
                settings.configs = {name: profiles[name] for name in ENGINES}
                settings.config_indices = {name: indices[name] for name in ENGINES}
                content = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix('.json.tmp')
                tmp.write_text(content+'\n', encoding='utf-8')
                tmp.replace(path)
                from .engine import ACTIVE_SINKS
                for sink in tuple(ACTIVE_SINKS):
                    sink.update_settings(settings)
            except (ValueError, OSError, TypeError) as exc:
                messagebox.showerror("保存失败", str(exc), parent=win)
                return
            win.destroy()
        ttk.Button(body, text="保存", command=save).grid(row=11, column=0, sticky="e", pady=5)
        ttk.Button(body, text="取消", command=win.destroy).grid(row=11, column=1, sticky="e", pady=5)
        ttk.Label(body, text="Google / Google2 属于联网翻译；启用后字幕原文会发送给 Google。",
                  foreground="#666666", wraplength=440).grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(5,0))
        return win


    def open_history(parent,history_path=None):
        """Full upstream history features: search, pagination, clear, export all."""
        import tkinter as tk
        from tkinter import ttk,filedialog,messagebox
        store=HistoryStore(history_path or ROOT/"translation-history.sqlite3")
        win=tk.Toplevel(parent)
        win.title("翻译历史")
        win.geometry("1000x500")
        win.attributes("-topmost",True)
        controls=ttk.Frame(win,padding=8)
        controls.pack(fill="x")
        query=tk.StringVar()
        page=[1]
        size=50
        ttk.Label(controls,text="检索原文/译文").pack(side="left")
        input_=ttk.Entry(controls,textvariable=query,width=28)
        input_.pack(side="left",padx=8)
        page_label=ttk.Label(controls,text="1 / 1")
        page_label.pack(side="right",padx=6)
        cols=("time","source","translation","language","engine")
        table=ttk.Treeview(win,columns=cols,show="headings")
        for key,label,width in zip(cols,("时间","原文","译文","目标语言","引擎"),
                                   (155,250,360,85,110)):
            table.heading(key,text=label)
            table.column(key,width=width,anchor="w")
        table.pack(fill="both",expand=True,padx=8,pady=3)
        max_page=[1]

        def refresh():
            rows,max_page[0]=store.search(query.get(),page[0],size)
            if page[0]>max_page[0]:
                page[0]=max_page[0]
                rows,_=store.search(query.get(),page[0],size)
            for item in table.get_children():
                table.delete(item)
            for entry in rows:
                table.insert("","end",values=(entry.timestamp,entry.source,
                     entry.translated,entry.target,entry.engine))
            page_label["text"]=f"{page[0]} / {max_page[0]} （共 {store.count(query.get())} 条）"

        def find(event=None):
            page[0]=1
            refresh()

        def move(delta):
            page[0]=max(1,min(max_page[0],page[0]+delta))
            refresh()

        def erase():
            if not messagebox.askyesno("清空翻译历史",
                    "确认永久清空翻译历史、待译字幕及未完成的翻译请求吗？",parent=win):
                return
            try:
                from .engine import ACTIVE_SINKS
                active = [sink for sink in tuple(ACTIVE_SINKS)
                          if Path(sink.history.path).resolve() == Path(store.path).resolve()]
                if active:
                    for sink in active:
                        sink.clear_history()
                else:
                    store.clear()
            except (OSError,ValueError,RuntimeError) as exc:
                messagebox.showerror("清空失败",str(exc),parent=win)
                return
            page[0]=1
            refresh()

        def export():
            filename=filedialog.asksaveasfilename(parent=win,title="导出全部翻译历史",
                     defaultextension=".csv",filetypes=[("CSV","*.csv")])
            if not filename:
                return
            try:
                count=store.export_csv(filename)
                messagebox.showinfo("导出完成",f"已导出 {count} 条记录",parent=win)
            except OSError as exc:
                messagebox.showerror("导出失败",str(exc),parent=win)

        ttk.Button(controls,text="搜索",command=find).pack(side="left")
        input_.bind("<Return>",find)
        buttons=ttk.Frame(win,padding=8)
        buttons.pack(fill="x")
        ttk.Button(buttons,text="上一页",command=lambda:move(-1)).pack(side="left")
        ttk.Button(buttons,text="下一页",command=lambda:move(1)).pack(side="left",padx=6)
        ttk.Button(buttons,text="刷新",command=refresh).pack(side="left",padx=6)
        ttk.Button(buttons,text="导出全部 CSV",command=export).pack(side="left",padx=6)
        ttk.Button(buttons,text="清空历史",command=erase).pack(side="left",padx=6)
        ttk.Button(buttons,text="关闭",command=win.destroy).pack(side="right")
        refresh()
        return win
    # <<< INLINE translation/gui.py END >>>

if False:  # Embedded translation/__init__.py
    # <<< INLINE translation/__init__.py BEGIN >>>
    """Google / Google2 translation from SakiRinn/LiveCaptions-Translator.

    Reference: upstream master commit a6fee12757b15edbeef7c60f8895e0be694801e1.
    ASR remains separate.
    """
    from .api import TranslationAPI, TranslationSettings, google_translate
    from .engine import GoogleTranslateSink
    from .history import HistoryStore, TranslationEntry
    __all__ = ["TranslationAPI","TranslationSettings","google_translate",
               "GoogleTranslateSink","HistoryStore","TranslationEntry"]
    # <<< INLINE translation/__init__.py END >>>

