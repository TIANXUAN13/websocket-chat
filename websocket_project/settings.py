# websocket_project/settings.py
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 安全密钥（生产环境要修改）
SECRET_KEY = 'django-insecure-your-secret-key-here'

DEBUG = True
ALLOWED_HOSTS = ['*', 'localhost', '127.0.0.1', '.ngrok-free.app', '.ngrok.io', 'chat.6143443.xyz']

DEFAULT_CSRF_TRUSTED_ORIGINS = [
    'https://*.ngrok-free.app',
    'https://*.ngrok.io',
    'http://*.ngrok-free.app',
    'http://*.ngrok.io',
    'https://www.dongwu.eu.cc',
    'https://chat.6143443.xyz',
]
CSRF_TRUSTED_ORIGINS = list(DEFAULT_CSRF_TRUSTED_ORIGINS)

# 如果需要处理跨域
CORS_ALLOW_ALL_ORIGINS = False
DEFAULT_CORS_ALLOWED_ORIGINS = [
    'https://chat.6143443.xyz',
]
# 添加应用
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',  # 添加channels
    'chat',      # 我们的聊天应用
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'chat.origin_middleware.DynamicOriginSettingsMiddleware',
    'chat.origin_middleware.DynamicCorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'chat.middleware.CheckUserSessionMiddleware',
]

ROOT_URLCONF = 'websocket_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'chat.context_processors.site_branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'websocket_project.wsgi.application'
# 添加ASGI配置（用于WebSocket）
ASGI_APPLICATION = 'websocket_project.asgi.application'

# 数据库配置
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# 国际化
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# 静态文件
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CSRF配置
CSRF_COOKIE_SECURE = False  # 开发环境设为False，生产环境设为True
CSRF_COOKIE_HTTPONLY = False
CSRF_USE_SESSIONS = False
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7  # 7天
CSRF_COOKIE_DOMAIN = None
CSRF_COOKIE_PATH = '/'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = 'Lax'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Channels配置
CHANNEL_LAYERS = {
    'default': {
        # 使用内存作为通道层（开发环境）
        'BACKEND': 'channels.layers.InMemoryChannelLayer'

        # 生产环境可以使用Redis
        # 'BACKEND': 'channels_redis.core.RedisChannelLayer',
        # 'CONFIG': {
        #     "hosts": [('127.0.0.1', 6379)],
        # },
    }
}
