# Imagem Oficial
FROM python:3.12-slim

# Working Directory
WORKDIR /app

# Instação de dependências
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia o requirements.txt (bibliotecas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia geral
COPY . .

# Porta 8000
EXPOSE 8000

# Comando padrão
CMD ["python"]