import re

from .china_division_repository import ChinaDivisionRepository


class ChinaAddressNormalizer:
    """中国地址名称规范化"""

    CJK_RE = re.compile(r'[\u4e00-\u9fff]')

    MUNICIPALITIES = {'北京市', '上海市', '天津市', '重庆市'}
    MUNICIPALITY_SHORT = {'北京', '上海', '天津', '重庆'}

    @classmethod
    def has_chinese_text(cls, value):
        return bool(cls.CJK_RE.search((value or '').strip()))

    @classmethod
    def is_china(cls, country):
        normalized = (country or '').strip().lower()
        return normalized in {'中国', '中华人民共和国', 'china', "people's republic of china", 'cn'}

    @classmethod
    def normalize_component(cls, value):
        return (value or '').strip()

    @classmethod
    def normalize(cls, location_data):
        if not location_data:
            return None

        normalized = dict(location_data)
        normalized['country'] = cls.normalize_component(location_data.get('country', ''))
        normalized['region'] = cls.normalize_component(location_data.get('region', ''))
        normalized['city'] = cls.normalize_component(location_data.get('city', ''))
        normalized['district'] = cls.normalize_component(location_data.get('district', ''))
        normalized['township'] = cls.normalize_component(location_data.get('township', ''))

        if not cls.is_china(normalized['country']):
            return normalized

        normalized['country'] = '中国'
        normalized['region'] = cls._normalize_region(normalized['region'])
        normalized['city'] = cls._normalize_city(normalized['city'], normalized['region'])
        normalized['district'] = cls._normalize_district(normalized['district'], normalized['city'])
        canonical = ChinaDivisionRepository.canonicalize(
            region=normalized['region'],
            city=normalized['city'],
            district=normalized['district'],
        )
        normalized['region'] = canonical['region'] or normalized['region']
        normalized['city'] = canonical['city'] or normalized['city']
        normalized['district'] = canonical['district'] or normalized['district']
        return normalized

    @classmethod
    def _normalize_region(cls, region):
        region = cls.normalize_component(region)
        if not region:
            return ''
        if region in cls.MUNICIPALITY_SHORT:
            return f'{region}市'
        return region

    @classmethod
    def _normalize_city(cls, city, region):
        city = cls.normalize_component(city)
        if not city:
            return ''
        if region in cls.MUNICIPALITIES and city in {region, region[:-1]}:
            return region
        if city in cls.MUNICIPALITY_SHORT:
            return f'{city}市'
        return city

    @classmethod
    def _normalize_district(cls, district, city):
        district = cls.normalize_component(district)
        if not district:
            return ''
        if district == city:
            return ''
        return district
