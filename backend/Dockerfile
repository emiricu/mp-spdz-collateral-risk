FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
COPY content ./content
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
EXPOSE 9090 9091
CMD ["./entrypoint.sh"]
