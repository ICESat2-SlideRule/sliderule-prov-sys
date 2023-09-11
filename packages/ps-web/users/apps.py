from django.apps import AppConfig

import logging
LOG = logging.getLogger('django')


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'
    
    def ready(self):
        # can add code here if needed
        LOG.info("Users Ready")
        from django.contrib.sites.models import Site
        ndx = 0
        try:
            for site in Site.objects.all():
                LOG.info(f"site[{ndx}] id:{site.id} {type(site.id)} name:{site.name} domain:{site.domain}")
                ndx = ndx+1
        except Exception as e:
            LOG.exception("caught exception:")
