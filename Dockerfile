FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py backup.py legacy_core.py recipe_art.py ./
COPY data ./data
COPY public ./public
RUN mkdir -p /data && useradd --uid 10001 --no-create-home miga && chown -R miga:miga /app /data
EXPOSE 8000
# El volumen de Railway se monta como root: ajustamos permisos antes de bajar privilegios.
CMD ["sh", "-c", "chown -R miga:miga /data && exec gosu miga uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
