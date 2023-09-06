from __future__ import absolute_import, unicode_literals

from django_celery_results import views as celery_views

# Uncomment the next two lines to enable the admin:
from django.contrib import admin
from django.http import Http404
from django.urls import include, re_path
from django.urls import path
from . import views
from django.conf import settings
from django.views.generic.base import TemplateView
from django.contrib.sites.models import Site

import os
import logging

LOG = logging.getLogger('django')

urlpatterns = [
     path('cancel_membership/<str:pk>/', views.cancelMembership, name="cancel-membership"),
     path('browse/', views.browse, name="browse"),
     path('org_manage_members/<str:pk>/', views.orgManageMembers, name="org-manage-members"),
     path('org_create/', views.orgAccountCreate,  name="org-create"),
     path('org_destroy/<str:pk>/', views.orgAccountDestroy, name="org-destroy"),
     path('org_config/<str:pk>/', views.orgConfig, name="org-config"),
     path('org_account_forecast/<str:pk>/', views.orgAccountForecast, name="org-account-forecast"),
     path('org_account_history/<str:pk>/', views.orgAccountHistory, name="org-account-history"),
     path('ajax_org_account_forecast/', views.ajaxOrgAccountForecast, name="ajax-org-account-forecast"),
     path('ajax_org_account_history/', views.ajaxOrgAccountHistory, name="ajax-org-account-history"),
     path('cluster_create/', views.clusterCreate, name="cluster-create"),
     path('cluster_destroy/<str:pk>/', views.clusterDestroy, name="cluster-destroy"),
     path('cluster_manage/<str:pk>/', views.clusterManage, name="org-manage-cluster"),
     path('cluster_configure/<str:pk>/', views.clusterConfigure, name="cluster-configure"),
     path('cluster_refresh/<str:pk>/', views.clusterRefresh, name="cluster-refresh"),
     path('cluster_account_forecast/<str:pk>/', views.clusterAccountForecast, name="cluster-account-forecast"),
     path('cluster_account_history/<str:pk>/', views.clusterAccountHistory, name="cluster-account-history"),
     path('ajax_cluster_account_forecast/', views.ajaxClusterAccountForecast, name="ajax-org-account-forecast"),
     path('ajax_cluster_account_history/', views.ajaxClusterAccountHistory, name="ajax-org-account-history"),
     path('clear_num_nodes_reqs/<str:pk>/', views.clearNumNodesReqs, name='clear-num-nodes-reqs'),
     path('clear_active_num_node_req/<str:pk>/', views.clearActiveNumNodeReq, name='clear-active-num-node-req'),
     path('req_new_membership/<str:pk>/', views.reqNewMembership,name="req-new-membership-account"),
     path('accounts/profile/', views.userProfile, name="user-profile"),
     path('prov_sys_admin/', views.provSysAdmin, name="prov-sys-admin"),
     path('disable_provisioning/', views.disableProvisioning, name="disable-provisioning"),
     re_path(r'^(?P<task_id>[\w\d\-]+)/done/?$', celery_views.is_task_successful, name='celery-is-task-successful'),
     re_path(r'^(?P<task_id>[\w\d\-]+)/status/?$', celery_views.task_status, name='celery-task-status'),
     path('', views.browse, name="browse")
]

LOG.info("settings.DEBUG:%s",settings.DEBUG)
LOG.info("settings.GIT_VERSION:%s",settings.GIT_VERSION)
LOG.info("settings.DOCKER_TAG:%s",settings.DOCKER_TAG)
LOG.info("settings.PS_VERSION:%s",settings.PS_VERSION)
LOG.info("settings.PS_SITE_TITLE:%s",settings.PS_SITE_TITLE)
LOG.info("settings.PS_BLD_ENVVER:%s",settings.PS_BLD_ENVVER)
LOG.info("settings.ALLOWED_HOSTS:%s",settings.ALLOWED_HOSTS)
LOG.info("settings.CSRF_TRUSTED_ORIGINS:%s",settings.CSRF_TRUSTED_ORIGINS)
LOG.info("settings.LOGGING.handlers.console.level:%s",settings.LOGGING['handlers']['console']['level'])
LOG.info(f"settings.SITE_ID:{settings.SITE_ID} {type(settings.SITE_ID)} must match one of the following site[n].id ...")
ndx = 0
try:
     for site in Site.objects.all():
          LOG.info(f"site[{ndx}] id:{site.id} {type(site.id)} name:{site.name} domain:{site.domain}")
          ndx = ndx+1
except Exception as e:
     LOG.exception("caught exception:")
