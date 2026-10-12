FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY init_db.py app.py ./

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "python init_db.py && streamlit run app.py --server.port=8080 --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false"]
