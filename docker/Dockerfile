FROM python:3.12-slim

WORKDIR /app

COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pymavlink_publish.py .

CMD ["python", "pymavlink_publish.py"] 