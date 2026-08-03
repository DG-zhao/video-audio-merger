# 视频音频合并插件 — Coze 集成完整指南

## 准备工作

### 1. 启动本地服务 + 公网隧道（测试用）

双击 `start-tunnel.bat`，会依次：
1. 启动 Flask 服务（端口 8899）
2. 通过 SSH 隧道暴露到公网
3. 终端会显示类似这样的地址：
   ```
   Forwarding HTTP traffic from https://xxxx.serveo.net
   ```
   **记下这个地址**，比如 `https://xxxx.serveo.net`

> 注意：隧道依赖当前电脑保持开机和网络连接。长期使用建议部署到云平台（见下方方案二）。

### 2. 验证服务是否正常

浏览器访问 `https://xxxx.serveo.net/health`，应该返回：
```json
{"ffmpeg_available": true, "status": "ok"}
```

访问 `https://xxxx.serveo.net/openapi.json`，应该返回 OpenAPI 规范。

---

## 在 Coze 中配置插件

### 步骤 1：进入插件管理

1. 登录 Coze 控制台
2. 左侧菜单 → **插件**
3. 点击右上角 **创建插件**

### 步骤 2：导入 OpenAPI 规范

**方式一：URL 导入（推荐）**
1. 插件来源选择 **URL 导入**
2. 填入：`https://xxxx.serveo.net/openapi.json`
3. 点击 **导入**

**方式二：手动创建**
如果 URL 导入失败，手动配置：

| 配置项 | 值 |
|---|---|
| 插件名称 | 视频音频合并 |
| 插件描述 | 将视频文件与音频文件（BGM/配音）合并为带音轨的视频 |
| 请求方式 | POST |
| 接口地址 | `https://xxxx.serveo.net/merge` |
| 认证方式 | 无需认证 |

参数配置（手动添加）：

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| videoUrl | string | 是 | - | 视频文件 URL |
| audioUrl | string | 是 | - | 音频文件 URL（BGM/配音） |
| audioVolume | number | 否 | 1.0 | BGM 音量，0.0-2.0 |
| videoVolume | number | 否 | 0.3 | 原视频音量，0.3=压低原声突出BGM |
| loopAudio | boolean | 否 | true | BGM 是否循环匹配视频长度 |

### 步骤 3：测试插件

1. 保存后点击 **测试**
2. 填入测试参数：
   ```json
   {
     "videoUrl": "你的视频URL",
     "audioUrl": "你的BGM音频URL"
   }
   ```
3. 点击发送，等待返回合并后的视频文件

### 步骤 4：在 Coze 工作流中使用

1. 创建工作流
2. 添加 **插件节点** → 选择 **视频音频合并** → **mergeVideoAudio**
3. 将上游生成的视频 URL 和音频 URL 分别连接到 `videoUrl` 和 `audioUrl`
4. 插件输出是合并后的 mp4 视频文件，可直接传给下游节点（如上传到云存储）

---

## 参数使用建议

| 场景 | videoVolume | audioVolume | 效果 |
|---|---|---|---|
| BGM 纯背景音乐（无人声） | 0.2 | 1.0 | BGM 突出，原视频声压低 |
| 原视频有配音 + BGM | 0.5 | 0.4 | 保留原声为主，BGM 微弱铺垫 |
| 原视频完全不要声音 | 0.0 | 1.0 | 纯 BGM，原视频静音 |
| 配音文件 + 视频画面 | 0.0 | 1.0 | 纯配音替换原声 |

---

## 长期部署方案（二选一）

### 方案 A：Railway 部署（简单免费）

1. 把整个 `video-audio-merger/` 目录推送到 GitHub
2. 在 [railway.app](https://railway.app) 中导入 GitHub 仓库
3. Railway 自动识别 Python 项目，添加 `ffmpeg` 到 `packages` 配置
4. 部署后获得 HTTPS 地址 `https://xxx.up.railway.app`
5. 在 Coze 中配置：`https://xxx.up.railway.app/openapi.json`

### 方案 B：Render 部署

1. 在 [render.com](https://render.com) 创建新的 Web Service
2. 连接 GitHub 仓库
3. Build Command: `pip install flask requests`
4. Start Command: `python main.py`
5. 部署后获得 HTTPS 地址
