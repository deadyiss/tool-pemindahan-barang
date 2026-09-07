# Dockerfile untuk deploy "Tool Cek Validitas Barang" ke Google Cloud Run
# (atau platform container lain mana pun -- Render, Railway, dst).
#
# Kenapa perlu Dockerfile (padahal Streamlit Community Cloud tidak butuh)?
# Cloud Run cuma bisa menjalankan container, bukan "baca requirements.txt
# lalu jalankan streamlit run" seperti Streamlit Community Cloud. Dockerfile
# ini yang mendefinisikan "cara menjalankan app" itu.

FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary sudah termasuk libpq versi sendiri, jadi TIDAK perlu
# install libpq-dev/gcc di sini -- image tetap kecil & build tetap cepat.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run selalu kasih tahu port yang harus dipakai lewat env var $PORT
# (biasanya 8080) -- app HARUS listen di port itu, bukan port tetap 8501.
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_ENABLE_CORS=false
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0"]
