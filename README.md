# Webhook Normalizer - Refatoração 2.0

## 🎯 Melhorias Implementadas

### 1. **Arquitetura em Camadas**
- **Repositories**: Acesso ao banco de dados (CRUD puro)
- **Services**: Lógica de negócio
- **Models**: Validação e tipagem com Pydantic
- **Utils**: Funções utilitárias reutilizáveis

### 2. **Separação de Responsabilidades**
Cada módulo tem uma responsabilidade única:

```
app/
├── config.py              # Configurações centralizadas
├── dependencies.py        # Dependency Injection
├── main.py                # Rotas da API (limpo!)
│
├── models/
│   ├── schemas.py         # Pydantic models (validação)
│   └── enums.py           # Constantes e enums
│
├── services/
│   ├── normalizer.py      # Normalização de payloads
│   ├── event_processor.py # Lógica de negócio
│   └── slack_service.py   # Notificações
│
├── repositories/
│   └── database.py        # Acesso ao Supabase
│
└── utils/
    └── date_utils.py      # Funções auxiliares
```

### 3. **Tipagem Forte com Pydantic**
- Validação automática de dados
- Type hints em toda a aplicação
- Conversão automática de tipos
- Documentação automática via OpenAPI

### 4. **Dependency Injection**
- Instâncias singleton cacheadas
- Fácil de testar (mock das dependências)
- Injeção automática pelo FastAPI

### 5. **Configurações Centralizadas**
- Todas as configurações em um único lugar
- Validação com pydantic-settings
- Suporte a .env e variáveis de ambiente

### 6. **Enums e Constantes**
- Tipos seguros para networks, actions, etc.
- Mapeamentos centralizados
- Reduz strings mágicas no código

### 7. **Async/Sync Corretamente**
- `async` apenas onde necessário (endpoints)
- Supabase é síncrono (sem async desnecessário)
- Background tasks funcionando corretamente

## 📦 Dependências Necessárias

```txt
fastapi
uvicorn[standard]
python-dotenv
loguru
supabase
slack-sdk
pydantic
pydantic-settings
```

## 🔧 Migração do Código Antigo

### Antes
```python
# Tudo misturado em main.py
async def task_save_event(db: DatabaseService, data: dict):
    # 200 linhas de lógica de negócio...
```

### Depois
```python
# main.py limpo - apenas rotas
@app.post("/buygoods/{secret_token}")
async def webhook_buygoods(...):
    background_tasks.add_task(
        process_webhook_background,
        normalizer,
        processor,
        NetworkType.BUYGOODS,
        payload
    )
```

## 🧪 Testabilidade

A nova estrutura facilita MUITO os testes:

```python
# test_normalizer.py
def test_normalize_buygoods():
    normalizer = PayloadNormalizer()
    payload = {...}
    
    result = normalizer.normalize(NetworkType.BUYGOODS, payload)
    
    assert result.network == NetworkType.BUYGOODS
    assert result.order_id == "12345"
```

## 🚀 Como Usar

### 1. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 2. Configurar .env
```env
WEBHOOK_SECRET=seu_secret_aqui
SUPABASE_URL=https://...
SUPABASE_KEY=eyJ...
SLACK_BOT_TOKEN=xoxb-...
```

### 3. Rodar
```bash
uvicorn app.main:app --reload
```

## 📊 Benefícios

### Manutenibilidade ✅
- Cada arquivo tem < 300 linhas
- Fácil encontrar código específico
- Fácil adicionar novas redes

### Performance ✅
- Singleton pattern para conexões
- Background tasks eficientes
- Sem async desnecessário

### Qualidade ✅
- Tipagem forte evita bugs
- Validação automática de dados
- Erros mais claros

### Escalabilidade ✅
- Fácil adicionar novas features
- Fácil adicionar novos serviços
- Código testável

## 🔄 Mudanças Principais

### DatabaseService → DatabaseRepository
- Removido async desnecessário
- Métodos mais específicos
- Retorna Pydantic models

### Normalização
- Movida para service dedicado
- Usa Pydantic para validação
- Enums para types seguros

### Processamento
- Lógica separada em EventProcessor
- Cada etapa em método próprio
- Mais fácil debugar

### Slack
- Service dedicado
- Melhor separação de concerns
- Fácil adicionar novos tipos de notificação

## 📝 Próximos Passos

1. **Adicionar Testes**: pytest + pytest-asyncio
2. **Adicionar Logging Estruturado**: JSON logs
3. **Adicionar Métricas**: Prometheus/Grafana
4. **Adicionar Rate Limiting**: slowapi
5. **Adicionar Retry Logic**: tenacity
6. **Adicionar Health Checks**: mais detalhados

## ⚠️ Breaking Changes

### Importações
```python
# Antes
from database import get_db

# Depois
from app.dependencies import get_database_repository
```

### Configurações
```python
# Antes
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Depois
settings = get_settings()
secret = settings.webhook_secret
```

### Models
```python
# Antes
data: Dict[str, Any]

# Depois
event: NormalizedEvent
```

## 🎓 Padrões Aplicados

- **Repository Pattern**: Separação de acesso a dados
- **Service Layer**: Lógica de negócio isolada
- **Dependency Injection**: Inversão de controle
- **Strategy Pattern**: Normalizers por rede
- **Singleton Pattern**: Conexões e configs
- **DTO Pattern**: Pydantic models

## 💡 Dicas

### Adicionar Nova Rede
1. Adicionar enum em `enums.py`
2. Adicionar método em `normalizer.py`
3. Adicionar endpoint em `main.py`
4. Adicionar testes

### Debugar
```python
# Adicione breakpoints nos services
def _enrich_checkout(self, event):
    breakpoint()  # debugging
    ...
```

### Adicionar Nova Feature
1. Identificar camada correta (service/repository)
2. Adicionar model se necessário
3. Implementar lógica
4. Atualizar dependencies se necessário