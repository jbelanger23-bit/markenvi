# desk on a host like Railway. Build context is the desk folder.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DESK_HOST=0.0.0.0 DESK_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8787
CMD ["python", "server.py", "--no-browser"]
