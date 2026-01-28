# Tiger Offers Dashboard & Webhook Handler

Sistema de alta performance para recebimento e processamento de webhooks de vendas e ofertas.

O projeto utiliza o **Inbox Pattern** para garantir 100% de confiabilidade nos dados (zero perda de webhooks) e **Arquitetura de Microsserviços** com Docker para separar a ingestão de dados do processamento pesado.

---

## Arquitetura

O sistema é dividido em dois serviços principais orquestrados via Docker Compose:

1.  **API (`dashhook-api`):**
    * Feita em **FastAPI**.
    * Responsabilidade única: Receber o webhook e salvar no Banco de Dados (PostgreSQL/Supabase) o mais rápido possível.
    * Retorna `200 OK` instantaneamente para a origem.
    * Não processa regras de negócio (para não travar a requisição).

2.  **Worker (`dashhook-worker`):**
    * Script Python com **AsyncIO**.
    * Roda em loop infinito monitorando o banco de dados.
    * Processa as vendas pendentes (envia para Slack, atualiza métricas, etc).
    * Utiliza `asyncio.gather` para processamento paralelo.

### Por que Inbox Pattern?
Em vez de usar filas em memória (como Redis), gravamos o evento cru no banco primeiro. Isso garante que, mesmo se o servidor reiniciar ou faltar luz, o webhook está salvo em disco e será processado quando o sistema voltar.

---

## Como Rodar Localmente (Desenvolvimento)

### Pré-requisitos
* Docker e Docker Compose instalados.
* Git.

### Passo a Passo

1.  **Clone o repositório:**
    ```bash
    git clone <url-do-repo>
    cd dashboard
    ```

2.  **Configure as Variáveis de Ambiente:**
    Crie um arquivo `.env` na raiz (baseado no `.env.example`):
    ```bash
    cp .env.example .env
    ```
    *Preencha com as credenciais do Supabase e Slack.*

3.  **Suba o ambiente:**
    ```bash
    docker compose up -d
    ```
    *Obs: Em desenvolvimento, configuramos "Volumes". Qualquer alteração que você fizer no código `.py` será refletida automaticamente (Hot Reload na API).*

4.  **Acesse:**
    * API: `http://localhost:8000`
    * Docs (Swagger): `http://localhost:8000/docs`

---

## Como Fazer Deploy (Produção)

No servidor de produção, o fluxo é focado em estabilidade e imutabilidade.

### Primeiro Deploy
1.  Instale o Docker e Docker Compose no servidor.
2.  Clone o projeto em `/opt/dashboard`.
3.  Crie o arquivo `.env` de produção com as senhas reais.
4.  Rode: `docker compose up -d --build`.

### Atualizando a Versão (Deploy Contínuo)
Sempre que fizer alterações no código e enviar para o Git, rode estes comandos no servidor:

```bash
# 1. Baixar novidades
git pull origin main

# 2. Reconstruir e reiniciar os containers (Zero Downtime na medida do possível)
docker compose up -d --build

# 3. (Opcional) Limpar imagens antigas para liberar espaço
docker image prune -f