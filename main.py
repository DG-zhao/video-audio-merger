"""
瑙嗛闊抽鍚堝苟鎻掍欢 鈥?Coze Plugin
鍔熻兘锛氫笅杞借棰戝拰闊抽鏂囦欢锛屼娇鐢?FFmpeg 鍚堝苟鍚庤繑鍥炲甫闊宠建鐨勮棰戙€?"""

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

# 宸ヤ綔鐩綍
WORK_DIR = Path(__file__).parent / "work"
WORK_DIR.mkdir(exist_ok=True)

# 鏈€澶ф枃浠跺ぇ灏忥紙500MB锛?MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024

# 寮傛浠诲姟鐘舵€佸瓨鍌?_task_status: dict = {}  # task_id -> {"status": "processing|success|error", "data": ...}


def find_ffmpeg():
    """鏌ユ壘 ffmpeg 璺緞"""
    # 浼樺厛浠庣幆澧冨彉閲忔壘
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path
    # 甯歌 Windows 瀹夎璺緞锛堝惈 winget 瀹夎璺緞锛?    candidates = [
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path.home() / "ffmpeg/bin/ffmpeg.exe",
        # winget 瀹夎璺緞
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe",
    ]
    # 鑷姩鎼滅储 WinGet 鐩綍涓嬬殑 ffmpeg锛堢増鏈彿鍙兘鍙樺寲锛?    winget_base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    if winget_base.exists():
        for d in winget_base.iterdir():
            if "FFmpeg" in d.name or "ffmpeg" in d.name:
                bin_dir = d / "bin"
                if bin_dir.exists():
                    exe = bin_dir / "ffmpeg.exe"
                    if exe.exists():
                        candidates.insert(0, str(exe))
                # 涔熸悳绱㈠瓙鐩綍
                for sub in d.rglob("ffmpeg.exe"):
                    candidates.insert(0, str(sub))
    for p in candidates:
        p = Path(p)
        if p.exists():
            return str(p)
    return None


def find_ffprobe():
    """鏌ユ壘 ffprobe 璺緞"""
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
    瑙ｆ瀽 storage.to 鍒嗕韩閾炬帴锛岃繑鍥?stusercontent.com 鐩撮摼銆?    濡傛灉閾炬帴涓嶆槸 storage.to 鎴栬В鏋愬け璐ワ紝杩斿洖鍘?URL銆?    """
    try:
        parsed = urlparse(share_url)
        if "storage.to" not in parsed.netloc:
            return share_url  # 涓嶆槸 storage.to锛屼笉澶勭悊

        # 鑾峰彇鍒嗕韩椤甸潰
        resp = requests.get(share_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        html = resp.text

        # 鎻愬彇 stusercontent.com 鐩撮摼锛堟帓闄?thumbnails 璺緞锛?        # 鏍煎紡: stusercontent.com/<uuid>?expires=...&sig=...
        pattern = r'stusercontent\.com/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\?[^"\'<>\s]+)'
        matches = re.findall(pattern, html)
        if not matches:
            return share_url  # 娌℃壘鍒扮洿閾撅紝淇濇寔鍘?URL

        # 鍙栫涓€涓尮閰嶏紙鎺掗櫎閲嶅鐨?\u0026 鐗堟湰锛?        dirty_url = matches[0]
        # 娓呯悊 \u0026 鍜?&amp; 鈫?&
        clean_url = dirty_url.replace("\\u0026", "&").replace("&amp;", "&")
        direct_url = f"https://stusercontent.com/{clean_url}"

        return direct_url
    except Exception:
        return share_url  # 瑙ｆ瀽澶辫触锛岀敤鍘?URL 灏濊瘯涓嬭浇


def download_file(url: str, dest: str) -> tuple:
    """涓嬭浇鏂囦欢锛屽甫澶у皬闄愬埗鍜岃繘搴︺€傝繑鍥?(success, error_message)"""
    actual_url = url
    try:
        # 鑷姩瑙ｆ瀽 storage.to 绛夊垎浜摼鎺ヤ负鐩撮摼
        actual_url = resolve_storage_to_url(url)
        if actual_url != url:
            print(f"[INFO] 宸茶В鏋?storage.to 鐩撮摼: {actual_url[:80]}...")

        resp = requests.get(actual_url, stream=True, timeout=300, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

        # 妫€鏌?Content-Type锛岄伩鍏嶄笅杞藉埌 HTML 椤甸潰
        content_type = resp.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            return False, "閾炬帴杩斿洖鐨勬槸缃戦〉鑰屼笉鏄枃浠讹紝璇蜂娇鐢ㄧ洿閾捐€屼笉鏄垎浜〉闈㈤摼鎺?

        total = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > MAX_DOWNLOAD_SIZE:
                    return False, f"鏂囦欢瓒呰繃澶у皬闄愬埗锛坽MAX_DOWNLOAD_SIZE // (1024*1024)}MB锛?
                f.write(chunk)

        if total < 1000:
            return False, "涓嬭浇鍒扮殑鏂囦欢澶皬锛堝彲鑳介摼鎺ュ凡杩囨湡鎴栦笉鏄湁鏁堝獟浣撴枃浠讹級"

        return True, ""
    except requests.exceptions.HTTPError as e:
        return False, f"HTTP 閿欒锛坽e.response.status_code}锛夛細鏂囦欢涓嶅瓨鍦ㄦ垨閾炬帴宸茶繃鏈?
    except requests.exceptions.ConnectionError:
        return False, "鏃犳硶杩炴帴鍒版湇鍔″櫒锛岃妫€鏌ラ摼鎺ユ槸鍚︽湁鏁?
    except requests.exceptions.Timeout:
        return False, "涓嬭浇瓒呮椂锛屾枃浠跺彲鑳借繃澶ф垨閾炬帴鏃犳晥"
    except Exception as e:
        return False, f"涓嬭浇寮傚父: {str(e)}"


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
    """Coze 鎻掍欢鎵€闇€鐨?OpenAPI 瑙勮寖"""
    base_url = request.host_url.rstrip("/")
    return jsonify({
        "openapi": "3.0.0",
        "info": {
            "title": "瑙嗛闊抽鍚堝苟鎻掍欢",
            "description": "灏嗚棰戞枃浠朵笌闊抽鏂囦欢鍚堝苟涓哄甫闊宠建鐨勮棰戯紝鏀寔 mp4/mov/webm 瑙嗛鍜?mp3/wav/aac 闊抽",
            "version": "1.0.0",
        },
        "servers": [{"url": base_url}],
        "paths": {
            "/merge": {
                "post": {
                    "summary": "鍚堝苟瑙嗛鍜岄煶棰?,
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
                                            "description": "瑙嗛鏂囦欢鐨?URL 鍦板潃",
                                        },
                                        "audioUrl": {
                                            "type": "string",
                                            "description": "闊抽鏂囦欢鐨?URL 鍦板潃锛堣儗鏅煶涔?閰嶉煶锛?,
                                        },
                                        "audioVolume": {
                                            "type": "number",
                                            "description": "闊抽闊抽噺锛?.0-2.0锛岄粯璁?1.0锛堝師濮嬮煶閲忥級锛?.5 涓轰竴鍗?,
                                            "default": 1.0,
                                        },
                                        "videoVolume": {
                                            "type": "number",
                                            "description": "鍘熻棰戦煶閲忥紝0.0-2.0锛岄粯璁?0.3锛堝帇浣庡師澹扮獊鍑築GM锛夛紝璁句负 0 鍒欏畬鍏ㄩ潤闊?,
                                            "default": 0.3,
                                        },
                                        "loopAudio": {
                                            "type": "boolean",
                                            "description": "闊抽鏄惁寰幆鎾斁浠ュ尮閰嶈棰戦暱搴?,
                                            "default": True,
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "鍚堝苟鎴愬姛锛岃繑鍥炰笅杞戒俊鎭?,
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
                            "description": "鍙傛暟閿欒",
                        },
                        "500": {
                            "description": "鍚堝苟澶辫触",
                        },
                    },
                }
            }
        },
    })


def _do_merge(session_id: str, video_url: str, audio_url: str,
              audio_volume: float, video_volume: float, loop_audio: bool,
              base_url: str):
    """鍚庡彴绾跨▼鎵ц鍚堝苟浠诲姟"""
    _task_status[session_id] = {"status": "processing", "message": "寮€濮嬪鐞?.."}
    
    session_dir = WORK_DIR / session_id
    try:
        # 鎺ㄦ柇鏂囦欢鎵╁睍鍚?        video_ext = os.path.splitext(urlparse(video_url).path)[1] or ".mp4"
        audio_ext = os.path.splitext(urlparse(audio_url).path)[1] or ".mp3"
        video_path = session_dir / f"input{video_ext}"
        audio_path = session_dir / f"audio{audio_ext}"
        output_path = session_dir / "output.mp4"

        # 涓嬭浇瑙嗛
        _task_status[session_id]["message"] = "姝ｅ湪涓嬭浇瑙嗛..."
        ok, err = download_file(video_url, str(video_path))
        if not ok:
            _task_status[session_id] = {"status": "error", "message": f"瑙嗛涓嬭浇澶辫触: {err}"}
            return

        # 涓嬭浇闊抽
        _task_status[session_id]["message"] = "姝ｅ湪涓嬭浇闊抽..."
        ok, err = download_file(audio_url, str(audio_path))
        if not ok:
            _task_status[session_id] = {"status": "error", "message": f"闊抽涓嬭浇澶辫触: {err}"}
            return

        # 鑾峰彇瑙嗛鏃堕暱
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

        # 鏌ユ壘 ffmpeg
        ffmpeg = find_ffmpeg()

        # 鏋勫缓 FFmpeg 鍛戒护
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

        # 鎵ц鍚堝苟
        _task_status[session_id]["message"] = "姝ｅ湪鍚堝苟瑙嗛鍜岄煶棰?.."
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if process.returncode != 0:
            _task_status[session_id] = {
                "status": "error",
                "message": "FFmpeg 鍚堝苟澶辫触",
                "ffmpeg_stderr": process.stderr[-500:],
            }
            return

        if not output_path.exists():
            _task_status[session_id] = {"status": "error", "message": "鍚堝苟鍚庢湭鐢熸垚杈撳嚭鏂囦欢"}
            return

        video_size = output_path.stat().st_size
        download_url = f"{base_url}/download/{session_id}"
        _task_status[session_id] = {
            "status": "success",
            "message": "瑙嗛闊抽鍚堝苟鎴愬姛",
            "task_id": session_id,
            "video_size_mb": round(video_size / (1024 * 1024), 2),
            "download_url": download_url,
        }

    except subprocess.TimeoutExpired:
        _task_status[session_id] = {"status": "error", "message": "鍚堝苟瓒呮椂锛岃棰戝彲鑳借繃澶?}
    except Exception as e:
        _task_status[session_id] = {"status": "error", "message": f"澶勭悊寮傚父: {str(e)}"}


@app.route("/merge", methods=["POST"])
def merge_video_audio():
    """寮傛鍚堝苟瑙嗛鍜岄煶棰戯紙绔嬪嵆杩斿洖锛屽悗鍙板鐞嗭級"""
    data = request.get_json(silent=True) or {}

    video_url = data.get("videoUrl", "").strip()
    audio_url = data.get("audioUrl", "").strip()

    if not video_url or not audio_url:
        return jsonify({"error": "videoUrl 鍜?audioUrl 涓哄繀濉弬鏁?}), 400

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return jsonify({"error": "鏈嶅姟鍣ㄦ湭瀹夎 FFmpeg"}), 500

    audio_volume = max(0.0, min(2.0, float(data.get("audioVolume", 1.0))))
    video_volume = max(0.0, min(2.0, float(data.get("videoVolume", 0.3))))
    loop_audio = data.get("loopAudio", True)

    session_id = uuid.uuid4().hex[:8]
    session_dir = WORK_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    base_url = request.host_url.rstrip("/")

    # 鍚姩鍚庡彴绾跨▼
    t = threading.Thread(
        target=_do_merge,
        args=(session_id, video_url, audio_url, audio_volume, video_volume, loop_audio, base_url),
        daemon=True,
    )
    t.start()

    # 绔嬪嵆杩斿洖
    return jsonify({
        "status": "processing",
        "message": "浠诲姟宸叉帴鏀讹紝姝ｅ湪鍚庡彴澶勭悊",
        "task_id": session_id,
        "status_url": f"{base_url}/status/{session_id}",
    })


@app.route("/status/<task_id>", methods=["GET"])
def task_status(task_id):
    """鏌ヨ寮傛浠诲姟鐘舵€?""
    info = _task_status.get(task_id)
    if not info:
        return jsonify({"error": "浠诲姟涓嶅瓨鍦ㄦ垨宸茶繃鏈?}), 404
    return jsonify(info)


@app.route("/download/<session_id>", methods=["GET"])
def download_video(session_id):
    """涓嬭浇鍚堝苟鍚庣殑瑙嗛鏂囦欢"""
    output_path = WORK_DIR / session_id / "output.mp4"
    if not output_path.exists():
        return jsonify({"error": "鏂囦欢涓嶅瓨鍦ㄦ垨宸茶繃鏈?}), 404
    return send_file(
        str(output_path),
        mimetype="video/mp4",
        as_attachment=True,
        download_name="merged_video.mp4",
    )


if __name__ == "__main__":
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        print(f"[OK] FFmpeg 宸叉壘鍒? {ffmpeg}")
    else:
        print("[WARN] 鏈壘鍒?FFmpeg锛岃瀹夎: apt-get install ffmpeg")
    port = int(os.environ.get("PORT", 8899))
    print(f"[INFO] 鏈嶅姟鍚姩: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
