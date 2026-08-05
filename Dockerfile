FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LD_LIBRARY_PATH=/opt/dji/thermal-sdk \
    SDK_LIBRARY_PATH=/opt/dji/thermal-sdk/libdirp.so

WORKDIR /app

RUN groupadd --system thermal && useradd --system --gid thermal thermal

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY sdk/tsdk-core/lib/linux/release_x64/ /opt/dji/thermal-sdk/
COPY app.py config.yaml ./
COPY api/ ./api/
COPY config/ ./config/
COPY model/ ./model/
COPY service/ ./service/
COPY sdk/__init__.py sdk/dji_thermal_sdk.py ./sdk/

RUN chown -R thermal:thermal /app /opt/dji/thermal-sdk
USER thermal

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
