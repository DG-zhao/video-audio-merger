FROM python:3.11-slim

# 安装 FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Render 会自动设置 PORT 环境变量
EXPOSE 10000

CMD ["python", "main.py"]
