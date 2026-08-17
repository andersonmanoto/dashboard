"""
Sincroniza as URLs de checkout (BuyGoods) guardadas em `checkouts` no Supabase
para as páginas HTML publicadas no servidor.

Como funciona:
  1. Recebe product_id, network_id, domain, active_funnel_number e
     previous_funnel_number do frontend. Uma Edge Function do Supabase já
     inseriu a linha correspondente em `product_network_active_funnels`
     antes de chamar este serviço — o id dela (active_funnel_row_id) também
     vem no payload. Este serviço NÃO insere/faz upsert nessa tabela, só
     atualiza `status`/`error_message` da linha indicada com o resultado da
     tentativa.
  2. Busca em `checkouts` todas as linhas desse product_id + network_id +
     active_funnel_number, montando um mapa (funnel_stage, switch_link) ->
     url. switch_link não é uma coluna — é derivado como
     f"switchLink-{quantity}b" a partir de checkouts.quantity (convenção
     alinhada com quem cria os codenames dos produtos).
  3. Varre recursivamente {hosting_base}/{domain}/public_html atrás de
     arquivos .html.
  4. Em cada arquivo, encontra tags <a id="switchLink-..."> e troca APENAS o
     valor do atributo href pelo valor correspondente do mapa — o resto do
     arquivo não é tocado. O estágio do funil (Purchase, Up1, Dw1, ...) vem
     do atributo data-funnel-stage da mesma tag (default "Purchase" se
     ausente).
  5. Ao final, sempre atualiza status ('success' ou 'error') e
     error_message em `product_network_active_funnels` (via
     active_funnel_row_id). Em caso de sucesso e se o funil realmente mudou
     (previous_funnel_number != active_funnel_number), registra a transição
     em `active_funnel_history` — essa tabela continua de responsabilidade
     do backend. O status gravado ali reflete se todos os arquivos tocados
     passaram na verificação pós-escrita (não é sobre o sync como um todo,
     que só chega a esse ponto sem ter lançado exceção); link_changes guarda
     um jsonb { "dominio/subdiretorio": { switchLink_id: nova_url, ... } }
     com o que foi trocado em cada página.

Porta a lógica do script standalone /python/funil/sync.py para dentro do
dashboard, reaproveitando o client Supabase do DatabaseRepository e as
credenciais SSH já usadas pelo WebScannerService.
"""

import asyncio
import difflib
import os
import posixpath
import re
import stat
from datetime import datetime, timezone

import paramiko
from loguru import logger

from app.config import Settings
from app.repositories.database import DatabaseRepository

TAG_RE = re.compile(r"<a\b[^>]*?>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"'
    r"|([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*'([^']*)'"
)
HREF_RE = re.compile(r'(href\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?\s*([a-zA-Z0-9_-]+)', re.IGNORECASE
)


class FunnelSyncError(Exception):
    """Erro esperado (produto/checkout não encontrado, config faltando)."""


def _detect_encoding(raw: bytes) -> str:
    """Tenta achar o charset declarado em <meta> nos primeiros bytes do HTML."""
    match = META_CHARSET_RE.search(raw[:4096])
    if match:
        name = match.group(1).decode("ascii", "ignore")
        try:
            "".encode(name)
            return name
        except LookupError:
            pass
    return "utf-8"


def _parse_attrs(tag_text: str) -> dict:
    attrs = {}
    for m in ATTR_RE.finditer(tag_text):
        if m.group(1) is not None:
            attrs[m.group(1).lower()] = m.group(2)
        else:
            attrs[m.group(3).lower()] = m.group(4)
    return attrs


def _build_new_content(
    original: str, checkout_map: dict, remote_path: str, warnings: list[str]
) -> tuple[str | None, dict[str, str]]:
    """Troca hrefs nas tags <a id="..."> cujo id bate com switch_link.

    Retorna (novo_conteudo, changed_links). novo_conteudo é None se nada mudou
    nesse arquivo. changed_links mapeia switch_link -> nova url, só com os que
    realmente mudaram (pra alimentar active_funnel_history.link_changes).
    """
    known_switch_links = {switch_link for (_, switch_link) in checkout_map}

    parts = []
    last_end = 0
    changed_links: dict[str, str] = {}

    for m in TAG_RE.finditer(original):
        tag_text = m.group(0)
        attrs = _parse_attrs(tag_text)
        switch_link = attrs.get("id")
        if not switch_link or switch_link not in known_switch_links:
            continue
        stage = attrs.get("data-funnel-stage", "Purchase")

        new_url = checkout_map.get((stage, switch_link))
        if not new_url:
            warnings.append(
                f"{remote_path}: sem checkout cadastrado para "
                f"funnel_stage='{stage}' switch_link='{switch_link}' — link mantido como está"
            )
            continue

        href_match = HREF_RE.search(tag_text)
        if not href_match:
            warnings.append(
                f"{remote_path}: tag com id='{switch_link}' não tem href — pulando"
            )
            continue

        old_href = href_match.group(3)
        if old_href == new_url:
            continue

        tag_start = m.start()
        href_value_start = tag_start + href_match.start(3)
        href_value_end = tag_start + href_match.end(3)

        parts.append(original[last_end:href_value_start])
        parts.append(new_url)
        last_end = href_value_end
        changed_links[switch_link] = new_url

    if not changed_links:
        return None, {}

    parts.append(original[last_end:])
    return "".join(parts), changed_links


class FunnelSyncService:
    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        self.settings = settings
        self.db_repo = db_repo

    # --- Supabase: leitura ---

    def _fetch_product(self, product_id: str) -> dict:
        response = (
            self.db_repo.client.table("products")
            .select("id, name")
            .eq("id", product_id)
            .execute()
        )
        if not response.data:
            raise FunnelSyncError(f"Produto '{product_id}' não encontrado.")
        return response.data[0]

    def _fetch_checkout_map(
        self, product_id: str, network_id: str, funnel_number: int
    ) -> dict:
        """Retorna { (funnel_stage, switch_link): url }, só do estágio Purchase.

        switch_link não é mais uma coluna própria — é derivado de
        checkouts.quantity, já que o id da tag no HTML sempre segue o padrão
        switchLink-{quantity}b (convenção alinhada com quem cria os
        codenames). Filtra allow_aff_link=true porque pode haver mais de um
        checkout com o mesmo (funnel_stage, quantity) — ex: a variante _sms
        de um mesmo pote — e só a linha "principal" (allow_aff_link=true)
        deve virar o switchLink do HTML.

        Restrito a funnel_stage=Purchase de propósito: só as páginas de
        front (Purchase) seguem a convenção switchLink-Nb de forma
        consistente entre produtos. Páginas de upsell/downsell variam
        demais (id="add", sem id nenhum, links em variável JS) pra esse
        parser genérico mexer nelas com segurança.
        """
        response = (
            self.db_repo.client.table("checkouts")
            .select("funnel_stage, quantity, url")
            .eq("product_id", product_id)
            .eq("network_id", network_id)
            .eq("funnel_number", funnel_number)
            .eq("allow_aff_link", True)
            .execute()
        )

        mapping = {}
        for row in response.data:
            quantity = row.get("quantity")
            if not quantity:
                continue
            stage = row["funnel_stage"] or "Purchase"
            if stage != "Purchase":
                continue
            switch_link = f"switchLink-{quantity}b"
            mapping[(stage, switch_link)] = row["url"]
        return mapping

    # --- Supabase: escrita (não deve derrubar o sync se falhar) ---

    def _update_sync_status(
        self, active_funnel_row_id: str, status: str, error_message: str | None
    ) -> None:
        try:
            self.db_repo.client.table("product_network_active_funnels").update(
                {"status": status, "error_message": error_message}
            ).eq("id", active_funnel_row_id).execute()
        except Exception as e:
            logger.error(
                f"[funnel-sync] falha ao atualizar status em "
                f"product_network_active_funnels (id={active_funnel_row_id}): {e}"
            )

    def _update_funnel_history_status(
        self,
        history_id: str,
        status: str,
        link_changes: dict,
    ) -> None:
        """Atualiza a linha em active_funnel_history criada como 'processing'
        pelo router assim que o POST chegou (ver app/routers/active_funnels.py)."""
        try:
            self.db_repo.client.table("active_funnel_history").update(
                {
                    "status": status,
                    "link_changes": link_changes or None,
                }
            ).eq("id", history_id).execute()
        except Exception as e:
            logger.error(
                f"[funnel-sync] falha ao atualizar active_funnel_history "
                f"(id={history_id}): {e}"
            )

    # --- SFTP ---

    def _connect_sftp(self):
        transport = paramiko.Transport(
            (self.settings.ssh_host, self.settings.ssh_port)
        )
        transport.connect(
            username=self.settings.ssh_username, password=self.settings.ssh_password
        )
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise RuntimeError("Não foi possível abrir sessão SFTP.")
        return sftp, transport

    def _walk_remote_html_files(self, sftp, remote_dir: str):
        for entry in sftp.listdir_attr(remote_dir):
            remote_path = posixpath.join(remote_dir, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                yield from self._walk_remote_html_files(sftp, remote_path)
            elif entry.filename.lower().endswith((".html", ".htm")):
                yield remote_path

    @staticmethod
    def _read_remote_file(sftp, remote_path: str) -> tuple[str, str]:
        """Lê o arquivo remoto e devolve (conteudo, encoding usado).

        Nem toda página hospedada é UTF-8 de fato (export de Word/Windows
        costuma vir em windows-1252/latin-1, mesmo declarando outra coisa ou
        nada no <meta charset>). Detecta pelo <meta charset> quando possível;
        se os bytes não baterem, tenta cp1252 (cobre os bytes "tipográficos"
        0x80-0x9F que exports do Windows costumam usar) e por fim latin-1 —
        que mapeia todos os 256 valores de byte e nunca lança
        UnicodeDecodeError (diferente de cp1252, que tem alguns pontos
        indefinidos, ex: 0x81, 0x8D, 0x8F, 0x90, 0x9D). O encoding devolvido
        precisa ser reusado na escrita, senão o conteúdo não tocado pelo
        swap de link é corrompido ao ser re-salvo em UTF-8.
        """
        with sftp.open(remote_path, "rb") as f:
            raw = f.read()

        encoding = _detect_encoding(raw)
        for candidate in dict.fromkeys([encoding, "cp1252", "latin-1"]):
            try:
                return raw.decode(candidate), candidate
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1"), "latin-1"  # inalcançável: latin-1 não falha

    def _backup_file(self, remote_path: str, content: str) -> str:
        backup_dir = os.path.join(self.settings.temp_dir, "funnel_sync_backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = remote_path.strip("/").replace("/", "__")
        backup_path = os.path.join(backup_dir, f"{ts}__{safe_name}")
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(content)
        return backup_path

    # --- Fluxo principal (bloqueante — roda em thread) ---

    def _run_sync(
        self,
        product_id: str,
        network_id: str,
        domain: str,
        user_id: str,
        active_funnel_number: int,
        previous_funnel_number: int | None,
        active_funnel_row_id: str,
        active_funnel_history_id: str | None,
    ) -> dict:
        warnings: list[str] = []
        files_changed: list[dict] = []
        link_changes: dict[str, dict[str, str]] = {}
        checkout_map: dict = {}

        try:
            product = self._fetch_product(product_id)

            checkout_map = self._fetch_checkout_map(
                product_id, network_id, active_funnel_number
            )
            if not checkout_map:
                raise FunnelSyncError(
                    f"Nenhum checkout cadastrado para product_id={product_id} "
                    f"network_id={network_id} funnel_number={active_funnel_number}."
                )

            remote_root = f"{self.settings.hosting_base}/{domain}/public_html"
            logger.info(
                f"[funnel-sync] Varrendo {remote_root} "
                f"(produto={product['name']}, network_id={network_id}, "
                f"funil={active_funnel_number})"
            )

            sftp, transport = self._connect_sftp()
            try:
                for remote_path in self._walk_remote_html_files(sftp, remote_root):
                    original, encoding = self._read_remote_file(sftp, remote_path)
                    new_content, changed_links = _build_new_content(
                        original, checkout_map, remote_path, warnings
                    )
                    if new_content is None:
                        continue

                    backup_path = self._backup_file(remote_path, original)
                    # Escreve bytes na mesma codificação da leitura — se
                    # passássemos str, o paramiko sempre re-codifica em
                    # UTF-8, corrompendo páginas que não são UTF-8 de fato.
                    with sftp.open(remote_path, "wb") as f:
                        f.write(new_content.encode(encoding))

                    verify, _ = self._read_remote_file(sftp, remote_path)
                    ok = verify == new_content

                    diff = "".join(
                        difflib.unified_diff(
                            original.splitlines(keepends=True),
                            new_content.splitlines(keepends=True),
                            lineterm="",
                        )
                    )

                    files_changed.append(
                        {
                            "path": remote_path,
                            "ok": ok,
                            "backup_path": backup_path,
                            "diff": diff,
                        }
                    )

                    rel_dir = posixpath.dirname(
                        posixpath.relpath(remote_path, remote_root)
                    )
                    page_key = f"{domain}/{rel_dir}" if rel_dir else domain
                    link_changes.setdefault(page_key, {}).update(changed_links)

                    log = logger.info if ok else logger.error
                    log(
                        f"[funnel-sync] {'OK' if ok else 'FALHOU'} {remote_path} "
                        f"(backup: {backup_path})"
                    )
            finally:
                sftp.close()
                transport.close()
        except Exception as e:
            self._update_sync_status(active_funnel_row_id, "error", str(e))
            if active_funnel_history_id is not None:
                self._update_funnel_history_status(
                    active_funnel_history_id, "error", link_changes
                )
            raise
        else:
            self._update_sync_status(active_funnel_row_id, "success", None)
            if active_funnel_history_id is not None:
                history_status = (
                    "success" if all(f["ok"] for f in files_changed) else "error"
                )
                self._update_funnel_history_status(
                    active_funnel_history_id, history_status, link_changes
                )

        return {
            "product": product["name"],
            "domain": domain,
            "network_id": network_id,
            "funnel_number": active_funnel_number,
            "checkouts_found": len(checkout_map),
            "warnings": warnings,
            "files_changed": files_changed,
        }

    async def apply(
        self,
        product_id: str,
        network_id: str,
        domain: str,
        user_id: str,
        active_funnel_number: int,
        previous_funnel_number: int | None,
        active_funnel_row_id: str,
        active_funnel_history_id: str | None = None,
    ) -> dict:
        """Offload da operação bloqueante (SSH/SFTP) para uma thread separada."""
        return await asyncio.to_thread(
            self._run_sync,
            product_id,
            network_id,
            domain,
            user_id,
            active_funnel_number,
            previous_funnel_number,
            active_funnel_row_id,
            active_funnel_history_id,
        )
