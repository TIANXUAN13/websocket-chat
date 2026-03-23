import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .china_division_repository import ChinaDivisionRepository
from .location_normalizer import ChinaAddressNormalizer

logger = logging.getLogger(__name__)


class GlobalReverseGeocodeService:
    """全球反向地理编码服务"""

    REVERSE_GEOCODE_URL = getattr(settings, 'REVERSE_GEOCODE_URL', 'https://nominatim.openstreetmap.org/reverse')
    REQUEST_HEADERS = {
        'User-Agent': getattr(settings, 'GEOCODE_USER_AGENT', 'websocket-chat/1.0 (location reverse geocoding)')
    }
    BIGDATA_REVERSE_URL = getattr(settings, 'BIGDATA_REVERSE_URL', 'https://api.bigdatacloud.net/data/reverse-geocode-client')
    AMAP_REVERSE_URL = 'https://restapi.amap.com/v3/geocode/regeo'
    PROVIDER = getattr(settings, 'GEOCODE_PROVIDER', 'auto').strip().lower()
    TIMEOUT = float(getattr(settings, 'GEOCODE_TIMEOUT', 8))
    AMAP_WEB_API_KEY = getattr(settings, 'AMAP_WEB_API_KEY', '').strip()

    @classmethod
    def reverse_geocode(cls, latitude, longitude):
        cache_key = f'reverse_geo_{round(latitude, 4)}_{round(longitude, 4)}'
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data

        providers = cls._provider_chain(latitude, longitude)
        for provider in providers:
            location_data = cls._reverse_geocode_with_provider(provider, latitude, longitude)
            if location_data:
                cache.set(cache_key, location_data, 86400)
                return location_data
        return None

    @classmethod
    def _provider_chain(cls, latitude, longitude):
        provider = cls.PROVIDER or 'auto'
        is_china = cls._is_china_coordinate(latitude, longitude)
        if provider == 'amap':
            return ('amap', 'nominatim', 'bigdatacloud')
        if provider == 'nominatim':
            return ('nominatim', 'bigdatacloud')
        if provider == 'bigdatacloud':
            return ('bigdatacloud',)
        if is_china and cls.AMAP_WEB_API_KEY:
            return ('amap', 'nominatim', 'bigdatacloud')
        return ('nominatim', 'bigdatacloud')

    @classmethod
    def _reverse_geocode_with_provider(cls, provider, latitude, longitude):
        try:
            if provider == 'amap':
                return cls.reverse_geocode_amap(latitude, longitude)
            if provider == 'bigdatacloud':
                return cls.reverse_geocode_secondary(latitude, longitude)
            return cls.reverse_geocode_nominatim(latitude, longitude)
        except requests.RequestException as e:
            logger.warning(f"{provider} 反向地理编码请求失败: {latitude},{longitude} - {str(e)}")
            return None
        except Exception as e:
            logger.warning(f"{provider} 反向地理编码处理失败: {latitude},{longitude} - {str(e)}")
            return None

    @classmethod
    def reverse_geocode_nominatim(cls, latitude, longitude):
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
                timeout=cls.TIMEOUT,
            )
            response.raise_for_status()
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
            return location_data
        except requests.RequestException:
            raise
        except Exception:
            raise

    @classmethod
    def reverse_geocode_amap(cls, latitude, longitude):
        if not cls.AMAP_WEB_API_KEY:
            return None
        response = requests.get(
            cls.AMAP_REVERSE_URL,
            params={
                'location': f'{longitude},{latitude}',
                'key': cls.AMAP_WEB_API_KEY,
                'extensions': 'base',
                'batch': 'false',
                'roadlevel': 0,
                'output': 'JSON',
            },
            timeout=cls.TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if str(data.get('status')) != '1':
            info = data.get('info') or data.get('infocode') or 'unknown_error'
            raise ValueError(f'amap_reverse_geocode_failed:{info}')

        regeocode = data.get('regeocode') or {}
        address = regeocode.get('addressComponent') or {}
        city = address.get('city', '')
        if isinstance(city, list):
            city = city[0] if city else ''

        location_data = {
            'country': address.get('country', '中国') or '中国',
            'region': address.get('province', ''),
            'city': city or address.get('province', ''),
            'district': address.get('district', ''),
            'township': address.get('township', ''),
            'latitude': latitude,
            'longitude': longitude,
            'timezone': '',
        }
        location_data = ChinaAddressNormalizer.normalize(location_data)
        if cls._needs_secondary_lookup(location_data):
            fallback = cls.reverse_geocode_secondary(latitude, longitude)
            if fallback:
                location_data = fallback
        return location_data

    @staticmethod
    def _is_china_coordinate(latitude, longitude):
        return 18 <= latitude <= 54 and 73 <= longitude <= 135

    @classmethod
    def reverse_geocode_secondary(cls, latitude, longitude):
        try:
            return cls._reverse_geocode_secondary(latitude, longitude)
        except requests.RequestException as e:
            logger.warning(f"bigdatacloud 反向地理编码请求失败: {latitude},{longitude} - {str(e)}")
            return None
        except Exception as e:
            logger.warning(f"bigdatacloud 反向地理编码处理失败: {latitude},{longitude} - {str(e)}")
            return None

    @classmethod
    def _reverse_geocode_secondary(cls, latitude, longitude):
        response = requests.get(
            cls.BIGDATA_REVERSE_URL,
            params={
                'latitude': latitude,
                'longitude': longitude,
                'localityLanguage': 'zh',
            },
            timeout=cls.TIMEOUT,
        )
        response.raise_for_status()
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
