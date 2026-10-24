#!/usr/bin/env bash
# Reinicia o dashhook-redis quando ele para de responder pela porta do host.
#
# O restart: unless-stopped do compose só age se o container MORRE. Em
# 2026-10-23 o Redis ficou ~15h de pé mas travado (clientes recebendo
# "Connection reset by peer"), derrubando webhooks e workers até restart manual.
# O teste é feito em 127.0.0.1:6379 (mesmo caminho da api/workers), e não via
# podman exec, porque a falha pode estar no repasse de porta e não no Redis.
set -u

CONTAINER=dashhook-redis
TRIES=3

ping_redis() {
    # Sem senha: "-NOAUTH" já prova que o servidor está vivo e respondendo.
    timeout 5 bash -c '
        exec 3<>/dev/tcp/127.0.0.1/6379 || exit 1
        printf "PING\r\n" >&3
        read -r -t 3 reply <&3 || exit 1
        [[ $reply == "+PONG"* || $reply == "-NOAUTH"* ]]
    ' 2>/dev/null
}

for ((i = 1; i <= TRIES; i++)); do
    ping_redis && exit 0
    sleep 5
done

echo "Redis não respondeu em ${TRIES} tentativas, reiniciando ${CONTAINER}"
podman restart --time 10 "$CONTAINER"
