# 🚀 Guia de Deploy: Podman Rootless + Systemd (Quadlets)

Este documento descreve o processo passo a passo para configurar e atualizar o ambiente de produção no **AlmaLinux 9** utilizando **Podman Rootless** (sem privilégios de root) gerenciado nativamente pelo **Systemd**.

---

## 1. Acesso ao Servidor

Acesse o servidor via SSH utilizando o usuário `root`.

```bash
ssh root@webhook.tigeroffers.com
# Ou alternativamente pelo IP:
# ssh root@31.97.6.214

## Pré-requisitos do Servidor

```bash
# Instalar pacotes essenciais
dnf install -y podman podman-compose nginx git policycoreutils-python-utils

# (Opcional) Instalar Certbot para SSL
dnf install -y certbot python3-certbot-nginx
```

## 2. Configuração do Usuário: dashboard# 🚀 Guia de Deploy: Podman Rootless + Systemd (Quadlets)

Este documento descreve o processo passo a passo para configurar e atualizar o ambiente de produção no **AlmaLinux 9** utilizando **Podman Rootless** (sem privilégios de root) gerenciado nativamente pelo **Systemd**.

---

## 1. Acesso ao Servidor

Acesse o servidor via SSH utilizando o usuário `root`.

```bash
ssh root@webhook.tigeroffers.com
# Ou alternativamente pelo IP:
# ssh root@31.97.6.214

Por segurança, não rodamos os containers como root. Criamos um usuário dedicado e configuramos o sistema para manter os serviços dele rodando mesmo após o logout.

```bash
# 1. Criar o usuário
useradd dashboard

# 2. Permitir que os serviços iniciem no boot (CRÍTICO)
loginctl enable-linger dashboard

# 3. Permitir que o usuário leia logs do sistema
usermod -aG systemd-journal dashboard
```

## 3. Instalação do Código (Como usuário dashboard)

Agora, saia do root e logue como o usuário da aplicação: `su - dashboard`

```bash
# 1. Clonar o repositório
git clone https://github.com/andersonmanoto/dashboard.git /opt/dashboard
# (Se der erro de permissão na pasta /opt, execute o comando logado como root: chown -R dashboard:dashboard dashboard)

cd /opt/dashboard

# 2. Configurar variáveis de ambiente
cp .env.example .env
nano .env # Edite com as credenciais reais de produção
```

## 4. Configuração dos Quadlets (Systemd)

O Podman moderno usa arquivos `.container` para gerar serviços Systemd automaticamente.

Crie o diretório de configuração:

```bash
mkdir -p ~/.config/containers/systemd/
cd ~/.config/containers/systemd/
```

Crie os 5 arquivos abaixo dentro desta pasta:

### 4.1. Rede (`tiger.network`)

```ini
[Unit]
Description=Rede Tiger Dashboard

[Network]
NetworkName=tiger_network
Driver=bridge
```

### 4.2. Volume (`redis_data.volume`)

```ini
[Unit]
Description=Volume Persistente Redis

[Volume]
VolumeName=redis_data
```

### 4.3. Banco de Dados (`dashhook-redis.container`)

```ini
[Unit]
Description=Redis Container
After=network-online.target

[Container]
Image=docker.io/library/redis:7-alpine
ContainerName=dashhook-redis
Network=tiger.network
NetworkAlias=redis
Volume=redis_data.volume:/data:Z
PublishPort=6379:6379
Exec=redis-server --save 60 1 --loglevel warning --shutdown-timeout 30

[Install]
WantedBy=default.target
```

### 4.4. API (`dashhook-api.container`)

```ini
[Unit]
Description=Dashboard API
After=dashhook-redis.service
Requires=dashhook-redis.service

[Container]
Image=localhost/dashhook/api:2.3.0
ContainerName=dashhook-api
Network=tiger.network
EnvironmentFile=/opt/dashboard/.env
Environment=PYTHONPATH=/app:/app/app
Environment=REDIS_HOST=redis
PublishPort=8000:8000
Exec=uvicorn app.main:app --host 0.0.0.0 --port 8000

[Install]
WantedBy=default.target
```

### 4.5. Worker (`dashhook-worker.container`)

```ini
[Unit]
Description=Dashboard Worker
After=dashhook-redis.service
Requires=dashhook-redis.service

[Container]
Image=localhost/dashhook/worker:2.3.0
ContainerName=dashhook-worker
Network=tiger.network
EnvironmentFile=/opt/dashboard/.env
Environment=PYTHONPATH=/app:/app/app
Environment=REDIS_HOST=redis
Exec=arq app.worker.WorkerSettings

[Install]
WantedBy=default.target
```

## 5. Build e Start (Como usuário dashboard)

Com os arquivos de configuração prontos, vamos construir as imagens e iniciar o sistema.

```bash
# 1. Construir as imagens locais
cd /opt/dashboard

# Usamos o compose apenas para facilitar o build, mas quem roda é o Systemd
podman-compose build

# 2. (IMPORTANTE) Taggear as imagens para o nome que o Systemd espera
# O Systemd espera "localhost/dashhook/api:2.3.0"
podman tag dashboard_api:latest localhost/dashhook/api:2.3.0
podman tag dashboard_worker:latest localhost/dashhook/worker:2.3.0

# 3. Recarregar o Systemd para ler os arquivos novos
systemctl --user daemon-reload

# 4. Iniciar os serviços
systemctl --user enable --now dashhook-api
systemctl --user enable --now dashhook-worker
# (O Redis sobe sozinho por dependência)

# 5. Verificar status
systemctl --user status dashhook-api
```

## 6. Configuração do Proxy Nginx (Como ROOT)

O container roda na porta 8000. O Nginx expõe na 80/443 com SSL.

**Arquivo:** `/etc/nginx/conf.d/webhook.conf`

```nginx
server {
    listen 80;
    server_name webhook.seu-dominio.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name webhook.seu-dominio.com;

    # Certificados (ajuste o caminho após rodar o certbot)
    ssl_certificate /etc/letsencrypt/live/webhook.seu-dominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/webhook.seu-dominio.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Websocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 🔄 Fluxo de Atualização (CI/CD Manual)

Quando você fizer alterações no código e der `git pull`, siga estes passos para atualizar sem downtime:

```bash
# 1. Baixar código novo
cd /opt/dashboard
git pull origin main

# 2. Reconstruir imagens
podman-compose build
podman tag dashboard_api:latest localhost/dashhook/api:2.3.0
podman tag dashboard_worker:latest localhost/dashhook/worker:2.3.0

# 3. Reiniciar serviços (O Systemd gerencia a troca)
systemctl --user restart dashhook-api
systemctl --user restart dashhook-worker

# 4. Limpar lixo (opcional)
podman image prune -f
```

## 🛠 Comandos Úteis

```bash
# Ver logs da API em tempo real
journalctl --user -fu dashhook-api

# Ver logs do Worker
journalctl --user -fu dashhook-worker

# Ver logs do Redis
journalctl --user -fu dashhook-redis

# Parar tudo
systemctl --user stop dashhook-api dashhook-worker dashhook-redis
```