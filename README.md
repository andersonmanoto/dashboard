## Como Fazer Deploy (Produção)

Este projeto utiliza **Podman Rootless** gerenciado via **Systemd (Quadlets)** para máxima segurança e estabilidade no AlmaLinux 9.

👉 **[Leia o Guia Completo de Deploy (DEPLOY.md)](DEPLOY.md)** para instruções de configuração do servidor, usuários, permissões e SSL.

### Atualização Rápida (Deploy Contínuo)

Se o ambiente já estiver configurado, para atualizar a versão:

```bash
# 1. Baixar código
git pull

# 2. Buildar novas imagens (Quadlet requer tag específica)
podman-compose build
podman tag dashboard_api:latest localhost/dashhook/api:2.3.0
podman tag dashboard_worker:latest localhost/dashhook/worker:2.3.0

# 3. Reiniciar serviços (Zero Downtime via Systemd)
systemctl --user restart dashhook-api
systemctl --user restart dashhook-worker