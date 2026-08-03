"""
视频音频合并插件 — Coze Plugin
功能：下载视频和音频文件，使用 FFmpeg 合并后返回带音轨的视频。
"""

import os
import uuid
import shutil
import subprocess
import tempfile
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


def download_file(url: str, dest: str) -> bool:
    """下载文件，带大小限制和进度"""
    try:
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()
        total = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > MAX_DOWNLOAD_SIZE:
                    return False
                f.write(chunk)
        return True
    except Exception:
        return False


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


@app.route("/merge", methods=["POST"])
def merge_video_audio():
    """主接口：合并视频和音频"""
    data = request.get_json(silent=True) or {}

    video_url = data.get("videoUrl", "").strip()
    audio_url = data.get("audioUrl", "").strip()

    if not video_url or not audio_url:
        return jsonify({"error": "videoUrl 和 audioUrl 为必填参数"}), 400

    # 查找 ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return jsonify({
            "error": "服务器未安装 FFmpeg，请先安装",
            "install_hint": "winget install Gyan.FFmpeg",
        }), 500

    audio_volume = float(data.get("audioVolume", 1.0))
    video_volume = float(data.get("videoVolume", 0.3))
    loop_audio = data.get("loopAudio", True)

    # 限制音量范围
    audio_volume = max(0.0, min(2.0, audio_volume))
    video_volume = max(0.0, min(2.0, video_volume))

    session_id = uuid.uuid4().hex[:8]
    session_dir = WORK_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # 推断文件扩展名
    video_ext = os.path.splitext(urlparse(video_url).path)[1] or ".mp4"
    audio_ext = os.path.splitext(urlparse(audio_url).path)[1] or ".mp3"

    video_path = session_dir / f"input{video_ext}"
    audio_path = session_dir / f"audio{audio_ext}"
    output_path = session_dir / "output.mp4"

    try:
        # 下载视频
        if not download_file(video_url, str(video_path)):
            return jsonify({"error": "视频下载失败，文件可能过大或链接无效"}), 500

        # 下载音频
        if not download_file(audio_url, str(audio_path)):
            return jsonify({"error": "音频下载失败，文件可能过大或链接无效"}), 500

        # 获取视频时长（用于音频循环）
        probe_cmd = [
            ffmpeg, "-i", str(video_path),
            "-show_entries", "format=duration",
            "-v", "quiet", "-of", "csv=p=0",
            "-f", "null", "-",
        ]
        # 用 ffprobe 获取时长
        ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
        if not os.path.exists(ffprobe):
            ffprobe = ffmpeg.replace("ffmpeg.exe", "ffprobe.exe")

        duration = None
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True, timeout=30,
            )
            duration = float(result.stdout.strip())
        except Exception:
            pass

        # 构建 FFmpeg 命令
        # 策略：视频画面保留，视频原声 + BGM 混合
        cmd = [ffmpeg, "-y"]

        # 输入1：视频
        cmd.extend(["-i", str(video_path)])

        # 输入2：音频
        if loop_audio and duration:
            # 使用 stream_loop 循环音频
            cmd.extend(["-stream_loop", "-1", "-i", str(audio_path)])
            cmd.extend(["-t", str(duration)])
        else:
            cmd.extend(["-i", str(audio_path)])

        # 滤镜：混合视频原声和BGM
        if video_volume > 0:
            # [0:a]视频原声 [1:a]BGM，混合两路
            filter_complex = (
                f"[0:a]volume={video_volume}[v];"
                f"[1:a]volume={audio_volume}[a];"
                f"[v][a]amix=inputs=2:duration=first:dropout_transition=2[out]"
            )
            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", "0:v:0",
                "-map", "[out]",
            ])
        else:
            # 视频原声完全静音，只用BGM
            cmd.extend([
                "-filter_complex", f"[1:a]volume={audio_volume}[out]",
                "-map", "0:v:0",
                "-map", "[out]",
            ])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ])

        # 执行合并
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if process.returncode != 0:
            return jsonify({
                "error": "FFmpeg 合并失败",
                "ffmpeg_stderr": process.stderr[-1000:],
            }), 500

        if not output_path.exists():
            return jsonify({"error": "合并后未生成输出文件"}), 500

        # 返回 JSON，包含下载链接（Coze 插件需要结构化响应）
        video_size = output_path.stat().st_size
        download_url = f"{request.host_url.rstrip('/')}/download/{session_id}"
        return jsonify({
            "status": "success",
            "message": "视频音频合并成功",
            "task_id": session_id,
            "video_size_mb": round(video_size / (1024 * 1024), 2),
            "download_url": download_url,
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "合并超时，视频可能过大"}), 500
    except Exception as e:
        return jsonify({"error": f"处理异常: {str(e)}"}), 500
    finally:
        # 清理临时文件（可选，保留方便调试）
        pass


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
