# 📊 Documentação Base: Cálculos Financeiros e Mapeamento de Payload

Este documento descreve detalhadamente como o sistema consome o payload da plataforma (BuyGoods), como esses dados são guardados e, por fim, como as *functions* SQL calculam cada métrica financeira nas tabelas de snapshot diárias.

---

## 1. Mapeamento: Payload BuyGoods ➔ Banco de Dados

Quando um webhook (neworder, refund, chargeback) chega, o sistema normaliza (através do `PayloadNormalizer` no Python) e salva os dados nas tabelas `events` (para fluxo de caixa de entrada) e `sales_status` (para perdas).

### 1.1. Vendas (Action Type: `neworder`, `sale`, `rebill`)
Estes dados vão para a tabela principal de `events`.

| Campo no Payload BuyGoods | Campo na Tabela `events` | Descrição Financeira |
| :--- | :--- | :--- |
| `total_clean` | `sale_total` | Faturamento bruto da transação (Gross). Usado como base principal. |
| `aff_commission` | `aff_commission` | Valor em dólares pago ao afiliado. |
| `merchant_commission` | `merchant_commission` | Taxa fixa + percentual cobrado pela processadora (BuyGoods). |
| *(Calculado no Python)* | `merchant_commission_rate`| Divisão de `merchant_commission / total_clean` (Ex: 0.08 = 8%). |
| `taxes` | `tax_amount` | Impostos incidentes (registrado, mas não deduzido do Net Revenue). |
| `shipping_cost` | `shipping_cost` | Custo de frete (Fulfillment). |
| `flag_upsell` | `is_upsell` (boolean) | `0` = Front (Aquisição), `1` = Upsell (Backend). |
| `action_type` | `action_type` | Define se é venda comum, recorrência (`rebill`), etc. |
| `is_test` | `is_test` (boolean) | Se `1`, a venda é completamente ignorada nas métricas de receita. |

### 1.2. Perdas (Action Type: `refund`, `chargeback`)
Estes dados geram um registro paralelo na tabela `sales_status`.

| Campo no Payload BuyGoods | Campo em `sales_status` | Descrição Financeira |
| :--- | :--- | :--- |
| `refund_amount` | `amount_affected` | Valor exato estornado ao cliente (pode ser parcial). |
| `total_amount_charged` | `amount_affected` | Valor contestado no chargeback. |
| `comments` | `status_reason` | O motivo da devolução. |
| `date_refunded` (ou `rr_createdate`) | `status_date` | A data real da perda de receita. |

---

## 2. Dicionário de Cálculos (Functions SQL)

As *functions* do banco de dados (ex: `update_daily_sale_snapshot`) agregam os dados brutos e realizam os cálculos finais. Abaixo detalhamos a matemática de cada coluna inserida nos Snapshots.

### 💰 Receita Bruta e Recorrência

* **Gross Revenue (Receita Bruta)**
    * **SQL:** `SUM(sale_total)`
    * **Filtros:** Ação é venda ou rebill (`SALE`, `neworder`, `payment`, `rebill`), não é teste.
    * **Explicação:** É todo o dinheiro que "passou no cartão" do cliente naquele dia.

* **Rebill Amount (Receita de Assinaturas)**
    * **SQL:** `SUM(sale_total) WHERE action_type = 'rebill'`
    * **Explicação:** Separa o faturamento bruto que veio especificamente de recorrências.

### 🛒 Divisão de Funil (Front vs Upsell)

Para o cálculo de conversão e custo de aquisição, o sistema divide rigidamente a entrada da venda.

* **Front Amount (Faturamento de Entrada)**
    * **SQL:** `SUM(sale_total) WHERE is_upsell IS FALSE AND action_type != 'rebill'`
    * **Explicação:** Soma apenas compras do produto principal. Ignora Upsells e Rebills.
* **Total Front (Qtd de Clientes Únicos)**
    * **SQL:** `COUNT(1) WHERE is_upsell IS FALSE AND action_type != 'rebill'`
    * **Explicação:** Contagem de pessoas que entraram na base hoje.
* **Upsell Amount (Faturamento de Backend)**
    * **SQL:** `SUM(sale_total) WHERE is_upsell IS TRUE`
    * **Explicação:** Todo o dinheiro extra gerado por OTOs (One Time Offers).

### 📉 Perdas e Lucro Líquido

* **Refund Amount & Chargeback Amount**
    * **SQL:** `SUM(amount_affected) WHERE status_type = 'refund'` (ou `'chargeback'`) na tabela `sales_status`.
    * **Explicação:** Quantidade de dinheiro que saiu do caixa no dia alvo devido a disputas ou devoluções.

* **Net Revenue (Lucro Líquido)**
    * **Cálculo:** `Gross Revenue` - `Refund Amount` - `Chargeback Amount` - `Total Aff Commission` - `Total Merchant Commission`
    * **Explicação:** Dinheiro real que sobra para a empresa pagar custos fixos (produtos e operações). **Nota:** Impostos (Tax) e Fretes (Shipping) não são subtraídos aqui por regra de negócio.

### 📈 Indicadores Chave de Performance (KPIs)

* **CPA Real (Custo por Aquisição Exato)**
    * **Cálculo:** `Total Affiliate Commission / Total Front`
    * **Explicação:** Mostra quanto, em média, a empresa pagou ao afiliado para trazer **um novo cliente**. Usa-se `Total Front` em vez de `Total Sales` porque Upsells não são novos clientes, são os mesmos clientes comprando mais.

* **Sale AOV (Ticket Médio Global)**
    * **Cálculo:** `Gross Revenue / Total Front`
    * **Explicação:** O valor médio real de cada cliente que entra. Se o Front for $50 e o Gross Total for $100 (graças aos upsells), o AOV daquele cliente é $100.

* **Refund Rate (Taxa de Reembolso)**
    * **Cálculo:** `Total Refunds / Total Sales` (Formatado com 4 casas decimais).
    * **Explicação:** Calculado com base em **quantidade de transações**, e não volume de dinheiro. Mostra a saúde do tráfego.

---

## 3. Regras de Exclusão nas Consultas (Where Clauses)

Os Snapshots aplicam filtros severos para garantir que os dados não sejam poluídos.

1.  **Is Test = False (`e.is_test IS FALSE`)**
    Nenhuma venda marcada como teste no payload entra em lugar nenhum.
2.  **Ignorar Vendas Internas (`aff_id != '0'`)**
    Nos snapshots de *Afiliado*, compras onde o afiliado é o ID '0' (Tiger Offers / Tráfego Orgânico) são excluídas, para não distorcer o ranking e as métricas de parceiros externos.
3.  **Encaminhamento de HelpGrid (`ILIKE '%helpgrid%'`)**
    Existem dois universos de cálculo:
    * `update_daily_affiliate_*`: **Exclui** todos os afiliados cujo nome contém "helpgrid".
    * `update_daily_helpgrid_*`: **Inclui SOMENTE** afiliados cujo nome contém "helpgrid".
    Isso é feito porque as métricas de televendas/recuperação (HelpGrid) são tratadas num painel separado e têm uma dinâmica de conversão completamente diferente do tráfego frio.

---

## 4. O Snapshot de Funil (`update_daily_funnel_snapshot`)

Esta é a query mais complexa. Ela cruza o Payload (`external_checkout_code`) com a tabela interna de `checkouts` para pivotar as vendas nas etapas do funil.

### Distribuição e Contagem (Pivoting)
Ele cria sumários condicionais (`SUM CASE WHEN`):
* `up1_amount`: Dinheiro de vendas vinculadas a checkouts configurados como estágio `Up1`.
* `dw1_amount`: Dinheiro de vendas vinculadas a `Dw1`.
* *(O mesmo padrão segue até Up4/Dw4)*.

### Métricas de Conversão de Funil (Step-by-Step)
Calcula onde os clientes estão abandonando o fluxo.

* **Front to Up1 Rate**
    `Total (Up1 + Dw1) / Total Front`
* **Up1 to Up2 Rate**
    `Total (Up2 + Dw2) / Total (Up1 + Dw1)`
    *A lógica avança comparando sempre a etapa atual com o volume de pessoas que converteu na etapa imediatamente anterior.*

### Métricas de Ticket de Funil
* **AOV Up1**
    `(up1_amount + dw1_amount) / (total_up1 + total_dw1)`
    *Informa o ticket médio especificamente da primeira etapa de Upsell.*

* **Backend Rate (Taxa de Aproveitamento Pós-Venda)**
    `Soma de todo Faturamento de Upsells e Downsells / Gross Revenue`
    *Se esse número for `0.60`, significa que 60% de toda a sua receita vem da oferta de pós-venda, e apenas 40% vem do produto de entrada.*