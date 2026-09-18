# Образ для воспроизводимого запуска инференса и сервиса.
#   docker build -t firewatch .
#   docker run --rm -v /path/to/test:/data -v $(pwd)/out:/out firewatch \
#     python inference.py --data-dir /data --output /out/submission.csv
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

# Зависимости ставятся отдельным слоем: пересборка кода не тянет за собой
# повторную установку колёс rasterio и scikit-learn.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# По умолчанию — справка по инференсу; конкретная команда задаётся при запуске.
CMD ["python", "inference.py", "--help"]
