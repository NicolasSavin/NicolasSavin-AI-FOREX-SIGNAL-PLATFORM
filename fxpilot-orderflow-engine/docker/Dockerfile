FROM python:3.11-slim
WORKDIR /app
COPY . /app/fxpilot-orderflow-engine
ENV PYTHONPATH=/app/fxpilot-orderflow-engine
RUN pip install --no-cache-dir fastapi uvicorn
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
