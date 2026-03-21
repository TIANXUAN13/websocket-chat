import logging
import re

import requests
from django.core.cache import cache

from .location_normalizer import ChinaAddressNormalizer
from .reverse_geocode_service import GlobalReverseGeocodeService

logger = logging.getLogger(__name__)


class UserLocationService:
    """用户位置服务：全球反向定位 + 中国地址增强 + IP兜底"""

    API_URL_TEMPLATE = 'http://ip-api.com/json/{ip_address}'
    API_FIELDS = 'status,message,country,regionName,city,district,lat,lon,timezone'
    API_LANGUAGE = 'zh-CN'
    CJK_RE = re.compile(r'[\u4e00-\u9fff]')

    @classmethod
    def get_client_ip(cls, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')

    @classmethod
    def get_location_by_ip(cls, ip_address):
        if not ip_address or ip_address in ['127.0.0.1', 'localhost', '::1'] or ip_address.startswith('192.168.'):
            return None

        cache_key = f'geoip_{ip_address}'
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"从缓存获取地理位置: {ip_address}")
            return cached_data

        try:
            response = requests.get(
                cls.API_URL_TEMPLATE.format(ip_address=ip_address),
                params={
                    'fields': cls.API_FIELDS,
                    'lang': cls.API_LANGUAGE,
                },
                timeout=5,
            )
            data = response.json()
            if data['status'] != 'success':
                logger.warning(f"获取地理位置失败: {ip_address} - {data.get('message', '未知错误')}")
                return None

            location_data = {
                'country': data.get('country', ''),
                'region': data.get('regionName', ''),
                'city': data.get('city', ''),
                'district': data.get('district', ''),
                'township': '',
                'latitude': data.get('lat', 0.0),
                'longitude': data.get('lon', 0.0),
                'timezone': data.get('timezone', ''),
            }
            location_data = ChinaAddressNormalizer.normalize(location_data)
            cache.set(cache_key, location_data, 86400)
            logger.info(f"成功获取IP地理位置: {ip_address} -> {location_data['city']}, {location_data['country']}")
            return location_data
        except requests.RequestException as e:
            logger.error(f"请求IP地理位置API失败: {ip_address} - {str(e)}")
            return None
        except Exception as e:
            logger.error(f"处理IP地理位置数据失败: {ip_address} - {str(e)}")
            return None

    @classmethod
    def reverse_geocode(cls, latitude, longitude):
        return GlobalReverseGeocodeService.reverse_geocode(latitude, longitude)

    @classmethod
    def save_user_location(cls, user, ip_address):
        location_data = cls.get_location_by_ip(ip_address)
        if not location_data:
            return False
        return cls._upsert_user_location(user, location_data, ip_address=ip_address)

    @classmethod
    def save_precise_user_location(cls, user, latitude, longitude, ip_address=''):
        location_data = cls.reverse_geocode(latitude, longitude)
        if not location_data:
            return False
        return cls._upsert_user_location(user, location_data, ip_address=ip_address)

    @classmethod
    def _upsert_user_location(cls, user, location_data, ip_address=''):
        try:
            from ..models import UserLocation
            UserLocation.objects.update_or_create(
                user=user,
                defaults={
                    'ip_address': ip_address or getattr(getattr(user, 'location', None), 'ip_address', '127.0.0.1'),
                    'country': location_data.get('country', ''),
                    'region': location_data.get('region', ''),
                    'city': location_data.get('city', ''),
                    'district': location_data.get('district', ''),
                    'township': location_data.get('township', ''),
                    'latitude': location_data.get('latitude', 0.0),
                    'longitude': location_data.get('longitude', 0.0),
                    'timezone': location_data.get('timezone', ''),
                }
            )
            return True
        except Exception as e:
            logger.error(f"保存用户地理位置失败: {user.username} - {str(e)}")
            return False

    @classmethod
    def has_chinese_text(cls, value):
        return bool(cls.CJK_RE.search((value or '').strip()))

    @classmethod
    def location_needs_refresh(cls, location):
        if not location:
            return False
        fields = [location.country, location.region, location.city, location.district]
        non_empty = [field for field in fields if (field or '').strip()]
        if not non_empty:
            return True
        return not any(cls.has_chinese_text(field) for field in non_empty)

    @classmethod
    def refresh_user_location_if_needed(cls, user):
        location = getattr(user, 'location', None)
        if not location or not cls.location_needs_refresh(location):
            return False
        return cls.save_user_location(user, location.ip_address)
