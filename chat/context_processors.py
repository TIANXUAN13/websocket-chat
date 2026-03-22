from .models import SiteConfiguration


def site_branding(request):
    site_config = SiteConfiguration.get_solo()
    if site_config is None:
        return {
            'site_config': None,
            'site_title': 'animal chat',
            'site_favicon_url': '',
        }

    return {
        'site_config': site_config,
        'site_title': site_config.resolved_site_title,
        'site_favicon_url': site_config.favicon_url,
    }
