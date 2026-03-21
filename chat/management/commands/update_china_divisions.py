import json
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError

from chat.services.china_division_repository import ChinaDivisionRepository, DEFAULT_DATASET_PATH


class Command(BaseCommand):
    help = '下载并更新本地中国省市区地址库'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source-base-url',
            default='https://unpkg.com/province-city-china/dist',
            help='开源地址库的基础 URL',
        )
        parser.add_argument(
            '--output',
            default=str(DEFAULT_DATASET_PATH),
            help='输出文件路径',
        )

    def handle(self, *args, **options):
        base_url = options['source_base_url'].rstrip('/')
        output_path = Path(options['output'])

        try:
            provinces = self.fetch_json(f'{base_url}/province.json')
            cities = self.fetch_json(f'{base_url}/city.json')
            areas = self.fetch_json(f'{base_url}/area.json')
        except requests.RequestException as exc:
            raise CommandError(f'下载地址库失败: {exc}') from exc

        dataset = ChinaDivisionRepository.build_dataset(provinces, cities, areas)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8') as fp:
            json.dump(dataset, fp, ensure_ascii=False, separators=(',', ':'))

        ChinaDivisionRepository.clear_cache()
        self.stdout.write(self.style.SUCCESS(f'已更新中国地址库: {output_path}'))

    def fetch_json(self, url):
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()
