FROM python:3.13-slim

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/thailex_api /app/thailex_api
COPY backend/queries /app/queries

EXPOSE 8000
CMD ["uvicorn", "thailex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
