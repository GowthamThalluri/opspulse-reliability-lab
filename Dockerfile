
# 1. Use a lightweight official Python base image
FROM python:3.13-slim

# 2. Set Python runtime behavior
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Set the working directory
WORKDIR /app

# 4. Create a non-root application user
RUN groupadd --system --gid 10001 app && \
    useradd --system --uid 10001 --gid app --no-create-home app

# 5. Copy dependencies first for better build caching
COPY requirements.txt .

# 6. Install application dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 7. Copy application source code
COPY --chown=app:app app/ ./app/

# 8. Run the application as a non-root user
USER app

# 9. Document the application port
EXPOSE 8000

# 10. Check application health inside the container
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

# 11. Start the application server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
