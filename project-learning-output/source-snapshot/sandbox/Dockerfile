FROM python:3.12-slim
RUN pip install --no-cache-dir --timeout 120 --retries 5 pandas==3.0.2 numpy==2.4.3
COPY runner.py /runner.py
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
ENTRYPOINT ["python", "-I", "/runner.py"]
