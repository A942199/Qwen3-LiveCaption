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
    _lc_pythonw = _LC_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not _lc_pythonw.is_file():
        try:
            import ctypes as _lc_ctypes
            _lc_ctypes.windll.user32.MessageBoxW(
                0,
                "Project virtual environment not found:\n" + str(_lc_pythonw) + "\n\nRun setup first.",
                "live-caption",
                0x10,
            )
        finally:
            raise SystemExit(1)
    _lc_env = dict(_lc_os.environ)
    _lc_env["_LIVE_CAPTION_PYW_BOOTSTRAPPED"] = "1"
    _lc_os.execve(
        str(_lc_pythonw),
        [str(_lc_pythonw), str(_LC_ROOT / _LCPath(__file__).name), *_lc_sys.argv[1:]],
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


def start_capture(pa, dev, audio_q, stop, overflowed=None):
    ch = int(dev["maxInputChannels"])
    sr = int(dev["defaultSampleRate"])
    rs = Resampler(sr)

    def cb(in_data, frame_count, time_info, status):
        if stop.is_set():
            return (None, pyaudio.paComplete)
        try:
            x = np.frombuffer(in_data, dtype=np.float32)
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            # Never block a PortAudio callback: report overrun rather than silently
            # losing audio or allowing the capture queue to grow indefinitely.
            audio_q.put_nowait(rs(x.astype(np.float32)))
        except Exception:
            if overflowed is not None:
                overflowed.set()
            stop.set()
            return (None, pyaudio.paComplete)
        if status:
            # PortAudio has already detected a device-side discontinuity.
            # Retain this callback's samples, then fail visibly and drain.
            if overflowed is not None:
                overflowed.set()
            stop.set()
            return (None, pyaudio.paComplete)
        return (None, pyaudio.paContinue)

    stream = pa.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True,
                     input_device_index=dev["index"], frames_per_buffer=int(sr * 0.1), stream_callback=cb)
    try:
        stream.start_stream()
    except BaseException:
        stream.close()
        raise
    # The consumer flushes this state only after callbacks have stopped.
    stream._lc_resampler = rs
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
                self.n_srt += 1
                self.srt.write(f"{self.n_srt}\n{fmt_ts(self.srt_offset + t_start - self.t0)} --> {fmt_ts(self.srt_offset + t_end - self.t0)}\n{text}\n\n")
                self.srt.flush()
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
    MAX_UI_EVENTS = 256
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
            "silence": 0.5,
            "vad_win": 0.2,
            "vad_threshold": 0.45,
            "vad_min_speech_ms": 100,
            "vad_min_silence_ms": 200,
            "vad_speech_pad_ms": 100,
            "min_seg": 1.0,
            "max_seg": 6.0,
            "max_gain": 60.0,
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
        self.asr_language = language_value if language_value in ("auto", "ja") else "ja"
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
            self.asr_language = value if value in ("auto", "ja") else "ja"
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
        }
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
                    "max_gain", "font_name", "font_size", "alpha", "width_frac", "bottom", "hold",
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
        self._settings_changed("auto_hide")
        self._settings_changed("bottom")

    def _apply_accuracy_preset(self):
        if not self._setting_vars:
            return
        preset = {
            "asr_model": "Q8_0",
            "asr_language": "ja",
            "context_enabled": True,
            "context_history": 1,
            "context_chars": 80,
            "llama_ctx": 8192,
            "slots": 2,
            "max_tokens": 800,
            "temperature": 0.0,
            "partial_every": 0.4,
            "silence": 0.50,
            "vad_win": 0.20,
            "vad_threshold": 0.3,
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
        if self.asr_settings_win is not None:
            try:
                self.asr_settings_win.destroy()
            except Exception:
                pass
        self.asr_settings_win = None

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

        def add_field(parent, row, col, label, key, start, end, increment):
            self.tk.Label(parent, text=label).grid(row=row, column=col * 2, sticky="w", padx=(0, 6), pady=4)
            box = self.tk.Spinbox(parent, from_=start, to=end, increment=increment, textvariable=v[key],
                                  width=9, command=lambda k=key: self._settings_changed(k))
            box.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 14), pady=4)
            box.bind("<Return>", lambda e, k=key: self._settings_changed(k))
            box.bind("<FocusOut>", lambda e, k=key: self._settings_changed(k))

        context_frame = ttk.LabelFrame(outer, text="上下文 / llama")
        context_frame.pack(fill="x", pady=(0, 8))
        self.tk.Checkbutton(context_frame, text="启用前文上下文", variable=v["context_enabled"],
                            command=lambda: self._settings_changed("context_enabled")).grid(
                                row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 2))
        self.tk.Label(context_frame, text="设置保存后下次启动生效；显式命令行参数优先。",
                      fg="#666666").grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=4)
        add_field(context_frame, 2, 0, "llama ctx 总长度", "llama_ctx", 2048, 16384, 1024)
        add_field(context_frame, 2, 1, "并行 slots", "slots", 1, 4, 1)
        add_field(context_frame, 3, 0, "Max tokens", "max_tokens", 64, 1024, 32)
        add_field(context_frame, 3, 1, "Temperature", "temperature", 0.0, 1.0, 0.1)
        self.tk.Label(context_frame, text="识别语言 (auto/ja)").grid(row=4, column=0, sticky="w", padx=8, pady=(2, 6))
        lang_box = ttk.Combobox(context_frame, textvariable=v["asr_language"],
                                 values=("auto", "ja"), width=12, state="readonly")
        lang_box.grid(row=4, column=1, sticky="w", pady=(2, 6))
        lang_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed("asr_language"))

        vad_frame = ttk.LabelFrame(outer, text="VAD")
        vad_frame.pack(fill="x", pady=(0, 8))
        add_field(vad_frame, 0, 0, "检测步长 (s)", "vad_win", 0.1, 1.0, 0.05)
        add_field(vad_frame, 0, 1, "语音阈值", "vad_threshold", 0.1, 0.9, 0.05)
        add_field(vad_frame, 1, 0, "最短语音 (ms)", "vad_min_speech_ms", 50, 1000, 50)
        add_field(vad_frame, 1, 1, "最短静音 (ms)", "vad_min_silence_ms", 50, 2000, 50)
        add_field(vad_frame, 2, 0, "语音边界补偿 (ms)", "vad_speech_pad_ms", 0, 1000, 50)
        self.tk.Label(vad_frame, text="连续音频窗口检测；补偿音频会送入 ASR，设置重启后生效。",
                      fg="#666666").grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=4)

        segment_frame = ttk.LabelFrame(outer, text="切句 / 实时性")
        segment_frame.pack(fill="x", pady=(0, 8))
        add_field(segment_frame, 0, 0, "partial 刷新 (s)", "partial_every", 0.2, 2.0, 0.05)
        add_field(segment_frame, 1, 0, "静音断句 (s)", "silence", 0.2, 2.0, 0.05)
        add_field(segment_frame, 2, 1, "最短句段 (s)", "min_seg", 0.2, 5.0, 0.1)
        add_field(segment_frame, 3, 0, "最长句段 (s，0=不强切)", "max_seg", 0.0, 30.0, 0.5)
        add_field(segment_frame, 3, 1, "自动增益上限", "max_gain", 1.0, 100.0, 1.0)

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
            if kind == "p":
                # Only previews are dropped. Durable Finals are written by ConsoleSink.
                return
        # Keep critical UI updates responsive even if the GUI is temporarily stalled.
        try:
            self.q.get_nowait()
        except queue.Empty:
            pass
        try:
            self.q.put_nowait((kind, text))
        except queue.Full:
            pass  # Concurrent producers may refill the queue.

    def partial(self, text):
        self._post_ui("p", text)

    def final(self, text, t_start=None, t_end=None):
        self._post_ui("f", text)

    def translation(self, source, translated):
        self._post_ui("t", (source, translated))

    def status(self, text):
        self._post_ui("s", text)

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
# The translation/ source folder is not required. ASR code is unchanged.


class MultiSink:
    def __init__(self, *sinks):
        self.sinks = sinks

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
def _add_nvidia_dlls():
    base = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    if not os.path.isdir(base):
        return
    for sub in os.listdir(base):
        b = os.path.join(base, sub, "bin")
        if os.path.isdir(b):
            os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(b)


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
    """Bounded, overlapping audio VAD, with sample-accurate padded timestamps.

    `vad_win` is the *evaluation interval*, not the amount of context fed to
    Silero. Every evaluation sees enough history to enforce min_speech and
    min_silence across chunks. No ONNX internal state/API assumptions are used.
    """

    def __init__(self, threshold, min_speech_ms, min_silence_ms, pad_ms,
                 detect=None, options_class=None):
        if detect is None or options_class is None:
            from faster_whisper.vad import get_speech_timestamps, VadOptions, get_vad_model
            if detect is None:
                get_vad_model()  # Fail before opening capture when ONNX cannot initialize.
            detect = detect or get_speech_timestamps
            options_class = options_class or VadOptions
        self._detect = detect
        self.opts = options_class(threshold=threshold,
                               # Silero's default threshold-.15 floors at .01
                               # for low thresholds: .05/.08 background then
                               # never ends a .10 speech segment. At that range
                               # use the configured speech threshold for EOS.
                               neg_threshold=threshold if threshold <= .15 else None,
                               min_speech_duration_ms=min_speech_ms,
                               min_silence_duration_ms=min_silence_ms,
                               speech_pad_ms=pad_ms)
        self.pad_samples = int(SR * pad_ms / 1000)
        # Allow the longest configured silence and speech durations plus pad.
        # Retain enough context for configured speech/silence/pad, without
        # replaying 3 s of VAD input every 100 ms under default settings.
        # This is still overlapping VAD, NOT a stateful ONNX streaming decoder.
        horizon = max(1.1, (min_speech_ms + min_silence_ms + 2 * pad_ms) / 1000 + 0.65)
        self.history_limit = int(SR * horizon)
        self.recent = np.empty(0, dtype=np.float32)
        self.total_samples = 0
        self.last_spans = []
        self.last_speech_end = 0

    def feed(self, audio):
        self.total_samples += len(audio)
        self.recent = np.concatenate((self.recent, audio))[-self.history_limit:]
        offset = self.total_samples - len(self.recent)
        if np.max(np.abs(self.recent), initial=0) <= 1e-3:
            self.last_spans = []
            return False
        local = self._detect(self.recent, self.opts)
        self.last_spans = [{"start": offset + int(span["start"]),
                            "end": offset + int(span["end"])} for span in local]
        if self.last_spans:
            end = self.last_spans[-1]['end']
            self.last_speech_end = max(self.last_spans[-1]['start'],
                                      end-self.pad_samples if end<self.total_samples else end)
        # Padding is included in the timestamp; the subsequent speech-boundary
        # algorithm separately waits for user silence seconds before finalizing.
        return bool(self.last_spans and
                    self.last_spans[-1]["end"] >= self.total_samples - self.pad_samples - SR // 20)

    def slice_recent(self, absolute_start):
        """Return retained audio starting at the requested absolute sample."""
        oldest = self.total_samples - len(self.recent)
        return self.recent[max(0, absolute_start - oldest):].copy()

    def finish(self):
        """Resolve pending VAD EOS with analysis-only silence, never captured audio."""
        if not len(self.recent):
            return
        padding = int(SR*self.opts.min_silence_duration_ms/1000)+self.pad_samples+1024
        analysis = np.concatenate((self.recent, np.zeros(padding, dtype=np.float32)))
        offset = self.total_samples-len(self.recent)
        local = self._detect(analysis, self.opts)
        valid = [span for span in local if offset+int(span['start'])<self.total_samples]
        if valid:
            last = valid[-1]
            self.last_speech_end = max(offset+int(last['start']),
                min(self.total_samples, offset+int(last['end'])-self.pad_samples))


# ============================ 识别循环 ============================
def _is_retryable_asr_error(exc):
    """Retry temporary transport failures, not malformed requests/model errors."""
    from urllib.error import HTTPError, URLError
    if isinstance(exc, HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (TimeoutError, ConnectionError, URLError, OSError))


def _write_recovery_audio(jobs, directory):
    """Write unfinished Final audio after a forced shutdown; never silently drop it."""
    import hashlib
    import tempfile
    import wave
    from pathlib import Path
    folder = Path(directory) / "asr_recovery"
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


def _clean(text):
    # Repetition alone is not evidence of hallucination (laughter, names,
    # acknowledgements and numbers can all repeat legitimately).
    text = text.strip()
    text = re.sub(r"\b([A-Za-z0-9])((?: [A-Za-z0-9])+)\b", lambda m: m.group(0).replace(" ", ""), text)  # "B L G" -> "BLG"
    text = re.sub(r"(?<=[一-鿿]) (?=[A-Za-z0-9])|(?<=[A-Za-z0-9]) (?=[一-鿿])", "", text)  # 中英之间去空格
    return text


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
        spooled_jobs = []   # Overflow during shutdown, persisted after capture closes.
        shutdown_abandoned = [False]
        partial_stopping = [False]
        partial_generation = [0]

        def log_dbg(job, text, t0, t1, reused=False):
            if dbg is None:
                return
            rec = {"seg": job["seg"], "kind": job["kind"], "reason": job.get("reason"),
                   "reused": reused, "audio_sec": round(len(job["audio"]) / SR, 2),
                   "speech_end": round(job["t_end"] - dbg.t0, 2),
                   "queued": round(job["t_q"] - dbg.t0, 2),
                   "infer_start": round(t0 - dbg.t0, 2), "infer_sec": round(t1 - t0, 2),
                   "latency": round(t1 - job["t_end"], 2), "text": text}
            dbg.write(rec, job["audio"] if job["kind"] == "final" else None)

        def worker_final():
            while True:
                with cv:
                    while not final_q and not final_closed[0] and not shutdown_abandoned[0]:
                        cv.wait()
                    if shutdown_abandoned[0] or not final_q:
                        return
                    job = final_q.popleft()
                    final_inflight[0] = job
                    cv.notify_all()  # release bounded producer backpressure
                t0 = time.time()
                reused = job["text"] is not None
                text = ""
                failed = False
                try:
                    for attempt in range(3):
                        try:
                            text = be.transcribe(job["audio"])
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
                            except Exception as e:
                                print(f"最终字幕输出失败 seg={job['seg']}: {e!r}", flush=True)
                                try:
                                    _write_recovery_audio([job], here)
                                except Exception as audio_error:
                                    print(f"恢复音频保存失败: {audio_error!r}", flush=True)
                    if not abandoned:
                        if failed:
                            # Even permanent model errors should not silently erase
                            # the only copy of captured speech. Recovery WAVs are
                            # local and may contain sensitive audio; user can delete.
                            try:
                                saved = _write_recovery_audio([job], here)
                                print(f"识别失败音频已保存: {saved[0]}", flush=True)
                                if status:
                                    status("⚠ 识别失败，音频已保留；请查看日志")
                            except Exception as save_error:
                                print(f"识别失败且无法保存恢复音频: {save_error!r}", flush=True)
                                if status:
                                    status("⚠ 识别失败且音频保存失败；请检查磁盘与日志")
                        try:
                            log_dbg(job, text, t0, t1, reused)
                        except Exception as e:
                            print(f"ASR 调试日志写入失败 seg={job['seg']}: {e!r}", flush=True)
                finally:
                    with cv:
                        final_inflight[0] = None
                        cv.notify_all()

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
                        if partial_stopping[0] or job["seg"] <= retired_upto[0]:
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
                   threading.Thread(target=worker_partial, name="asr-partial", daemon=True)]
        for th in threads:
            th.start()

        def submit(kind, audio, seg, t_start, t_end, reason=None, text=None):
            with cv:
                job = {"kind": kind, "audio": audio.copy(), "seg": seg,
                       "t_start": t_start, "t_end": t_end, "t_q": time.time(),
                       "reason": reason, "text": text}
                if kind == "final":
                    retired_upto[0] = max(retired_upto[0], seg)
                    partial_generation[0] += 1
                    if partial_slot[0] is not None and partial_slot[0]["seg"] == seg:
                        partial_slot[0] = None
                    partial_done.pop(seg, None)
                    # Bound queued Final audio without ever silently discarding it.
                    # This blocks only the consumer thread, never PortAudio's callback.
                    while len(final_q) >= 16 and not shutdown_abandoned[0]:
                        if stop.is_set():
                            # On exit, never wait behind 16 long-running requests;
                            # persist original audio instead of silently dropping it.
                            spooled_jobs.append(job)
                            return
                        cv.wait(timeout=0.2)
                    if shutdown_abandoned[0]:
                        spooled_jobs.append(job)
                        return
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
        audio_q = queue.Queue(maxsize=128)
        overflowed = threading.Event()
        stream = None
        frame_buf = np.empty(0, dtype=np.float32)
        win_samples = max(1, int(SR * args.vad_win))
        seg_buf = np.empty(0, dtype=np.float32)
        seg_start_sample = None
        last_speech_sample = None
        speech_samples = 0
        previous_cutoff = 0
        seg_id = 0
        run_peak = 0.0
        last_partial_sample = 0
        start_wall = time.time()
        t_run0 = start_wall

        def stamp(sample):
            return start_wall + sample / SR

        def consume(window):
            nonlocal seg_buf, seg_start_sample, last_speech_sample, speech_samples
            nonlocal previous_cutoff, seg_id, last_partial_sample
            is_active = detector.feed(window)
            pos = detector.total_samples
            spans = [sp for sp in detector.last_spans if sp["end"] > previous_cutoff]
            if seg_start_sample is None:
                if not spans:
                    return
                # Recover audio before the first qualified speech event. The
                # timestamp from VAD already includes the requested speech pad.
                seg_start_sample = max(previous_cutoff, spans[0]["start"])
                seg_buf = detector.slice_recent(seg_start_sample)
                last_speech_sample = max(seg_start_sample, spans[-1]["end"])
                speech_samples = max(0, last_speech_sample - seg_start_sample)
                last_partial_sample = pos
            else:
                seg_buf = np.concatenate((seg_buf, window))
                if spans:
                    last_speech_sample = max(last_speech_sample, spans[-1]["end"])
                    speech_samples = max(speech_samples, last_speech_sample - seg_start_sample)
            seg_len = len(seg_buf) / SR
            sil = max(0, pos - last_speech_sample) / SR
            reason = None
            if (sil >= args.silence and seg_len >= args.min_seg) or sil >= args.silence * 2.5:
                reason = "silence"
            elif seg_len >= args.max_seg:
                reason = "maxlen"
            if reason:
                submit("final", seg_buf, seg_id, stamp(seg_start_sample),
                       stamp(max(seg_start_sample, last_speech_sample)), reason)
                previous_cutoff = pos
                seg_id += 1
                seg_buf = np.empty(0, dtype=np.float32)
                seg_start_sample = last_speech_sample = None
            elif (not args.no_partial and
                  (pos - last_partial_sample) / SR >= args.partial_every and seg_len >= 1.0):
                last_partial_sample = pos
                submit("partial", seg_buf, seg_id, stamp(seg_start_sample),
                       stamp(max(seg_start_sample, last_speech_sample)))

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
            stream = start_capture(pa, dev, audio_q, stop, overflowed)
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
                    continue
                process_chunk(chunk)
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
                    resampler = getattr(stream, "_lc_resampler", None)
                    if resampler is not None:
                        resampler_tail = resampler.flush()
                except Exception as exc:
                    cleanup_errors.append(exc)
            try:
                while True:
                    try:
                        chunk = audio_q.get_nowait()
                    except queue.Empty:
                        break
                    process_chunk(chunk)
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
                buffers = [seg_buf, frame_buf]
                while True:
                    try:
                        buffers.append(audio_q.get_nowait())
                    except queue.Empty:
                        break
                if len(resampler_tail):
                    buffers.append(resampler_tail)
                raw = np.concatenate(buffers)
                if len(raw):
                    spooled_jobs.append(dict(kind="final", audio=raw, seg=seg_id,
                                             t_q=time.time(), t_start=stamp(0),
                                             t_end=stamp(detector.total_samples),
                                             reason="processing_error", text=None))
                seg_buf = np.empty(0, dtype=np.float32)
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
                if len(seg_buf):
                    # VAD has already qualified the speech. Do not discard a
                    # short terminal word merely because it lasts under 0.5s.
                    submit("final", seg_buf, seg_id, stamp(seg_start_sample),
                           stamp(max(seg_start_sample, last_speech_sample)), "shutdown")
            with cv:
                final_closed[0] = True
                cv.notify_all()
            if spooled_jobs:
                try:
                    saved = _write_recovery_audio(spooled_jobs, here)
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
                        saved = _write_recovery_audio(pending, here)
                        print(f"⚠ 超时退出，已保存 {len(saved)} 段未完成的 Final 音频至 asr_recovery/，请妥善保管。", flush=True)
                    except Exception as e:
                        print(f"⚠ 超时退出，未能保存 {len(pending)} 段 Final 音频：{e!r}", flush=True)
                print("⚠ ASR 线程超时；已停止等待。未完成任务无法保证输出字幕。", flush=True)
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

    def transcribe(self, audio, _startup=False):
        import json
        ctx = self.hot
        if self.use_context and self.last:
            ctx = (ctx + "\n" + self.last[-getattr(self, "_lc_context_chars", 80):]).strip()
        msgs = [{"role": "system", "content": ctx},
                {"role": "user", "content": [{"type": "input_audio",
                                              "input_audio": {"data": self._wav_b64(audio), "format": "wav"}}]}]
        options = {"messages": msgs,
                   "max_tokens": getattr(self, "_lc_max_tokens", _LC_RUNTIME_MAX_TOKENS),
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
            raise RuntimeError("ASR token output truncated; increase max_tokens or reduce max_seg")
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
        # Preserve the existing GUI/CLI meaning: 0 disables timed segmentation.
        # The unchanged upstream loop otherwise cuts every speech window at 0.
        args.max_seg = float("inf")
    elif 0 < getattr(args, "max_seg", 30) < getattr(args, "min_seg", 0):
        args.max_seg = args.min_seg
    if not is_explicit("--no-context") and "context_enabled" in settings:
        setting = settings["context_enabled"]
        args.context = setting.strip().lower() not in ("", "0", "false", "no", "off") if isinstance(setting, str) else bool(setting)
    if not is_explicit("--lang") and hasattr(args, "lang") and "asr_language" in settings:
        language = str(settings["asr_language"]).strip().lower()
        if language in ("auto", "ja"):
            args.lang = None if language == "auto" else "ja"
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
              ("silence", getattr(args, "silence", .5), .2, 2.0),
              ("vad_win", getattr(args, "vad_win", .2), .1, 1.0),
              ("min_seg", getattr(args, "min_seg", 1.), .2, 5.0),
              ("max_seg", getattr(args, "max_seg", 6.), 0, 30.0),
              ("max_gain", getattr(args, "max_gain", 60.), 1., 100.)]
    for name, value, low, high in checks:
        if name == "max_seg" and value == float("inf"):
            continue
        if not np.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid ASR {name}: {value!r} (range {low}..{high})")
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
    ap.add_argument("--silence", type=float, default=0.5, help="静音多少秒算一句结束")
    ap.add_argument("--vad-win", type=float, default=0.2, help="VAD 判定窗口秒数（越小句尾反应越快）")
    ap.add_argument("--vad-threshold", type=float, default=0.45, help="Silero VAD 语音阈值")
    ap.add_argument("--vad-min-speech-ms", type=int, default=100, help="VAD 最短语音毫秒")
    ap.add_argument("--vad-min-silence-ms", type=int, default=200, help="VAD 最短静音毫秒")
    ap.add_argument("--vad-speech-pad-ms", type=int, default=100, help="VAD 语音前后 padding 毫秒")
    ap.add_argument("--slots", type=int, default=2, help="llama-server 并行槽位数")
    ap.add_argument("--min-seg", type=float, default=1.0, help="短于这个秒数的句子先不切，等下一段一起")
    ap.add_argument("--max-seg", type=float, default=6.0, help="最长强制切段秒数；设为 0 禁用时长硬切，仅静音或停止时 Final（长句可能增加延迟）")
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

    stop = threading.Event()
    console = overlay = translator = th = None
    try:
        console = ConsoleSink(args.out, args.srt, show_partial=args.console and not args.no_partial)

        def cli_explicit(option):
            return any(a == option or a.startswith(option + "=") for a in _LC_ORIGINAL_ARGS)

        here = os.path.dirname(os.path.abspath(__file__))
        if args.console:
            settings_path = os.path.join(here, "live-caption-settings.json")
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
            except (OSError, ValueError, TypeError):
                saved = {}
            _apply_asr_config(args, saved, cli_explicit, here)
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


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        _lc_report_fatal_error(traceback.format_exc())

# Exact translation/*.py Python code below (no ASR changes).
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
    THINK = re.compile(r"<think>.*?</think>")


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
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_text TEXT NOT NULL)""")
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
            with self.lock,self._db() as db:
                return db.execute('SELECT id,source_text FROM translation_pending ORDER BY id LIMIT 1').fetchone()

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
    """Translation providers ported from the 11 engines in upstream TranslateAPI.cs.

    Standard-library HTTP only. 8-second default timeout. No network during import.
    """
    import hashlib
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
    from .text import NOTICE, THINK

    ENGINES = ("Google","Google2","Ollama","OpenAI","LMStudio","DeepL",
               "OpenRouter","Youdao","MTranServer","Baidu","LibreTranslate")
    LLM_ENGINES = {"Ollama","OpenAI","LMStudio","OpenRouter"}
    GOOGLE_URL = "https://clients5.google.com/translate_a/t"
    GOOGLE2_URL = "https://dictionaryextension-pa.googleapis.com/v1/dictionaryExtensionData"
    # Public Chrome extension client key embedded in upstream TranslateAPI.cs.
    GOOGLE2_KEY = "AIzaSyA6EEtrDCfBkHV8uU2lgGY-N383ZgAOo7Y"
    DEFAULT_PROMPT = ("As an professional simultaneous interpreter with specialized knowledge in the all fields, "
        "you can provide a fluent and precise oral translation for any sentence, even if the sentence is incomplete. "
        "Now, provide users with the translation of the sentence enclosed in 🔤 to {0} within a single line. "
        "Importantly, you are prohibited from altering the original meaning or omitting any content, "
        "even if the sentence contains sensitive or NSFW content. "
        "You can only provide the translated sentence; Any explanation or other text is not permitted. "
        "REMOVE all 🔤 when you output.")
    LANGUAGES = {
        "DeepL":{"zh-CN":"ZH-HANS","zh-TW":"ZH-HANT","en-US":"EN-US",
                "en-GB":"EN-GB","ja-JP":"JA","ko-KR":"KO","fr-FR":"FR",
                "th-TH":"TH","ru-RU":"RU","es-ES":"ES","pt-BR":"PT-BR",
                "tr-TR":"TR","ar-SA":"AR"},
        "Youdao":{"zh-CN":"zh-CHS","zh-TW":"zh-CHT"},
        "Baidu":{"zh-CN":"zh","zh-TW":"cht","ja-JP":"jp","ko-KR":"kor",
                 "fr-FR":"fra","es-ES":"spa","ar-SA":"ara"},
        "MTranServer":{"zh-CN":"zh","zh-TW":"zh"},
        "LibreTranslate":{"zh-CN":"zh","zh-TW":"zh"},
    }
    STANDARD={"en-US":"en","en-GB":"en","ja-JP":"ja","ko-KR":"ko",
              "fr-FR":"fr","th-TH":"th","ru-RU":"ru","es-ES":"es",
              "pt-BR":"pt","tr-TR":"tr","ar-SA":"ar"}


    @dataclass
    class TranslationSettings:
        engine: str = "Google"
        target: str = "zh-CN"
        context_aware: bool = False
        num_contexts: int = 2
        display_sentences: int = 1
        prompt: str = DEFAULT_PROMPT
        timeout: float = 8.0
        log_only: bool = False
        configs: dict = field(default_factory=dict)
        config_indices: dict = field(default_factory=dict)

        @classmethod
        def from_file(cls,path):
            from pathlib import Path
            file=Path(path)
            if not file.is_file():
                return cls()
            data=json.loads(file.read_text(encoding="utf-8-sig"))
            if not isinstance(data,dict):
                raise ValueError("translation settings must be an object")
            instance=cls(**{k:v for k,v in data.items() if k in cls.__dataclass_fields__})
            if instance.engine not in ENGINES:
                raise ValueError("unknown translation engine: "+str(instance.engine))
            if not isinstance(instance.configs,dict):
                raise ValueError("configs must be an object")
            if not isinstance(instance.config_indices, dict):
                raise ValueError("config_indices must be an object")
            for name in ("context_aware", "log_only"):
                if type(getattr(instance, name)) is not bool:
                    raise ValueError(name + " must be boolean")
            for name in ("num_contexts", "display_sentences"):
                value = getattr(instance, name)
                if type(value) is not int or not 0 <= value <= 10:
                    raise ValueError(name + " must be an integer in 0..10")
            if (type(instance.timeout) not in (int, float)
                    or not math.isfinite(instance.timeout) or instance.timeout <= 0):
                raise ValueError("timeout must be a finite positive number")
            if not isinstance(instance.target, str) or not instance.target.strip():
                raise ValueError("target must be a nonempty string")
            if not isinstance(instance.prompt, str):
                raise ValueError("prompt must be a string")
            for engine, profiles in instance.configs.items():
                if not isinstance(profiles, dict) and not (
                        isinstance(profiles, list) and profiles
                        and all(isinstance(profile, dict) for profile in profiles)):
                    raise ValueError("configuration must be an object or a nonempty list of objects")
            for engine, index in instance.config_indices.items():
                if type(index) is not int or index < 0:
                    raise ValueError("configuration index must be a nonnegative integer")
                profiles = instance.configs.get(engine)
                if isinstance(profiles, list) and index >= len(profiles):
                    raise ValueError("invalid configuration index for " + str(engine))
            return instance

        def config(self):
            configurations = self.configs.get(self.engine, {}) or {}
            if isinstance(configurations, list):
                index = int(self.config_indices.get(self.engine, 0))
                if not 0 <= index < len(configurations):
                    raise ValueError("invalid configuration index for " + self.engine)
                return configurations[index]
            if not isinstance(configurations, dict):
                raise ValueError("configuration must be an object or a list")
            return configurations


    def _config(config,key,default=""):
        # Accept both Python snake_case and original C# PascalCase keys.
        pascal="".join(s.title() for s in key.split("_"))
        if key == "keep_alive":
            return config.get("keep_alive",config.get("Keep_alive",config.get("KeepAlive",default)))
        return config.get(key,config.get(pascal,default))


    def normalize_url(url):
        """TextUtil.NormalizeUrl: preserve http(s) prefix, collapse other slashes."""
        match=re.match(r"^https?://",url)
        prefix=match.group(0) if match else ""
        return prefix+re.sub(r"/{2,}","/",url[len(prefix):]).rstrip("/")



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


    def google_translate(text,target="zh-CN",timeout=8.0):
        url=GOOGLE_URL+"?"+urlencode({"client":"dict-chrome-ex","sl":"auto",
                                      "tl":target,"q":text})
        return _request(url,timeout=timeout)[0][0]


    class TranslationAPI:
        def __init__(self,settings=None,requester=None):
            self.settings=settings or TranslationSettings()
            self.requester=requester or _request
            self._openai_fallback_index=0
            self._cancellation_local = threading.local()

        @contextmanager
        def cancellation(self,token):
            previous = getattr(self._cancellation_local,"token",None)
            self._cancellation_local.token = token
            try:
                if token is not None:
                    token.check()
                yield
            finally:
                self._cancellation_local.token = previous

        def _r(self,url,**kwargs):
            kwargs.setdefault("timeout",self.settings.timeout)
            token = getattr(self._cancellation_local,"token",None)
            if token is not None:
                token.check()
                # Injected requesters from existing tests have the old signature.
                if self.requester is _request:
                    kwargs["cancel_token"] = token
            return self.requester(url,**kwargs)

        def _lang(self,engine):
            target=self.settings.target
            mapping=LANGUAGES.get(engine,{})
            return mapping.get(target, STANDARD.get(target, target) if engine in
                               {"Youdao","Baidu","MTranServer","LibreTranslate"} else target)

        def _messages(self,text,contexts):
            messages=[{"role":"system","content":self.settings.prompt.format(self.settings.target)},
                      {"role":"user","content":f"🔤 {text} 🔤"}]
            if self.settings.context_aware:
                for entry in contexts:
                    source=getattr(entry,"source","")
                    translated=getattr(entry,"translated","")
                    if "[ERROR]" in translated or "[WARNING]" in translated:
                        continue
                    # Upstream inserts the context pair at index 1, newest first.
                    messages[1:1]=[{"role":"user","content":f"🔤 {source} 🔤"},
                                   {"role":"assistant","content":NOTICE.sub("",translated)}]
            return messages

        def translate(self,text,contexts=()):
            engine=self.settings.engine
            cfg=self.settings.config()
            lang=self._lang(engine)
            try:
                if engine=="Google":
                    url=GOOGLE_URL+"?"+urlencode({"client":"dict-chrome-ex",
                                                   "sl":"auto","tl":self.settings.target,"q":text})
                    return self._r(url)[0][0]
                if engine=="Google2":
                    url=GOOGLE2_URL+"?"+urlencode({"language":self.settings.target,
                        "key":_config(cfg,"api_key",GOOGLE2_KEY),"term":text,"strategy":"2"})
                    reply = self._r(url, headers={"x-referer":
                        "chrome-extension://mgijmajocgfcbeboacabfgobmjgjcoja"})
                    if "translateResponse" not in reply:
                        return "[ERROR] Translation Failed: Unexpected API response format"
                    return reply["translateResponse"]["translateText"]
                if engine in LLM_ENGINES:
                    return self._llm(engine,text,contexts,cfg)
                if engine=="DeepL":
                    answer=self._r(normalize_url(_config(cfg,"api_url","https://api.deepl.com/v2/translate")),
                        data={"text":[text],"target_lang":lang},
                        headers={"Authorization":"DeepL-Auth-Key "+_config(cfg,"api_key")})
                    return answer["translations"][0]["text"]
                if engine=="Youdao":
                    salt=str(int(time.time()*1000)%1000)
                    sign=hashlib.md5((_config(cfg,"app_key")+text+salt+
                                      _config(cfg,"app_secret")).encode("utf-8")).hexdigest()
                    answer=self._r(_config(cfg,"api_url","https://openapi.youdao.com/api"),
                        data={"q":text,"from":"auto","to":lang,"appKey":_config(cfg,"app_key"),
                              "salt":salt,"sign":sign},form=True)
                    if str(answer.get("errorCode","0"))!="0":
                        return "[ERROR] Translation Failed: Youdao Error - "+str(answer["errorCode"])
                    return next(iter(answer.get("translation",[])),"[ERROR] Translation Failed: No content")
                if engine=="MTranServer":
                    answer=self._r(normalize_url(_config(cfg,"api_url","http://localhost:8989/translate")),
                        data={"text":text,"to":lang,"from":_config(cfg,"source_language","en")},
                        headers={"Authorization":"Bearer "+_config(cfg,"api_key")})
                    return answer["result"]
                if engine=="Baidu":
                    salt=str(int(time.time()*1000)%1000)
                    sign=hashlib.md5((_config(cfg,"app_id")+text+salt+
                                      _config(cfg,"app_secret")).encode("utf-8")).hexdigest()
                    answer=self._r(_config(cfg,"api_url","https://fanyi-api.baidu.com/api/trans/vip/translate"),
                        data={"q":text,"from":"auto","to":lang,
                              "appid":_config(cfg,"app_id"),"salt":salt,"sign":sign},form=True)
                    if answer.get("error_code") and str(answer["error_code"])!="0":
                        return "[ERROR] Translation Failed: Baidu Error - "+str(answer["error_code"])
                    return next((e["dst"] for e in answer.get("trans_result",[])),
                                "[ERROR] Translation Failed: No content")
                if engine=="LibreTranslate":
                    answer=self._r(normalize_url(_config(cfg,"api_url","http://localhost:5000/translate")),
                        data={"q":text,"target":lang,"source":"auto","format":"text",
                              "api_key":_config(cfg,"api_key")})
                    return answer["translatedText"]
                raise ValueError("Unsupported engine "+str(engine))
            except TranslationCancelled:
                raise
            except Exception as exc:
                reason=str(getattr(exc,"reason",""))
                if isinstance(exc,TimeoutError) or (isinstance(exc,URLError) and "timed out" in reason.lower()):
                    return ("[ERROR] Translation Failed: The request was canceled due to timeout (> 8 seconds), "
                            "please use a faster API or check network connection.")
                return "[ERROR] Translation Failed: "+str(exc)

        @staticmethod
        def _request_data(platform, model, messages, temperature, keep_alive=600):
            """Exact upstream RequestData.cs / LLMRequestDataFactory.cs fields."""
            base = {"model": model, "messages": messages,
                    "temperature": float(temperature), "max_tokens": 128,
                    "stream": False, "keep_alive": int(keep_alive)}
            if platform == "integrated":
                base.update(think=False, enable_thinking=False, reasoning_effort="low",
                            reasoning={"exclude": True, "enabled": False, "effort": "low"},
                            thinking={"type": "disabled"})
            elif platform == "Aliyun":
                base["enable_thinking"] = False
            elif platform == "Anthropic":
                base["thinking"] = {"type": "disabled"}
            elif platform == "Ollama":
                base["think"] = False
            elif platform == "OpenRouter":
                base["reasoning"] = {"exclude": True, "enabled": False}
            elif platform == "OpenAI":
                base["reasoning"] = {"effort": "low"}
            elif platform == "XAI":
                base["reasoning_effort"] = "low"
            return base

        def _llm(self,engine,text,contexts,cfg):
            model=_config(cfg,"model_name")
            temperature=float(_config(cfg,"temperature",1.0))
            messages=self._messages(text,contexts)
            if engine=="LMStudio":
                url=normalize_url(_config(cfg,"api_url","http://localhost:1234/api/v1")+"/chat")
                entry=f"🔤 {text} 🔤"
                if self.settings.context_aware:
                    lines=[f"🔤 {e.source} 🔤 → {NOTICE.sub('',e.translated)}" for e in contexts
                           if "[ERROR]" not in e.translated and "[WARNING]" not in e.translated]
                    if lines:
                        entry="\n".join(lines)+"\n"+entry
                data={"model":model,"system_prompt":self.settings.prompt.format(self.settings.target),
                      "input":entry,"temperature":temperature}
                reply=self._r(url,data=data)
                return next((THINK.sub("",item.get("content","")) for item in reply.get("output",[])
                             if item.get("type")=="message"),
                            "[ERROR] Translation Failed: Unexpected response format")
            if engine=="Ollama":
                url=normalize_url(_config(cfg,"api_url","http://localhost:11434")+"/api/chat")
                reply=self._r(url,data=self._request_data(
                    "Ollama",model,messages,temperature,_config(cfg,"keep_alive",600)))
                return THINK.sub("",reply["message"]["content"])

            url=("https://openrouter.ai/api/v1/chat/completions" if engine=="OpenRouter"
                 else normalize_url(_config(cfg,"api_url")))
            headers={"Authorization":"Bearer "+_config(cfg,"api_key")}
            if engine=="OpenRouter":
                data=self._request_data("OpenRouter",model,messages,temperature)
                reply=self._r(url,data=data,headers=headers)
            else:
                # Upstream attempts all eight request schemas only on HTTP 400/422.
                schemas=("integrated","Aliyun","Anthropic","Ollama",
                         "OpenRouter","OpenAI","XAI","base")
                # As in TranslateAPI.cs, retain the last compatible schema across
                # subsequent requests instead of restarting at "integrated".
                for _ in range(len(schemas)):
                    index=self._openai_fallback_index
                    data=self._request_data(schemas[index],model,messages,temperature)
                    try:
                        reply=self._r(url,data=data,headers=headers)
                        break
                    except (ValueError,HTTPError) as error:
                        code=getattr(error,"code",None)
                        if code is None:
                            found=re.search(r"\b(400|422)\b",str(error))
                            code=int(found.group(1)) if found else None
                        if code not in (400,422):
                            raise
                        self._openai_fallback_index+=1
                        if self._openai_fallback_index>=len(schemas):
                            self._openai_fallback_index=0
                            raise
            return THINK.sub("",reply["choices"][0]["message"]["content"])
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

    from .api import (TranslationAPI, TranslationSettings, LLM_ENGINES,
                      CancellationToken, TranslationCancelled)
    from .history import HistoryStore
    from .text import (PUNC_EOS, SHORT_THRESHOLD, NOTICE, preprocess,
                       extract_original_caption, latest_caption, similar_to_last)

    ROOT = Path(__file__).resolve().parent.parent
    ACTIVE_SINKS = weakref.WeakSet()


    class GoogleTranslateSink:
        """Backward-compatible sink name; supports all 11 upstream translation APIs."""
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
            if settings is None:
                try:
                    settings = TranslationSettings.from_file(root / "translation-settings.json")
                except (OSError, ValueError, TypeError) as exc:
                    print("翻译配置无效，使用默认 Google 日译中：", exc, flush=True)
                    settings = TranslationSettings()
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
            with self.state_lock:
                if self.closed:
                    raise RuntimeError('translation sink is closed')
                source=text.strip()
                # maxlen is an artificial audio chunk boundary, NOT semantic EOS.
                # Join the next ASR segment before constructing a final sentence.
                if reason != "maxlen" and source[-1] not in PUNC_EOS:
                    source += "。"
                store=getattr(self,"_final_store",None)
                if store is not None:
                    # This runs on the Final publisher, never the capture callback.
                    # A failed transaction propagates to the ASR sink error path;
                    # do not advertise text as accepted before it is recoverable.
                    try:
                        pending=store.accept_translation_final(source,reason=="maxlen")
                    except Exception as exc:
                        print('翻译 Final 本地保存失败：',repr(exc),flush=True)
                        raise
                self.finalized_text = (getattr(self,"finalized_text","") + source)[-8192:]
                self.observed_text = self.finalized_text
                # Final is an event, not a polled latest value: backlog can emit
                # several complete segments between two sync ticks. Preserve every
                # event and join artificial maxlen cuts until a semantic endpoint.
                if store is None:
                    pending = getattr(self,"_pending_final_text","") + source
                    if reason == "maxlen":
                        while len(pending) >= 8192:
                            self.pending_text_queue.put((preprocess(pending[:8192]), True))
                            pending = pending[8192:]
                    elif pending:
                        self.pending_text_queue.put((preprocess(pending), True))
                        pending = ""
                self._pending_final_text = pending
                if reason != "maxlen" or not pending:
                    self._queued_final_caption = extract_original_caption(preprocess(self.finalized_text))

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
                if self.sync_count > self.MAX_SYNC_INTERVAL or self.idle_count == self.MAX_IDLE_INTERVAL:
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
            with self.task_lock:
                if self.closed:
                    return
                if store is not None and time.monotonic()<getattr(self,"_final_retry_after",0.):
                    return
                generation=getattr(self,"_settings_generation",0)
                try:
                    row=store.pending_translation() if store is not None else None
                except Exception as exc:
                    self._final_retry_after=time.monotonic()+1.
                    with self.output_lock:
                        self.output=('', '[ERROR] 读取本地翻译队列失败，待译 Final 未删除', False)
                        self.output_version+=1
                    print('翻译队列读取失败：',repr(exc),flush=True)
                    return
                pending_id=None
                if row:
                    if time.monotonic()<getattr(self,"_final_retry_after",0.):
                        return
                    pending_id,source=row
                    is_final=True
                else:
                    try:
                        source = self.pending_text_queue.get_nowait()
                    except queue.Empty:
                        source = ""
                    is_final = isinstance(source, tuple)
                    if is_final:
                        source, is_final = source
                if source and self.settings.log_only:
                    try:
                        previous = self.history.last()
                        overwrite = bool(not is_final and previous
                            and not getattr(self,"_history_last_is_final",False)
                            and similar_to_last(source,previous.source))
                        if pending_id is not None:
                            committed=store.complete_translation(pending_id,"N/A","N/A","LogOnly")
                            if not committed:return
                        else:
                            self.history.log(source,"N/A","N/A","LogOnly",overwrite=overwrite)
                        self._history_last_is_final = bool(is_final)
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
                durable_partial=not final and getattr(self,"_final_store",None) is not None
                if durable_partial and getattr(self,"_partial_active",False):
                    return None
                if durable_partial:
                    self._partial_active=True
                self.next_task_id += 1
                task = {"id":self.next_task_id,"text":source,"cancelled":False,
                        "cancel_token":CancellationToken(),"final":final,"pending_id":pending_id}
                self.tasks.append(task)
            if final:
                # Final translations commit in capture order. Partial work remains
                # asynchronous and may be superseded, but cannot cancel a Final.
                self._translation_worker(task)
                return task
            def run_partial():
                try:
                    self._translation_worker(task)
                finally:
                    if durable_partial:
                        with self.task_lock:
                            self._partial_active=False
                            if task in self.tasks:self.tasks.remove(task)
            thread=threading.Thread(target=run_partial,daemon=True,
                                    name=f"translation-job-{task['id']}")
            task["thread"]=thread
            thread.start()
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
            try:
                with context:
                    if settings.context_aware and settings.engine not in LLM_ENGINES:
                        previous = self.history.previous_text(
                            settings.num_contexts,translated=False,entries=contexts)
                        raw = api.translate(f"{previous} 🔤 {text} 🔤",contexts)
                        if "[ERROR]" in raw or "[WARNING]" in raw:
                            translated = raw
                        else:
                            match = re.search(r"🔤\s*(.+?)\s*🔤",raw)
                            translated = match.group(1) if match else ""
                    else:
                        translated = api.translate(text,contexts).replace("🔤","")
            except TranslationCancelled:
                with self.task_lock:
                    if task in self.tasks:
                        self.tasks.remove(task)
                        if task.get('pending_id') is not None:
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

        def _on_task_completed(self,task,translated,choke):
            with self.task_lock:
                if task.get("cancelled") or task not in self.tasks or self.closed:
                    return False
                pending_id=task.get("pending_id")
                if pending_id is not None:
                    # Provider errors are not successful delivery. Keep FIFO head
                    # recoverable and avoid an unbounded hot retry loop.
                    if not translated or '[ERROR]' in translated or '[WARNING]' in translated:
                        self.tasks.remove(task)
                        self._final_retry_after=time.monotonic()+1.
                        with self.output_lock:
                            self.output=(task['text'],translated or '[ERROR] Empty translation; Final 已保留',False)
                            self.output_version+=1
                        print('翻译 Final 未完成，已保留待重试：',translated,flush=True)
                        return False
                    try:
                        previous=self.history.last()
                        committed=self._final_store.complete_translation(
                            pending_id,translated,self.settings.target,self.settings.engine)
                    except Exception as exc:
                        self.tasks.remove(task)
                        self._final_retry_after=time.monotonic()+1.
                        with self.output_lock:
                            self.output=(task['text'],'[ERROR] 本地提交失败，Final 已保留待重试',False)
                            self.output_version+=1
                        print('翻译 Final 提交失败，已保留：',repr(exc),flush=True)
                        return False
                    if not committed:
                        self.tasks.remove(task)
                        return False
                index=self.tasks.index(task)
                for older in self.tasks[:index]:
                    older["cancelled"]=True
                    token=older.get("cancel_token")
                    if token is not None:
                        token.cancel()
                del self.tasks[:index+1]
                # Keep task state and published result together. The upstream
                # intends only the latest completed task to replace prior output.
                with self.output_lock:
                    self.output=(task["text"],translated,choke)
                    self.output_version += 1
                # Commit history in the same order as result publication. Settings
                # swaps also take task_lock, so engine/target cannot change halfway
                # through this commit. HistoryStore bounds failed SQLite waits.
                try:
                    if pending_id is None:
                        previous=self.history.last()
                    if not isinstance(getattr(previous,"source",None),str):
                        previous=None
                    overwrite=bool(previous and not getattr(self,"_history_last_is_final",False)
                                   and similar_to_last(task["text"],previous.source))
                    if not overwrite and previous:
                        with self.state_lock:
                            queue_=getattr(self,"context_entries",None)
                            if queue_ is not None:
                                queue_.append(previous)
                    if pending_id is None:
                        self.history.log(task["text"],translated,self.settings.target,
                                         self.settings.engine,overwrite=overwrite)
                    self._history_last_is_final = bool(task.get("final"))
                except Exception as error:
                    print("翻译历史保存失败：",repr(error),flush=True)
            return True

        def _display_loop(self):
            seen=0
            while not self.closed:
                if self.settings.log_only:
                    time.sleep(self.DISPLAY_TICK)
                    continue
                with self.output_lock:
                    version=self.output_version
                    result=self.output
                if version!=seen:
                    seen=version
                    source,translated,choke=result
                    with self.state_lock:
                        show=getattr(self.settings,"display_sentences",0)
                        previous=list(getattr(self,"context_entries",[]))[-show:] if show > 0 else []
                    if show>0 and "[ERROR]" not in translated and "[WARNING]" not in translated:
                        prefix=self.history.previous_text(show,entries=previous)
                        if prefix:
                            translated=prefix+translated
                    # Expansion above may overlap a settings swap or newer result.
                    # Validate and publish under the same lock used by reset below.
                    with self.task_lock:
                        with self.output_lock:
                            current = version == self.output_version
                        if not current or self.closed or self.settings.log_only:
                            continue
                        self.overlay.translation(source,translated)
                    if choke:
                        time.sleep(self.COMPLETE_SENTENCE_CHOKE)
                time.sleep(self.DISPLAY_TICK)

        def clear_history(self):
            """Purge historical data without old asynchronous requests restoring it."""
            with self.task_lock:
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
            with self.task_lock:
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
            with self.task_lock:
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
    """Tkinter translation settings and full history management; ASR-independent."""
    import json
    from dataclasses import asdict
    from pathlib import Path

    from .api import ENGINES, TranslationSettings
    from .history import HistoryStore

    ROOT=Path(__file__).resolve().parent.parent
    FIELDS=("api_url","api_key","app_key","app_secret","app_id",
            "model_name","temperature","source_language","keep_alive")
    PASSWORD={"api_key","app_secret"}
    LANGUAGES=("zh-CN","zh-TW","en-US","en-GB","ja-JP","ko-KR","fr-FR",
               "th-TH","ru-RU","es-ES","pt-BR","tr-TR","ar-SA")


    def normalize_profile(values):
        """GUI variables are strings; JSON protocol requires numeric values."""
        result={key:value.strip() for key,value in values.items() if value.strip()}
        if "temperature" in result:
            result["temperature"]=float(result["temperature"])
        if "keep_alive" in result:
            result["keep_alive"]=int(result["keep_alive"])
        return result


    def open_settings(parent,config_path=None):
        """Multi-profile engine editor mirroring Configs + ConfigIndices."""
        import tkinter as tk
        from tkinter import ttk,messagebox
        path=Path(config_path or ROOT/"translation-settings.json")
        settings=TranslationSettings.from_file(path)
        win=tk.Toplevel(parent)
        win.title("翻译设置 | LiveCaptions-Translator Python")
        win.attributes("-topmost",True)
        win.resizable(False,False)
        body=ttk.Frame(win,padding=14)
        body.pack(fill="both",expand=True)
        engine=tk.StringVar(value=settings.engine)
        target=tk.StringVar(value=settings.target)
        context=tk.BooleanVar(value=settings.context_aware)
        log_only=tk.BooleanVar(value=settings.log_only)
        num_contexts=tk.IntVar(value=settings.num_contexts)
        show_history=tk.IntVar(value=settings.display_sentences)
        prompt=tk.StringVar(value=settings.prompt)
        vars_={key:tk.StringVar() for key in FIELDS}
        profiles={}
        for name in ENGINES:
            raw=settings.configs.get(name,{})
            if isinstance(raw,list):
                profiles[name]=[dict(v) for v in raw] or [{}]
            else:
                profiles[name]=[dict(raw or {})]
        indices={name:max(0,min(int(settings.config_indices.get(name,0)),
                                 len(profiles[name])-1)) for name in ENGINES}
        selected=[settings.engine,indices[settings.engine]]
        profile_index=tk.StringVar()

        def display():
            name,idx=selected
            profile_box["values"]=[str(i+1) for i in range(len(profiles[name]))]
            profile_index.set(str(idx+1))
            cfg=profiles[name][idx]
            for key in FIELDS:
                camel="".join(word.title() for word in key.split("_"))
                vars_[key].set(str(cfg.get(key,cfg.get(camel,""))))

        def remember():
            name,idx=selected
            profiles[name][idx]=normalize_profile({k:v.get() for k,v in vars_.items()})

        def switch_engine(event=None):
            try:
                remember()
            except ValueError as error:
                messagebox.showerror("设置无效",str(error),parent=win)
                engine.set(selected[0])
                return
            name=engine.get()
            selected[:]=[name,indices[name]]
            display()

        def switch_profile(event=None):
            try:
                remember()
            except ValueError as error:
                messagebox.showerror("设置无效",str(error),parent=win)
                profile_index.set(str(selected[1]+1))
                return
            idx=int(profile_index.get())-1
            indices[selected[0]]=idx
            selected[1]=idx
            display()

        def add_profile():
            remember()
            name=selected[0]
            profiles[name].append(dict(profiles[name][selected[1]]))
            selected[1]=len(profiles[name])-1
            indices[name]=selected[1]
            display()

        def delete_profile():
            name=selected[0]
            if len(profiles[name])<=1:
                messagebox.showinfo("至少保留一份","每个引擎至少需要一份配置。",parent=win)
                return
            del profiles[name][selected[1]]
            selected[1]=min(selected[1],len(profiles[name])-1)
            indices[name]=selected[1]
            display()

        ttk.Label(body,text="翻译引擎").grid(row=0,column=0,sticky="w")
        box=ttk.Combobox(body,textvariable=engine,values=ENGINES,state="readonly",width=28)
        box.grid(row=0,column=1,sticky="ew",padx=8,pady=3)
        box.bind("<<ComboboxSelected>>",switch_engine)
        ttk.Label(body,text="配置档案").grid(row=1,column=0,sticky="w")
        profile_row=ttk.Frame(body)
        profile_row.grid(row=1,column=1,sticky="w",padx=8,pady=3)
        profile_box=ttk.Combobox(profile_row,textvariable=profile_index,state="readonly",width=8)
        profile_box.pack(side="left")
        profile_box.bind("<<ComboboxSelected>>",switch_profile)
        ttk.Button(profile_row,text="复制新增",command=add_profile).pack(side="left",padx=6)
        ttk.Button(profile_row,text="删除当前",command=delete_profile).pack(side="left")
        ttk.Label(body,text="目标语言").grid(row=2,column=0,sticky="w")
        ttk.Combobox(body,textvariable=target,values=LANGUAGES,width=28).grid(
            row=2,column=1,padx=8,pady=3)
        ttk.Checkbutton(body,text="启用翻译上下文",variable=context).grid(
            row=3,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(body,text="仅记录模式（暂停翻译）",variable=log_only).grid(
            row=4,column=0,columnspan=2,sticky="w")
        ttk.Label(body,text="上下文条数").grid(row=5,column=0,sticky="w")
        tk.Spinbox(body,from_=0,to=10,textvariable=num_contexts,width=8).grid(
            row=5,column=1,sticky="w",padx=8)
        ttk.Label(body,text="历史显示句数").grid(row=6,column=0,sticky="w")
        tk.Spinbox(body,from_=0,to=10,textvariable=show_history,width=8).grid(
            row=6,column=1,sticky="w",padx=8)
        ttk.Label(body,text="LLM Prompt").grid(row=7,column=0,sticky="w")
        ttk.Entry(body,textvariable=prompt,width=41).grid(row=7,column=1,padx=8,pady=4)
        ttk.Separator(body).grid(row=8,column=0,columnspan=2,sticky="ew",pady=5)
        for i,key in enumerate(FIELDS,9):
            ttk.Label(body,text=key).grid(row=i,column=0,sticky="w")
            ttk.Entry(body,textvariable=vars_[key],width=41,
                      show="*" if key in PASSWORD else "").grid(row=i,column=1,padx=8,pady=3)
        display()
        ttk.Label(body,text="保存后即时生效；API 密钥会保存在本机 JSON 中。",
                  foreground="#666666").grid(row=18,column=0,columnspan=2,sticky="w",pady=5)

        def save():
            try:
                remember()
                settings.engine=engine.get()
                settings.target=target.get().strip() or "zh-CN"
                settings.context_aware=bool(context.get())
                settings.log_only=bool(log_only.get())
                settings.num_contexts=max(0,min(10,int(num_contexts.get())))
                settings.display_sentences=max(0,min(10,int(show_history.get())))
                settings.prompt=prompt.get()
                settings.configs=profiles
                settings.config_indices=indices
                content=json.dumps(asdict(settings),ensure_ascii=False,indent=2)
                path.parent.mkdir(parents=True,exist_ok=True)
                tmp=path.with_suffix(".json.tmp")
                tmp.write_text(content+"\n",encoding="utf-8")
                tmp.replace(path)
                # Active translation sinks observe the new config immediately.
                from .engine import ACTIVE_SINKS
                for sink in tuple(ACTIVE_SINKS):
                    sink.update_settings(settings)
            except (ValueError,OSError,TypeError) as exc:
                messagebox.showerror("保存失败",str(exc),parent=win)
                return
            win.destroy()
        ttk.Button(body,text="保存",command=save).grid(row=19,column=0,sticky="e",pady=5)
        ttk.Button(body,text="取消",command=win.destroy).grid(row=19,column=1,sticky="e",pady=5)
        ttk.Label(body,text="隐私提示：云端翻译可能上传字幕原文；本地引擎也需确认服务地址。",
                  foreground="#666666",wraplength=440).grid(
            row=20,column=0,columnspan=2,sticky="w",pady=(5,0))
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
    """Python translation engine port of SakiRinn/LiveCaptions-Translator.

    Reference: upstream master commit a6fee12757b15edbeef7c60f8895e0be694801e1.
    ASR remains separate.
    """
    from .api import TranslationAPI, TranslationSettings, google_translate
    from .engine import GoogleTranslateSink
    from .history import HistoryStore, TranslationEntry
    __all__ = ["TranslationAPI","TranslationSettings","google_translate",
               "GoogleTranslateSink","HistoryStore","TranslationEntry"]
    # <<< INLINE translation/__init__.py END >>>

