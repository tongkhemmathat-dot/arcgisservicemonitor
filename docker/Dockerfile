FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor_backend.py index.html ./

RUN mkdir -p /data

ENV CONFIG_PATH=/data/config.json
ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

VOLUME ["/data"]

CMD ["python", "-u", "monitor_backend.py"]
