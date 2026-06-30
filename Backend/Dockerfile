FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY Backend/app ./app
COPY Backend/prompts ./prompts
COPY Backend/tests ./tests

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
