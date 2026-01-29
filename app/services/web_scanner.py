import json
import asyncio
import httpx
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from app.config import get_settings

logger = logging.getLogger(__name__)

class WebScannerService:
    def __init__(self):
        """
        Inicializa o serviço carregando as configurações do config.py
        """
        settings = get_settings()

        self.map_file = Path(settings.structure_map_file)
        self.concurrency_limit = 20

    def _load_structure_map(self) -> dict:
        if not self.map_file.exists():
            logger.error(f"Arquivo de mapa não encontrado em: {self.map_file}")
            return {}
        try:
            with open(self.map_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao ler JSON: {e}")
            return {}

    def _extract_stage_from_url(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        return path.split("/")[-1] if path else "home"

    async def _check_url(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str, codename: str):
        found_data = []
        unique_links = set()

        async with semaphore:
            try:
                response = await client.get(url, timeout=10.0, follow_redirects=True)
                if response.status_code != 200:
                    return []

                soup = BeautifulSoup(response.text, 'html.parser')
                
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    
                    if href in unique_links:
                        continue
                    
                    unique_links.add(href)

                    try:
                        parsed_url = urlparse(href)
                        query_params = parse_qs(parsed_url.query)
                        
                        match_found = False
                        
                        for values in query_params.values():
                            for val in values:
                                if val.strip().lower() == codename.strip().lower():
                                    match_found = True
                                    break
                            if match_found: break
                        
                        if match_found:
                            found_data.append({
                                "source_url": url,
                                "stage": self._extract_stage_from_url(url),
                                "found_link": href
                            })
                    except:
                        continue
            except Exception:
                pass
                
        return found_data

    async def run_scan(self, codename: str, domain_filter: str):
        structure_data = self._load_structure_map()
        urls_to_scan = []

        for domain, funnels in structure_data.items():
            if domain_filter and domain_filter not in domain:
                continue

            for funnel_name, directories in funnels.items():
                base_funnel_url = f"https://{domain}" if funnel_name == "fnn1" else f"https://{domain}/{funnel_name}"

                for directory in directories:
                    urls_to_scan.append(f"{base_funnel_url}/{directory}/")

        if not urls_to_scan:
            return []

        semaphore = asyncio.Semaphore(self.concurrency_limit)
        results = []
        
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
        async with httpx.AsyncClient(limits=limits, verify=False) as client:
            tasks = [
                self._check_url(client, semaphore, url, codename) 
                for url in urls_to_scan
            ]
            scan_results = await asyncio.gather(*tasks)
            
            for sublist in scan_results:
                if sublist:
                    results.extend(sublist)

        return results