# Qwen3-LiveCaption · Google / Google2

Windows 本地日语实时识别、可选中文翻译和悬浮字幕，Python 单文件版本。

## 项目内容

- **唯一启动文件：** `live-caption-ja-duo.pyw`（内嵌 ASR 与 Google / Google2 翻译模块）。
- **采集与识别：** WASAPI Loopback、Silero VAD、Qwen3-ASR-1.7B GGUF 和 llama.cpp / CUDA。ASR 在本机运行。
- **翻译：** 仅保留 `Google` 和 `Google2`。在线翻译会将待翻译文本发送至相应 Google 接口；这些并非具有服务保障的官方公共 API，可能变更或限流。
- **输出：** Tkinter 悬浮字幕、TXT/SRT 和可选翻译历史。

## Windows 安装

需要 NVIDIA GPU/驱动、Windows 10/11、Python 3.11+ 和系统播放设备。

```bat
git clone https://github.com/A942199/Qwen3-LiveCaption.git
cd Qwen3-LiveCaption
setup.bat
```

安装脚本会创建 `.venv`、安装依赖、下载 Qwen3-ASR GGUF 模型并准备 llama.cpp CUDA 运行程序。首次使用涉及大文件下载，请先阅读安装脚本。

完成后双击 **`live-caption-ja-duo.pyw`**。文件已内嵌翻译代码，不需要额外复制 `translation/` 目录。

## Google / Google2

| 引擎 | 接口 | 说明 |
| --- | --- | --- |
| Google | `clients5.google.com/translate_a/t` | 使用 Chrome 字典客户端请求格式 |
| Google2 | `dictionaryextension-pa.googleapis.com/v1/dictionaryExtensionData` | 使用扩展字典请求格式；支持自定义 API Key / 环境变量引用 |

**隐私提示：** 翻译默认 `log_only=true`（仅记录、不发在线请求）。主动启用远程翻译前，请确认可以将字幕文本发送至 Google。旧的其他服务配置会在内存中迁移，不会自动发送旧服务的凭证；翻译历史数据库不会被清空。

上述非官方接口可能改变、要求密钥或受访问限制；本项目不保证其持续可用。

## 使用

```bat
.venv\Scripts\python.exe live-caption-ja-duo.pyw --help
.venv\Scripts\python.exe live-caption-ja-duo.pyw --list
.venv\Scripts\python.exe live-caption-ja-duo.pyw --console
```

普通悬浮窗启动方式为双击 `.pyw`。`live-caption-settings.json`、`translation-settings.json`、`translation-history.sqlite3`、音频及字幕历史留在本机，**不要上传到仓库**。

## 代码与验证范围

仓库仅保存 Python 单文件、`requirements.txt`、`setup.bat` 和说明，不包含模型权重、虚拟环境、llama.cpp 二进制、音频、数据库或历史诊断报告。

本次整理 **未修改 Google/Google2 的代码**，只更新启动入口与项目资料。离线验证覆盖语法和模拟翻译请求；不等同于 Google 实际在线可用性、真实 GPU 识别或长时间运行验证。

## 来源

Google/Google2 请求格式参考 [SakiRinn/LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator)。第三方依赖、模型及服务遵循其各自许可和使用条件。
