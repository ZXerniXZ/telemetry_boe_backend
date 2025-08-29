# Dockerfile per Backend Python FastAPI - Ottimizzato per VPN
FROM python:3.11

# Configura timeout e retry per VPN
ENV PIP_DEFAULT_TIMEOUT=100
ENV PIP_RETRIES=10
ENV PIP_TIMEOUT=100

# Installa nmap prima di installare le dipendenze Python
RUN apt-get update && apt-get install -y nmap && rm -rf /var/lib/apt/lists/*

# Imposta la directory di lavoro
WORKDIR /app

# Copia i file dei requirements
COPY requirements.txt .

# Installa le dipendenze Python con configurazioni VPN-friendly
RUN pip install --no-cache-dir --upgrade pip --timeout 100 --retries 10 && \
    pip install --no-cache-dir --timeout 100 --retries 10 --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

# Copia il codice dell'applicazione
COPY . .

# Espone la porta 8001
EXPOSE 8001

# Comando per avviare l'applicazione
CMD ["uvicorn", "fetch_boe.api_server:app", "--host", "0.0.0.0", "--port", "8001", "--reload"] 