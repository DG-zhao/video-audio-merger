"""
视频音频合并插件 — Coze Plugin
功能：下载视频和音频文件，使用 FFmpeg 合并后返回带音轨的视频。
"""

import os
import re
import uuid
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import urlparse
from pathlib import Path

import requests
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

# 工作目录
WORK_DIR = Path(__file__).parent / "work"
WORK_DIR.mkdir(exist_ok=True)

# 最大文件大小（500MB）
MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024

# 异步任务状态存储
_task_status: dict = {}  # task_id -> {"status": "processing|success|error", "data": ...}


def find_ffmpeg():
    """查找 ffmpeg 路径"""
    # 优先从环境变量找
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path
    # 常见 Windows 安装路径（含 winget 安装路径）
    candidates = [
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path.home() / "ffmpeg/bin/ffmpeg.exe",
        # winget 安装路径
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe",
    ]
    # 自动搜索 WinGet 目录下的 ffmpeg（版本号可能变化）
    winget_base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    if winget_base.exists():
        for d in winget_base.iterdir():
            if "FFmpeg" in d.name or "ffmpeg" in d.name:
                bin_dir = d / "bin"
                if bin_dir.exists():
                    exe = bin_dir / "ffmpeg.exe"
                    if exe.exists():
                        candidates.insert(0, str(exe))
                # 也搜索子目录
                for sub in d.rglob("ffmpeg.exe"):
                    candidates.insert(0, str(sub))
    for p in candidates:
        p = Path(p)
        if p.exists():
            return str(p)
    return None


def find_ffprobe():
    """查找 ffprobe 路径"""
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        return None
    ffprobe = ffmpeg_path.replace("ffmpeg", "ffprobe")
    if ffprobe != ffmpeg_path and os.path.exists(ffprobe):
        return ffprobe
    ffprobe = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe")
    if os.path.exists(ffprobe):
        return ffprobe
    return shutil.which("ffprobe")


def resolve_storage_to_url(share_url: str) -> str:
    """
    解析 storage.to 分享链接，返回 stusercontent.com 直链。
    如果链接不是 storage.to 或解析失败，返回原 URL。
    """
    try:
        parsed = urlparse(share_url)
        if "storage.to" not in parsed.netloc:
            return share_url  # 不是 storage.to，不处理

        # 获取分享页面
        resp = requests.get(share_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        html = resp.text

        # 提取 stusercontent.com 直链（排除 thumbnails 路径）
        # 格式: stusercontent.com/<uuid>?expires=...&sig=...
        pattern = r'stusercontent\.com/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\?[^"\'<>\s]+)'
        matches = re.findall(pattern, html)
        if not matches:
            return share_url  # 没找到直链，保持原 URL

        # 取第一个匹配（排除重复的 \u0026 版本）
        dirty_url = matches[0]
        # 清理 \u0026 和 &amp; → &
        clean_url = dirty_url.replace("\\u0026", "&").replace("&amp;", "&")
        direct_url = f"https://stusercontent.com/{clean_url}"

        return direct_url
    except Exception:
        return share_url  # 解析失败，用原 URL 尝试下载


def download_file(url: str, dest: str) -> tuple:
    """下载文件，带大小限制和进度。返回 (success, error_message)"""
    actual_url = url
    try:
        # 自动解析 storage.to 等分享链接为直链
        actual_url = resolve_storage_to_url(url)
        if actual_url != url:
            print(f"[INFO] 已解析 storage.to 直链: {actual_url[:80]}...")

        resp = requests.get(actual_url, stream=True, timeout=300, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

        # 检查 Content-Type，避免下载到 HTML 页面
        content_type = resp.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            return False, "链接返回的是网页而不是文件，请使用直链而不是分享页面链接"

        total = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > MAX_DOWNLOAD_SIZE:
                    return False, f"文件超过大小限制（{MAX_DOWNLOAD_SIZE // (1024*1024)}MB）"
                f.write(chunk)

        if total < 1000:
            return False, "下载到的文件太小（可能链接已过期或不是有效媒体文件）"

        return True, ""
    except requests.exceptions.HTTPError as e:
        return False, f"HTTP 错误（{e.response.status_code}）：文件不存在或链接已过期"
    except requests.exceptions.ConnectionError:
        return False, "无法连接到服务器，请检查链接是否有效"
    except requests.exceptions.Timeout:
        return False, "下载超时，文件可能过大或链接无效"
    except Exception as e:
        return False, f"下载异常: {str(e)}"


@app.route("/health", methods=["GET"])
def health():
    ffmpeg = find_ffmpeg()
    return jsonify({
        "status": "ok",
        "ffmpeg_available": ffmpeg is not None,
        "ffmpeg_path": ffmpeg,
    })


@app.route("/openapi.json", methods=["GET"])
def openapi_spec():
    """Coze 插件所需的 OpenAPI 规范"""
    base_url = request.host_url.rstrip("/")
    return jsonify({
        "openapi": "3.0.0",
        "info": {
            "title": "视频音频合并插件",
            "description": "将视频文件与音频文件合并为带音轨的视频，支持 mp4/mov/webm 视频和 mp3/wav/aac 音频",
            "version": "1.0.0",
        },
        "servers": [{"url": base_url}],
        "paths": {
            "/merge": {
                "post": {
                    "summary": "合并视频和音频",
                    "operationId": "mergeVideoAudio",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["videoUrl", "audioUrl"],
                                    "properties": {
                                        "videoUrl": {
                                            "type": "string",
                                            "description": "视频文件的 URL 地址",
                                        },
                                        "audioUrl": {
                                            "type": "string",
                                            "description": "音频文件的 URL 地址（背景音乐/配音）",
                                        },
                                        "audioVolume": {
                                            "type": "number",
                                            "description": "音频音量，0.0-2.0，默认 1.0（原始音量），0.5 为一半",
                                            "default": 1.0,
                                        },
                                        "videoVolume": {
                                            "type": "number",
                                            "description": "原视频音量，0.0-2.0，默认 0.3（压低原声突出BGM），设为 0 则完全静音",
                                            "default": 0.3,
                                        },
                                        "loopAudio": {
                                            "type": "boolean",
                                            "description": "音频是否循环播放以匹配视频长度",
                                            "default": True,
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "合并成功，返回下载信息",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "message": {"type": "string"},
                                            "task_id": {"type": "string"},
                                            "video_size_mb": {"type": "number"},
                                            "download_url": {"type": "string"},
                                        }
                                    }
                                }
                            },
                        },
                        "400": {
                            "description": "参数错误",
                        },
                        "500": {
                            "description": "合并失败",
                        },
                    },
                }
            }
        },
    })


def _do_merge(session_id: str, video_url: str, audio_url: str,
              audio_volume: float, video_volume: float, loop_audio: bool,
              base_url: str):
    """后台线程执行合并任务"""
    _task_status[session_id] = {"status": "processing", "message": "开始处理..."}
    
    session_dir = WORK_DIR / session_id
    try:
        # 推断文件扩展名
        video_ext = os.path.splitext(urlparse(video_url).path)[1] or ".mp4"
        audio_ext = os.path.splitext(urlparse(audio_url).path)[1] or ".mp3"
        video_path = session_dir / f"input{video_ext}"
        audio_path = session_dir / f"audio{audio_ext}"
        output_path = session_dir / "output.mp4"

        # 下载视频
        _task_status[session_id]["message"] = "正在下载视频..."
        ok, err = download_file(video_url, str(video_path))
        if not ok:
            _task_status[session_id] = {"status": "error", "message": f"视频下载失败: {err}"}
            return

        # 下载音频
        _task_status[session_id]["message"] = "正在下载音频..."
        ok, err = download_file(audio_url, str(audio_path))
        if not ok:
            _task_status[session_id] = {"status": "error", "message": f"音频下载失败: {err}"}
            return

        # 获取视频时长
        ffprobe = find_ffprobe()
        duration = None
        if ffprobe:
            try:
                result = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                    capture_output=True, text=True, timeout=30,
                )
                duration = float(result.stdout.strip())
            except Exception:
                pass

        # 查找 ffmpeg
        ffmpeg = find_ffmpeg()

        # 构建 FFmpeg 命令
        cmd = [ffmpeg, "-y"]
        cmd.extend(["-i", str(video_path)])
        if loop_audio and duration:
            cmd.extend(["-stream_loop", "-1", "-i", str(audio_path)])
            cmd.extend(["-t", str(duration)])
        else:
            cmd.extend(["-i", str(audio_path)])

        if video_volume > 0:
            filter_complex = (
                f"[0:a]volume={video_volume}[v];"
                f"[1:a]volume={audio_volume}[a];"
                f"[v][a]amix=inputs=2:duration=first:dropout_transition=2[out]"
            )
            cmd.extend(["-filter_complex", filter_complex, "-map", "0:v:0", "-map", "[out]"])
        else:
            cmd.extend(["-filter_complex", f"[1:a]volume={audio_volume}[out]", "-map", "0:v:0", "-map", "[out]"])

        cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                     "-c:a", "aac", "-b:a", "192k", "-shortest",
                     "-movflags", "+faststart", str(output_path)])

        # 执行合并
        _task_status[session_id]["message"] = "正在合并视频和音频..."
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if process.returncode != 0:
            _task_status[session_id] = {
                "status": "error",
                "message": "FFmpeg 合并失败",
                "ffmpeg_stderr": process.stderr[-500:],
            }
            return

        if not output_path.exists():
            _task_status[session_id] = {"status": "error", "message": "合并后未生成输出文件"}
            return

        video_size = output_path.stat().st_size
        download_url = f"{base_url}/download/{session_id}"
        _task_status[session_id] = {
            "status": "success",
            "message": "视频音频合并成功",
            "task_id": session_id,
            "video_size_mb": round(video_size / (1024 * 1024), 2),
            "download_url": download_url,
        }

    except subprocess.TimeoutExpired:
        _task_status[session_id] = {"status": "error", "message": "合并超时，视频可能过大"}
    except Exception as e:
        _task_status[session_id] = {"status": "error", "message": f"处理异常: {str(e)}"}


@app.route("/merge", methods=["POST"])
def merge_video_audio():
    """合并视频和音频（同步模式，直接返回结果）"""
    data = request.get_json(silent=True) or {}

    video_url = data.get("videoUrl", "").strip()
    audio_url = data.get("audioUrl", "").strip()

    if not video_url or not audio_url:
        return jsonify({"error": "videoUrl 和 audioUrl 为必填参数"}), 400

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return jsonify({"error": "服务器未安装 FFmpeg"}), 500

    audio_volume = max(0.0, min(2.0, float(data.get("audioVolume", 1.0))))
    video_volume = max(0.0, min(2.0, float(data.get("videoVolume", 0.3))))
    loop_audio = data.get("loopAudio", True)

    session_id = uuid.uuid4().hex[:8]
    session_dir = WORK_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    base_url = request.host_url.rstrip("/")

    # 同步执行合并
    try:
        _do_merge(session_id, video_url, audio_url, audio_volume, video_volume, loop_audio, base_url)
        result = _task_status.get(session_id, {})
        return jsonify(result)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "合并超时，视频可能过大"}), 500
    except Exception as e:
        return jsonify({"error": f"处理异常: {str(e)}"}), 500


@app.route("/status/<task_id>", methods=["GET"])
def task_status(task_id):
    """查询异步任务状态"""
    info = _task_status.get(task_id)
    if not info:
        return jsonify({"error": "任务不存在或已过期"}), 404
    return jsonify(info)


@app.route("/download/<session_id>", methods=["GET"])
def download_video(session_id):
    """下载合并后的视频文件"""
    output_path = WORK_DIR / session_id / "output.mp4"
    if not output_path.exists():
        return jsonify({"error": "文件不存在或已过期"}), 404
    return send_file(
        str(output_path),
        mimetype="video/mp4",
        as_attachment=True,
        download_name="merged_video.mp4",
    )


if __name__ == "__main__":
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        print(f"[OK] FFmpeg 已找到: {ffmpeg}")
    else:
        print("[WARN] 未找到 FFmpeg，请安装: apt-get install ffmpeg")
    port = int(os.environ.get("PORT", 8899))
    print(f"[INFO] 服务启动: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
