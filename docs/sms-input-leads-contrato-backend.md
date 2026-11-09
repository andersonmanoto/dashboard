# Contrato do backend — Input de leads (SlickText)

As tabelas e as funções deste contrato já estão no projeto Supabase `eagomlcnhjpofqunbedf` (migration `0112_slicktext_input_leads.sql`).

Documento para implementar o consumidor do input de leads em lote. O painel e a edge **não** escolhem nem enviam os contatos. Eles criam a lista, gravam uma linha de controle e fazem um POST curto. O backend reúne os leads, preenche a fila e envia para a SlickText.

Conexão: usar a **service role** do projeto Supabase `eagomlcnhjpofqunbedf`. Nunca usar a anon key. A service role ignora RLS e é a única que pode executar `sms_input_leads` e `sms_input_refresh_status`.

## O que o backend não faz

- Não cria lista na SlickText e não insere em `slicktext_input_lists`.
- Não cria a linha em `slicktext_inputs`. Ela já existe com `status = 'pending'` quando o POST chega.
- Não escolhe os eventos na mão. Chama `sms_input_leads(input_id)`. A função aplica os filtros, pega um evento por telefone e respeita a chave única.
- Não lê a API key no payload. Lê com a RPC `get_slicktext_account_api_key(slick_account_id)`.
- Não apaga linha da fila quando a Abstract recusa. Marca `failed` e preenche `last_error`. O telefone continua ocupando a lista.

## Tabelas

### `slicktext_input_lists` (só leitura para o backend)

| Coluna | Tipo | Uso |
|---|---|---|
| `id` | uuid | `input_list_id` do payload |
| `slick_account_id` | uuid | Conta. Daqui sai o `brand_id` |
| `list_id` | text | `contact_list_id` da lista na SlickText. Destino do envio |
| `name` | text | Nome exibido |
| `created_at` | timestamptz | |

### `slicktext_inputs` (controle: o backend atualiza status)

Uma linha por input. O front lê esta tabela para mostrar o andamento. A edge não fica esperando o POST.

| Coluna | Tipo | Quem escreve | Significado |
|---|---|---|---|
| `id` | uuid | edge | `input_id` do payload |
| `input_list_id` | uuid | edge | Lista de destino |
| `start_date`, `end_date` | date | edge | Período inclusivo. Mínimo 1 dia (`start_date = end_date`) |
| `network_id` | uuid, null | edge | Plataforma. Null = todas |
| `product_id` | uuid, null | edge | Produto. Null = todos |
| `quantity` | int, null | edge | Potes (`checkouts.quantity`). Null = todos |
| `funnel_number` | int, null | edge | Número do funil. Null = todos |
| `enqueued_qty` | int, null | função `sms_input_leads` | Quantos leads deste input estão na fila. Null até a reunião terminar |
| `status` | text | edge cria `pending`; backend avança | `pending` → `processing` → `done` ou `error` |
| `last_error` | text, null | backend, só em `error` | Motivo do input inteiro. Não é o motivo de um lead |
| `started_at`, `finished_at` | timestamptz | função / backend | |

Há no máximo um input `pending` ou `processing` por lista.

`error` é falha do input inteiro (conta inativa, lista inexistente, falha ao reunir os leads). Lead barrado pela Abstract **não** torna o input `error`. O input termina `done` e a linha da fila fica `failed`.

### `slicktext_input_queue` (fila: o backend preenche via função e depois atualiza cada linha)

| Coluna | Tipo | Significado |
|---|---|---|
| `input_id` | uuid | Input que originou a linha. PK junto com `event_id` |
| `input_list_id` | uuid | Lista. Igual à do input |
| `event_id` | uuid | Evento em `events` de onde saem nome e e-mail. PK junto com `input_id` |
| `customer_phone` | text | Telefone **já normalizado** (só dígitos). É o valor a enviar |
| `status` | text | `pending`, `synced`, `failed`, `retry` |
| `attempts` | int | Tentativas de envio. Começa em 0 |
| `last_error` | text, null | Motivo da linha. Ver vocabulário abaixo |
| `created_at`, `updated_at` | timestamptz | |

Chave única: `(input_list_id, customer_phone)`. O mesmo telefone não entra duas vezes na mesma lista, mesmo com `event_id` diferente. O mesmo telefone pode existir em outra lista. Eventos diferentes do mesmo telefone **não** geram duas linhas.

`customer_phone` da fila não é cópia crua de `events.customer_phone`. A função grava o resultado de `normalize_phone` (remove tudo que não é dígito).

## Regras fixas dos leads

A função já aplica. O payload não traz estes campos:

- `events.action_type = 'neworder'`
- `events.is_test` é falso ou nulo
- `lower(trim(events.funnel_stage)) = 'purchase'`
- telefone com pelo menos um dígito
- um evento por telefone: o mais antigo do período (`event_date`, `event_time`, `created_at`)
- se `quantity` não for null, `checkouts.quantity` (join `events.checkout_id`) precisa ser igual. Evento sem checkout fica de fora quando há filtro de potes

## Payload

`POST` com `Content-Type: application/json` e header `X-API-KEY`.

Responder `202` assim que o corpo for aceito. Não esperar a SlickText.

```json
{
  "input_id": "5b1f0c2e-7a11-4c2e-9d01-000000000001",
  "input_list_id": "9a7d3e10-1b22-4d33-8e44-000000000002",
  "start_date": "2026-10-01",
  "end_date": "2026-10-15",
  "network_id": "11111111-1111-1111-1111-111111111111",
  "product_id": "22222222-2222-2222-2222-222222222222",
  "quantity": 3,
  "funnel_number": 1
}
```

Exemplo sem filtros opcionais (todas as plataformas, todos os produtos, todos os potes, todos os funis, um único dia):

```json
{
  "input_id": "5b1f0c2e-7a11-4c2e-9d01-000000000001",
  "input_list_id": "9a7d3e10-1b22-4d33-8e44-000000000002",
  "start_date": "2026-10-01",
  "end_date": "2026-10-01",
  "network_id": null,
  "product_id": null,
  "quantity": null,
  "funnel_number": null
}
```

Regras:

- `input_id` é a chave de idempotência. Processar só se `slicktext_inputs.status = 'pending'`. Se já estiver `processing` ou `done`, responder `202` e não começar de novo.
- `start_date` e `end_date` são obrigatórios e inclusivos, sobre `events.event_date`.
- `network_id`, `product_id`, `quantity` e `funnel_number` ausentes ou `null` significam sem restrição.
- A fonte de verdade dos filtros é a linha em `slicktext_inputs`, não o JSON. `sms_input_leads` lê a linha. O JSON serve para log e conferência.
- Não há campo de quantidade de leads. O período é o recorte. Para menos leads, o usuário encurta as datas.

Resposta de aceite:

```json
{ "accepted": true, "input_id": "5b1f0c2e-7a11-4c2e-9d01-000000000001" }
```

## Sequência depois do POST

### 1. Aceitar

Validar a API key e o JSON. Ler `slicktext_inputs` por `input_id`.

- Não existe, ou `input_list_id` do JSON difere da linha: responder `400`. Não escrever.
- `status` é `processing` ou `done`: responder `202` e parar.
- `status` é `error`: responder `409` e parar.
- `status` é `pending`: responder `202` e seguir em segundo plano.

### 2. Resolver o destino (só leitura)

```text
slicktext_inputs.input_list_id
  -> slicktext_input_lists.slick_account_id
  -> slicktext_input_lists.list_id          -- contact_list_id na SlickText
  -> slicktext_accounts.brand_id
  -> slicktext_accounts.status
```

API key:

```sql
select public.get_slicktext_account_api_key(:slick_account_id);
```

Se a conta não estiver `active` (comparar em minúsculas; `ativa` também vale), se não houver `brand_id`, se a RPC da chave falhar, ou se a lista não existir mais na SlickText: ir ao passo 6. Não chamar `sms_input_leads`.

### 3. Reunir os leads

```sql
select public.sms_input_leads(:input_id);
```

A função, nesta ordem:

1. Trava a linha do input (`FOR UPDATE`).
2. Se já está `done`, ou `processing` com `enqueued_qty` preenchido, devolve esse número e não insere de novo.
3. Marca `slicktext_inputs.status = 'processing'` e `started_at`.
4. Insere em `slicktext_input_queue` uma linha por telefone, com `status = 'pending'`, `attempts = 0`, `last_error = null`, `event_id` do evento mais antigo e `customer_phone` normalizado.
5. Telefone que já está na lista é descartado pelo banco (`unique (input_list_id, customer_phone)`), inclusive se veio de outro input.
6. Grava `enqueued_qty` com a quantidade de linhas deste `input_id`. Se for 0, marca o input `done` e preenche `finished_at`.

Se o retorno for `0`, encerrar. Não há o que enviar.

### 4. Enviar a fila

Ler as linhas deste `input_id` com `status in ('pending', 'retry')`. Travar com `FOR UPDATE SKIP LOCKED` para dois workers não pegarem a mesma linha.

Para cada linha:

1. Ler `events` por `event_id` (nome e e-mail). Usar `slicktext_input_queue.customer_phone`, não o telefone cru do evento.
2. Aplicar a mesma lógica do fluxo atual da `slicktext_sync_queue`: região, Abstract, criar ou localizar o contato na marca e adicionar à lista `list_id`.
3. Atualizar **a própria linha**:

| Resultado | `status` | `attempts` | `last_error` |
|---|---|---|---|
| Entrou na lista da SlickText | `synced` | +1 | `null` |
| Abstract recusou | `failed` | +1 | `rejeitado_abstract` |
| Telefone inválido | `failed` | +1 | `telefone_invalido` |
| Região não suportada | `failed` | +1 | `regiao_nao_suportada` |
| Lista não existe na plataforma | `failed` | +1 | `lista_nao_encontrada` |
| Erro temporário da API SlickText | `retry` | +1 | `slicktext_api_error` |
| `retry` passou do limite de tentativas | `failed` | mantém | `slicktext_api_error` |

Processar em lotes e respeitar o limite de requisições da Abstract e da SlickText. Um input pode ter dezenas de milhares de linhas.

Não repor lead barrado com outro lead. Quem foi `failed` permanece na lista e não entra de novo.

### 5. Encerrar o input

Depois de cada lote:

```sql
select public.sms_input_refresh_status(:input_id);
```

Se não restam linhas `pending` ou `retry` e `enqueued_qty` já está preenchido, a função marca `status = 'done'` e `finished_at`. Linhas `failed` não impedem `done`.

### 6. Falha do input inteiro

Quando o input não pode continuar (conta inativa, lista inexistente, exceção em `sms_input_leads`):

```sql
update public.slicktext_inputs
set status = 'error',
    last_error = :motivo,
    finished_at = now()
where id = :input_id
  and status in ('pending', 'processing');
```

Não apagar as linhas já inseridas na fila. Um processamento futuro pode retomá-las se o status voltar a ser tratado. Neste contrato, `error` não é reprocessado automaticamente.

`last_error` do input é texto curto e estável, por exemplo `conta_inativa`, `lista_nao_encontrada`, `falha_ao_reunir_leads`. Não colocar telefone nem API key.

## Rede de segurança

Rodar em intervalo fixo, independente do POST:

- Inputs `pending` com `created_at` antigo (o POST pode ter falhado depois de a edge gravar a linha): tratar como um POST novo, a partir do passo 2.
- Inputs `processing` sem atualização recente e com linhas `pending` ou `retry`: retomar o passo 4. `sms_input_leads` é seguro de chamar de novo: se `enqueued_qty` já existe, não reinsere.

## Vocabulário de `last_error` da fila

Usar os mesmos códigos da `slicktext_sync_queue`, para o restante do sistema continuar reconhecendo:

- `rejeitado_abstract`
- `telefone_invalido`
- `regiao_nao_suportada`
- `lista_nao_encontrada`
- `slicktext_api_error`

Não inventar variação com maiúsculas ou mensagem livre no lugar do código. Detalhe extra, se precisar, fica em log, não nesta coluna.

## Funções disponíveis

| Função | Quem chama | O que faz |
|---|---|---|
| `sms_input_leads(uuid) returns int` | backend, service role | Reúne os leads e devolve `enqueued_qty` |
| `sms_input_refresh_status(uuid) returns text` | backend, service role | Marca `done` quando a fila do input fechou |
| `get_slicktext_account_api_key(uuid) returns text` | backend, service role | API key da conta. Não logar o retorno |
| `normalize_phone(text) returns text` | já usada pelas funções | Só dígitos |
| `sms_lead_estimate(...)` | front | Contagens. O backend não precisa |
| `sms_input_overlaps(...)` | front | Aviso de período já usado. O backend não precisa |
| `sms_input_counters(uuid[])` | front | Contagem por status, por input |
| `sms_input_list_counters(uuid)` | front | Contagem por status, por lista da conta |

## Checklist de implementação

1. Validar `X-API-KEY` e responder `202` sem esperar o envio.
2. Ignorar POST repetido quando o input não está `pending`.
3. Resolver conta, `brand_id`, `list_id` e API key antes de reunir leads.
4. Chamar `sms_input_leads` uma vez e usar o inteiro retornado.
5. Atualizar cada linha da fila para `synced`, `failed` ou `retry` com o `last_error` do vocabulário.
6. Chamar `sms_input_refresh_status` ao fim de cada lote.
7. Marcar o input `error` só em falha estrutural, com `last_error` sem PII.
8. Ter a varredura de `pending` antigos e de `retry`.
9. Não inserir direto em `slicktext_input_queue` e não criar lista.
