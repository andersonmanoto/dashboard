import asyncio
import io
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML
import resend
from loguru import logger

from app.config import Settings
from app.repositories.database import DatabaseRepository


class ReportService:
    """
    Serviço dedicado à geração de PDFs e envio de e-mails.
    Isolado do tráfego financeiro por ser intensivo em CPU.
    """

    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        self.settings = settings
        self.db = db_repo

        template_dir = Path(__file__).parent.parent.parent / "templates"

        # Configura a API do Resend
        resend.api_key = self.settings.resend_api_key

        # Configura o Jinja2 para ler da pasta app/templates
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def _fetch_data(self, filters: dict) -> dict:
        transactions = self.db.get_high_fee_transactions(filters)

        grouped_data = {}
        total_revenue = 0
        total_fees = 0

        # Nome padrão caso não venha nada
        product_label = "General"

        for tx in transactions:
            total_revenue += tx.get("sale_total", 0)
            total_fees += tx.get("merchant_commission", 0)

            product_name = (
                tx.get("products", {}).get("name")
                if tx.get("products")
                else "Unknown Product"
            )
            account_id = tx.get("account_id") or "N/A"
            group_key = f"{product_name} ({account_id})"

            product_label = product_name

            if group_key not in grouped_data:
                grouped_data[group_key] = []

            grouped_data[group_key].append(tx)

        return {
            "report_title": "Excessive Fee Analysis: BuyGoods",
            "period_start": filters["period"]["start_date"],
            "period_end": filters["period"]["end_date"],
            "total_transactions": len(transactions),
            "total_affected_revenue": total_revenue,
            "total_fees_paid": total_fees,
            "product_label": product_label,
            "grouped_transactions": grouped_data,
        }

    def _render_html(
        self, data: dict, template_name: str = "buygoods_fee_audit.html"
    ) -> str:
        """Injeta os dados no template HTML usando Jinja2."""
        template = self.jinja_env.get_template(template_name)
        return template.render(**data)

    def _generate_pdf_bytes(self, html_string: str) -> bytes:
        """
        [CPU BOUND] - Converte a string HTML num PDF em bytes na memória RAM.
        """
        pdf_buffer = io.BytesIO()

        # 1. Calculamos o caminho base (o mesmo que usámos no Jinja)
        template_dir = Path(__file__).parent.parent.parent / "templates"

        # 2. Passamos o base_url para o WeasyPrint achar as imagens!
        HTML(string=html_string, base_url=str(template_dir)).write_pdf(pdf_buffer)

        pdf_bytes = pdf_buffer.getvalue()
        pdf_buffer.close()
        return pdf_bytes
    
    def _fetch_grouped_data(self, filters: dict) -> dict:
        """
        Busca as transações no banco e as agrupa por Produto (Account ID).
        """
        transactions = self.db.get_high_fee_transactions(filters)
        
        grouped = {}
        for tx in transactions:
            # Pega o nome do produto através do relacionamento 'products' no Supabase
            p_name = tx.get("products", {}).get("name") if tx.get("products") else "Unknown Product"
            acc_id = tx.get("account_id") or "N/A"
            key = f"{p_name} ({acc_id})"
            
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(tx)
        
        return grouped

    async def generate_and_send_report(
        self, 
        user_email: str, 
        filters: dict, 
        template_name: str = "buygoods_fee_audit.html"
    ):
        """
        Gera múltiplos PDFs (um por produto) e envia em um ÚNICO e-mail com múltiplos anexos.
        """
        try:
            # 1. Busca todos os dados e agrupa por produto (usando a função auxiliar _fetch_grouped_data)
            grouped_data = await asyncio.to_thread(self._fetch_grouped_data, filters)
            
            if not grouped_data:
                logger.warning(f"Nenhum dado de taxa abusiva encontrado para {user_email}.")
                return False

            attachments = []
            
            # 2. Loop principal: Criamos um PDF para cada produto agrupado
            for group_name, items in grouped_data.items():
                # --- CÁLCULOS DO SUMÁRIO ---
                prod_revenue = sum(item.get("sale_total", 0) for item in items)
                prod_fees = sum(item.get("merchant_commission", 0) for item in items)
                
                # Cálculo do Overcharged: O que foi pago além dos 7% combinados
                # Fórmula: Taxa Real - (Volume de Vendas * 0.07)
                expected_fee_at_7_percent = prod_revenue * 0.07
                overcharged_amount = max(0, prod_fees - expected_fee_at_7_percent)

                # Montamos o contexto específico para este arquivo PDF
                context = {
                    "report_title": "Excessive Fee Analysis – BuyGoods",
                    "period_start": filters["period"]["start_date"],
                    "period_end": filters["period"]["end_date"],
                    "total_transactions": len(items),
                    "total_affected_revenue": prod_revenue,
                    "total_fees_paid": prod_fees,
                    "overcharged_fees": overcharged_amount,
                    "grouped_transactions": {group_name: items} 
                }

                # 3. Renderização e Geração do PDF em Memória
                html_string = self.jinja_env.get_template(template_name).render(**context)
                pdf_bytes = await asyncio.to_thread(self._generate_pdf_bytes, html_string)

                # 4. Nome do Arquivo (Ex: "AUDIT_VisiumPro.pdf")
                clean_product_name = group_name.split(" (")[0].strip().replace(" ", "_")
                
                attachments.append({
                    "filename": f"AUDIT_{clean_product_name}.pdf",
                    "content": list(pdf_bytes) # Resend espera uma lista de inteiros ou bytes
                })

            # 5. Envio do E-mail Consolidado com todos os anexos
            logger.info(f"Enviando e-mail com {len(attachments)} anexos de auditoria para {user_email}")
            
            params = {
                "from": self.settings.email_from,
                "to": [user_email],
                "subject": f"BuyGoods Fee Audit: {len(attachments)} Products Found",
                "html": f"""
                <p>Hello,</p>
                <p>Your requested fee audit is ready. We identified overcharged fees in <strong>{len(attachments)}</strong> products.</p>
                <p>Please find the individual audit reports attached as PDF files.</p>
                <p>Best regards,<br>Tiger Offers Team</p>
                """,
                "attachments": attachments
            }

            # Envia via Resend (I/O Bound)
            response = await asyncio.to_thread(resend.Emails.send, params)
            
            logger.success(f"E-mail de auditoria enviado com sucesso para {user_email}. Resend ID: {response.get('id')}")
            return True

        except Exception as e:
            logger.exception(f"Erro ao processar e-mail com múltiplos anexos para {user_email}: {e}")
            raise e
