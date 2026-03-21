import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .china_division_repository import ChinaDivisionRepository
from .location_normalizer import ChinaAddressNormalizer

logger = logging.getLogger(__name__)


class GlobalReverseGeocodeService:
    """全球反向地理编码服务"""

    REVERSE_GEOCODE_URL = getattr(
        settings,
        'REVERSE_GEOCODE_URL',
        'https://nominatim.openstreetmap.org/reverse',
    )
    REQUEST_HEADERS = {
        'User-Agent': getattr(
            settings,
            'GEOCODE_USER_AGENT',
            'websocket-chat/1.0 (location reverse geocoding)',
        )
    }
    BIGDATA_REVERSE_URL = 'https://api.bigdatacloud.net/data/reverse-geocode-client'

    @classmethod
    def reverse_geocode(cls, latitude, longitude):
        cache_key = f'reverse_geo_{round(latitude, 4)}_{round(longitude, 4)}'
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data

        try:
            response = requests.get(
                cls.REVERSE_GEOCODE_URL,
                params={
                    'format': 'jsonv2',
                    'lat': latitude,
                    'lon': longitude,
                    'accept-language': 'zh-CN',
                    'zoom': 18,
                    'addressdetails': 1,
                },
                headers=cls.REQUEST_HEADERS,
                timeout=8,
            )
            data = response.json()
            address = data.get('address', {})
            location_data = {
                'country': address.get('country', ''),
                'region': address.get('state', '') or address.get('province', '') or address.get('region', ''),
                'city': (
                    address.get('city')
                    or address.get('municipality')
                    or address.get('town')
                    or address.get('county')
                    or address.get('city_district')
                    or ''
                ),
                'district': (
                    address.get('suburb')
                    or address.get('borough')
                    or address.get('quarter')
                    or address.get('neighbourhood')
                    or address.get('city_district')
                    or address.get('district')
                    or address.get('county')
                    or ''
                ),
                'township': (
                    address.get('town')
                    or address.get('village')
                    or address.get('hamlet')
                    or ''
                ),
                'latitude': latitude,
                'longitude': longitude,
                'timezone': '',
            }
            location_data = ChinaAddressNormalizer.normalize(location_data)
            if cls._needs_secondary_lookup(location_data):
                fallback = cls.reverse_geocode_secondary(latitude, longitude)
                if fallback:
                    location_data = fallback
            cache.set(cache_key, location_data, 86400)
            return location_data
        except requests.RequestException as e:
            logger.error(f"反向地理编码请求失败: {latitude},{longitude} - {str(e)}")
            return cls.reverse_geocode_secondary(latitude, longitude)
        except Exception as e:
            logger.error(f"反向地理编码处理失败: {latitude},{longitude} - {str(e)}")
            return cls.reverse_geocode_secondary(latitude, longitude)

    @classmethod
    def reverse_geocode_secondary(cls, latitude, longitude):
        try:
            response = requests.get(
                cls.BIGDATA_REVERSE_URL,
                params={
                    'latitude': latitude,
                    'longitude': longitude,
                    'localityLanguage': 'zh',
                },
                timeout=8,
            )
            data = response.json()
            location_data = {
                'country': data.get('countryName', ''),
                'region': data.get('principalSubdivision', ''),
                'city': data.get('city', ''),
                'district': data.get('locality', ''),
                'township': '',
                'latitude': latitude,
                'longitude': longitude,
                'timezone': '',
            }

            admin_entries = data.get('localityInfo', {}).get('administrative', [])
            china_admin_code = ''
            for entry in admin_entries:
                candidate = str(entry.get('chinaAdminCode', '')).strip()
                if candidate:
                    china_admin_code = candidate
                if entry.get('adminLevel') == 8 and not location_data['township']:
                    location_data['township'] = entry.get('name', '')

            location_data = ChinaAddressNormalizer.normalize(location_data)
            canonical = ChinaDivisionRepository.canonicalize_admin_code(china_admin_code)
            if canonical:
                location_data['region'] = canonical['region']
                location_data['city'] = canonical['city']
                location_data['district'] = canonical['district']
            return location_data
        except requests.RequestException as e:
            logger.error(f"二级反向地理编码请求失败: {latitude},{longitude} - {str(e)}")
            return None
        except Exception as e:
            logger.error(f"二级反向地理编码处理失败: {latitude},{longitude} - {str(e)}")
            return None

    @classmethod
    def _needs_secondary_lookup(cls, location_data):
        if not location_data:
            return True
        if not ChinaAddressNormalizer.is_china(location_data.get('country')):
            return False
        return not any([
            (location_data.get('region') or '').strip(),
            (location_data.get('city') or '').strip(),
            (location_data.get('district') or '').strip(),
        ])
