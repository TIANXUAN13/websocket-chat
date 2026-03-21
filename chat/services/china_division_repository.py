import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings


DEFAULT_DATASET_PATH = (
    Path(getattr(settings, 'BASE_DIR', Path(__file__).resolve().parents[2]))
    / 'chat'
    / 'data'
    / 'china_divisions.json'
)


class ChinaDivisionRepository:
    """本地中国行政区划库加载与匹配"""

    MUNICIPALITIES = {'北京市', '上海市', '天津市', '重庆市'}

    @classmethod
    @lru_cache(maxsize=1)
    def load_dataset(cls):
        if not DEFAULT_DATASET_PATH.exists():
            return None

        with DEFAULT_DATASET_PATH.open('r', encoding='utf-8') as fp:
            return json.load(fp)

    @classmethod
    def clear_cache(cls):
        cls.load_dataset.cache_clear()

    @classmethod
    def canonicalize(cls, region='', city='', district=''):
        dataset = cls.load_dataset()
        if not dataset:
            return {
                'region': (region or '').strip(),
                'city': (city or '').strip(),
                'district': (district or '').strip(),
            }

        region = (region or '').strip()
        city = (city or '').strip()
        district = (district or '').strip()

        matched_province = cls._match_item(dataset.get('provinces', {}), region or city)
        if not matched_province:
            return {'region': region, 'city': city, 'district': district}

        canonical_region = matched_province['name']
        city_nodes = matched_province.get('cities', {})

        matched_city = cls._match_item(city_nodes, city or canonical_region or district)
        canonical_city = matched_city['name'] if matched_city else city

        if canonical_region in cls.MUNICIPALITIES and not canonical_city:
            canonical_city = canonical_region

        district_nodes = matched_city.get('areas', {}) if matched_city else {}
        matched_district = cls._match_item(district_nodes, district)
        canonical_district = matched_district['name'] if matched_district else district

        return {
            'region': canonical_region,
            'city': canonical_city,
            'district': canonical_district,
        }

    @classmethod
    def canonicalize_admin_code(cls, china_admin_code):
        dataset = cls.load_dataset()
        if not dataset or not china_admin_code:
            return None

        normalized_code = ''.join(str(china_admin_code).split())
        for province in dataset.get('provinces', {}).values():
            if str(province.get('code', '')) == f'{normalized_code[:2]}0000':
                region = province['name']
                for city in province.get('cities', {}).values():
                    city_code = str(city.get('code', ''))
                    if city_code == f'{normalized_code[:4]}00' or city_code == normalized_code[2:4]:
                        city_name = city['name']
                        for area in city.get('areas', {}).values():
                            if str(area.get('code', '')) == normalized_code:
                                return {
                                    'region': region,
                                    'city': city_name,
                                    'district': area['name'],
                                }
        return None

    @classmethod
    def _match_item(cls, mapping, value):
        normalized = cls._normalize_key(value)
        if not normalized:
            return None
        return mapping.get(normalized)

    @classmethod
    def _normalize_key(cls, value):
        value = (value or '').strip()
        if not value:
            return ''
        compact = value.replace(' ', '').replace('·', '').replace('-', '').replace('—', '')
        variants = {compact}
        if compact.endswith('省') or compact.endswith('市') or compact.endswith('盟'):
            variants.add(compact[:-1])
        if compact.endswith('自治区') or compact.endswith('特别行政区'):
            variants.add(compact.replace('自治区', '').replace('特别行政区', ''))
        if compact in {'北京', '上海', '天津', '重庆'}:
            variants.add(f'{compact}市')
        return sorted(variants, key=len)[0]

    @classmethod
    def build_dataset(cls, provinces, cities, areas):
        province_nodes = {}
        for province in provinces:
            province_name = province['name']
            province_code = str(province.get('province') or str(province.get('code', ''))[:2])
            province_nodes[cls._normalize_key(province_name)] = {
                'name': province_name,
                'code': province.get('code'),
                'province_code': province_code,
                'cities': {},
            }

        city_index = {}
        for city in cities:
            province_code = str(city.get('province'))
            province_node = next(
                (node for node in province_nodes.values() if str(node.get('province_code')) == province_code),
                None,
            )
            if not province_node:
                continue
            city_name = city['name']
            city_code = str(city.get('code'))
            full_city_code = city_code if len(city_code) == 6 else f'{province_code}{city_code}00'
            city_node = {
                'name': city_name,
                'code': full_city_code,
                'city_code': str(city.get('city')),
                'areas': {},
            }
            province_node['cities'][cls._normalize_key(city_name)] = city_node
            city_index[(province_code, str(city.get('city')))] = city_node

        for area in areas:
            province_code = str(area.get('province'))
            city_segment = str(area.get('city'))
            city_node = city_index.get((province_code, city_segment))
            if not city_node:
                province_node = next(
                    (node for node in province_nodes.values() if str(node.get('province_code')) == province_code),
                    None,
                )
                if province_node and province_node['name'] in cls.MUNICIPALITIES:
                    synthetic_city_key = cls._normalize_key(province_node['name'])
                    city_node = province_node['cities'].get(synthetic_city_key)
                    if not city_node:
                        city_node = {
                            'name': province_node['name'],
                            'code': f'{province_code}{city_segment}00',
                            'city_code': city_segment,
                            'areas': {},
                        }
                        province_node['cities'][synthetic_city_key] = city_node
                    city_index[(province_code, city_segment)] = city_node
            if not city_node:
                continue
            area_name = area['name']
            city_node['areas'][cls._normalize_key(area_name)] = {
                'name': area_name,
                'code': area.get('code'),
            }

        return {
            'version': 1,
            'provinces': province_nodes,
        }
