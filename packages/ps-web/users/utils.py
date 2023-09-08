import json
import logging
import os
import socket
import redis
from dateutil import tz
import grpc
import ps_server_pb2
import ps_server_pb2_grpc
import environ
from pathlib import Path
from django.conf import settings
from datetime import date, datetime, timedelta, timezone, tzinfo
import subprocess
from users.ps_errors import ShortExpireTimeError,UnknownUserError,ClusterDeployAuthError

from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
import grpc
from users import ps_client
from .models import NodeGroup, Membership, OrgAccount, Cost
import requests
from api.tokens import OrgRefreshToken
from api.serializers import MembershipSerializer
from rest_framework_simplejwt.settings import api_settings
from django_celery_results.models import TaskResult
from .tasks import get_ps_versions, get_node_group_queue_name_str, get_node_group_queue_name, forever_loop_main_task, getGranChoice, set_PROVISIONING_DISABLED, get_PROVISIONING_DISABLED, init_new_org_memberships
from oauth2_provider.models import Application
from users.global_constants import *


LOG = logging.getLogger('django')
VERSIONS = []
FULL_FMT = "%Y-%m-%dT%H:%M:%SZ"
DAY_FMT = "%Y-%m-%d"
MONTH_FMT = "%Y-%m"



def paginateObjs(request, objs, results):
    page = request.GET.get('page')
    paginator = Paginator(objs, results)

    try:
        objs = paginator.page(page)
    except PageNotAnInteger:
        page = 1
        objs = paginator.page(page)
    except EmptyPage:
        page = paginator.num_pages
        objs = paginator.page(page)

    leftIndex = (int(page) - 4)

    if leftIndex < 1:
        leftIndex = 1

    rightIndex = (int(page) + 5)

    if rightIndex > paginator.num_pages:
        rightIndex = paginator.num_pages + 1

    custom_range = range(leftIndex, rightIndex)

    return custom_range, objs


def searchOrgAccounts(request):
    search_query = ''

    if request.GET.get('search_query'):
        search_query = request.GET.get('search_query')

    objs = OrgAccount.objects.distinct().filter(
        Q(name__icontains=search_query)
    )

    return objs, search_query


def searchMemberships(request):
    search_query = ''

    if request.GET.get('search_query'):
        search_query = request.GET.get('search_query')

    objs = Membership.objects.distinct().filter(
        Q(owner__icontains=search_query)
    )

    return objs, search_query

def get_db_cluster_cost(gran, nodeGroupObj):
    granObj = getGranChoice(granularity=gran)
    LOG.info(f"{nodeGroupObj} {granObj.granularity}")
    try:
        orgCost_qs0 = Cost.objects.filter(object_id=nodeGroupObj.id)
        # LOG.info(repr(orgCost_qs0))
        # LOG.info(orgCost_qs0[0].org.id)
        # LOG.info(orgCost_qs0[0].org.name)
        # LOG.info(orgCost_qs0[0].tm)
        # LOG.info(orgCost_qs0[0].gran)
        # LOG.info(granObj.granularity)
        orgCostObj = orgCost_qs0.get(gran=granObj.granularity)
        #LOG.info(repr(orgCostObj))
        return True, orgCostObj
    except ObjectDoesNotExist as e:
        emsg = f"{nodeGroupObj} {gran} report does not exist?"
        LOG.error(emsg)
        return False, None
    except Exception as e:
        emsg = f"{nodeGroupObj} {gran} report does not exist?"
        LOG.exception(emsg)
        return False, None


def check_MFA_code(mfa_code,orgAccountObj):
    return (mfa_code == orgAccountObj.mfa_code) # TBD add two factor device stuff

 
def testit():
    try:
        LOG.info(datetime.now().astimezone().tzinfo)
        LOG.info(datetime.now())
        LOG.info(datetime.now(tz=datetime.now().astimezone().tzinfo))
        LOG.info(datetime.now(tz=timezone.utc))
    except Exception as e:
        LOG.error(f"Caught an exception: {e}")       

#####################################################################



def getConsoleText(rrsp):
    console_text = ''
    has_error = False
    if(rrsp.cli.valid):
        if(rrsp.cli.cmd_args != ''):
            console_text += rrsp.cli.cmd_args
        if(rrsp.cli.stdout != ''):
            console_text += rrsp.cli.stdout
        if(rrsp.cli.stderr != ''):
            console_text += rrsp.cli.stderr
    if(rrsp.ps_server_error):
        LOG.error("Error in server:\n %s", rrsp.error_msg)
        has_error = True
    return has_error, console_text

def get_new_tokens(org):
    #LOG.info(org.name)
    refresh     = OrgRefreshToken.for_user(org.owner,org.name)
    #LOG.info(str(refresh))
    this_org    = OrgAccount.objects.get(name=org)
    # this next line will throw and exception if membership does not exist
    membership  = Membership.objects.filter(org=this_org).get(user=org.owner)
    serializer  = MembershipSerializer(membership, many=False)
    #LOG.info(serializer.data['active'])
    if not serializer.data['active']:
        LOG.warning("Membership exists but user not an active member?")
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'refresh_lifetime': str(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        'access_lifetime': str(api_settings.ACCESS_TOKEN_LIFETIME.total_seconds()),
    }

def create_node_group_queue(nodeGroupObj):
    hostname = socket.gethostname()
    LOG.info(f"hostname:{hostname}")
    qn = get_node_group_queue_name_str(nodeGroupObj.__str__()) # Unique queue name for this cluster with org prefix
    LOG.info(f"creating queue {qn}")
    SHELL_CMD=f"celery -A ps_web worker -n {qn}@{hostname} -l error -E -Q {qn} --concurrency=1".split(" ")
    LOG.info(f"subprocess--> {SHELL_CMD}")
    return subprocess.Popen(SHELL_CMD)

# init_celery is run from docker-entrypoint.sh using Django manage.py custom command
def init_celery():
    LOG.critical("environ DEBUG:%s",os.environ.get("DEBUG"))
    LOG.critical("environ DOCKER_TAG:%s",os.environ.get("DOCKER_TAG"))
    LOG.critical("environ GIT_VERSION:%s",os.environ.get("GIT_VERSION"))
    LOG.critical(f"environ PS_WEB_LOG_LEVEL:{os.environ.get('PS_WEB_LOG_LEVEL')}")
    LOG.critical(f"DOMAIN:{os.environ.get('DOMAIN')} {type(os.environ.get('DOMAIN'))}")

    f = open('requirements.freeze.txt')
    LOG.info(f"{f.read()}")

    hostname = socket.gethostname()
    LOG.info(f"hostname:{hostname}")
    domain = os.environ.get("DOMAIN")

    set_PROVISIONING_DISABLED('False')
    LOG.info(f"get_PROVISIONING_DISABLED:{get_PROVISIONING_DISABLED()}")

    if 'localhost' in domain: 
        SHELL_CMD=f"celery -A ps_web flower --url_prefix=flower".split(" ")
        LOG.info(f"subprocess--> {SHELL_CMD}")
        subprocess.Popen(SHELL_CMD)

    SHELL_CMD=f"celery -A ps_web worker -n default@{hostname} -l error -E -Q default".split(" ")
    LOG.info(f"subprocess--> {SHELL_CMD}")
    subprocess.Popen(SHELL_CMD)

    SHELL_CMD = f"celery -A ps_web beat -l error --scheduler django_celery_beat.schedulers:DatabaseScheduler".split(" ")
    LOG.info(f"subprocess--> {SHELL_CMD}")
    subprocess.Popen(SHELL_CMD)

    ng_qs = NodeGroup.objects.all()
    LOG.info("ng_qs:%s", repr(ng_qs))
    for nodeGroupObj in ng_qs:
        try:
            if nodeGroupObj.name == 'uninitialized':
                LOG.error(f"Ignoring uninitialized OrgAccount.id:{OrgAccount.id}")
            else:
                p = create_node_group_queue(nodeGroupObj)
                nodeGroupObj.loop_count = 0 # reset this but not the others
                nodeGroupObj.num_ps_cmd = 0
                nodeGroupObj.num_ps_cmd_successful = 0
                nodeGroupObj.num_owner_ps_cmd = 0
                nodeGroupObj.num_onn = 0
                nodeGroupObj.save(update_fields=['loop_count','num_ps_cmd','num_ps_cmd_successful','num_owner_ps_cmd','num_onn'])
                loop_count = nodeGroupObj.loop_count
                LOG.info(f"Entering forever loop for {nodeGroupObj} at loop_count:{nodeGroupObj.loop_count} num_ps_cmd:{nodeGroupObj.num_ps_cmd_successful}/{nodeGroupObj.num_ps_cmd} num_onn:{nodeGroupObj.num_onn}")
                nodeGroupObj.provision_env_ready = False # this forces a SetUp 
                nodeGroupObj.save(update_fields=['provision_env_ready'])
                LOG.info(f"Setting provision_env_ready to False to force initialization for {nodeGroupObj} at loop_count:{nodeGroupObj.loop_count} num_ps_cmd:{nodeGroupObj.num_ps_cmd_successful}/{nodeGroupObj.num_ps_cmd} num_onn:{nodeGroupObj.num_onn}")
                forever_loop_main_task.apply_async((nodeGroupObj.__str__(),loop_count),queue=get_node_group_queue_name(nodeGroupObj))
        except Exception as e:
            LOG.error(f"Caught an exception creating queues: {e}")
        LOG.info(f"forked subprocess--> {SHELL_CMD}")

def get_memberships(request):
    membershipObjs = Membership.objects.filter(user=request.user,active=True)
    memberships = []
    for m in membershipObjs:
        if m.org is not None:
            memberships.append(m.org.name)
    return memberships
def add_obj_cost(f):
    '''
        This adds an OrgAccount or NodeGroup object and the associated Cost objects
    '''
    emsg=''
    msg=''
    p=None
    new_obj=None
    try:
        start_main_loop = start_main_loop or False
        init_accounting_tm = datetime.now(timezone.utc)-timedelta(days=366) # force update
        if f.is_valid():
            new_obj = f.save(commit=False)
            new_obj.most_recent_charge_time=init_accounting_tm
            new_obj.most_recent_credit_time=init_accounting_tm
            new_obj.save()
            granObjHr = getGranChoice(granularity="HOURLY")
            orgCostHr = Cost.objects.create(content_object=new_obj, gran=granObjHr, tm=init_accounting_tm, cost_refresh_time=init_accounting_tm)
            #LOG.info(orgCostHr.tm)
            orgCostHr.save()
            granObjDay = getGranChoice(granularity="DAILY")
            orgCostDay = Cost.objects.create(content_object=new_obj, gran=granObjDay, tm=init_accounting_tm, cost_refresh_time=init_accounting_tm)
            orgCostDay.save()
            granObjMonth = getGranChoice(granularity="MONTHLY")
            orgCostMonth = Cost.objects.create(content_object=new_obj, gran=granObjMonth, tm=init_accounting_tm, cost_refresh_time=init_accounting_tm)
            orgCostMonth.save()
            LOG.info(f"added obj:{new_obj.name}")
        else:
            emsg = f"Input Errors:{f.errors.as_text}"
    except Exception as e:
        LOG.exception("caught exception:")
        emsg = "Caught exception:"+repr(e)
    
    return new_obj,msg,emsg

def add_org_cost(f):
    new_org,msg,emsg = add_obj_cost(f)
    msg = init_new_org_memberships(new_org)
    return new_org,msg,emsg

def add_node_group_cost(f,start_main_loop=False):
    new_node_group,msg,emsg = add_obj_cost(f)
    p = create_node_group_queue(new_node_group)
    if start_main_loop:
        forever_loop_main_task.apply_async((new_node_group.__str__(),0),queue=get_node_group_queue_name(new_node_group))
    if new_node_group.name == 'compute':
        # always add this to the OAUTH app (only one exists anyway)
        for app in Application.objects.all():
            domain = os.environ.get("DOMAIN")
            # new_node_group.__str__() has <org_name>-<cluster_name>
            app.redirect_uris += '\n{}'.format(f"https://{new_node_group.__str__()}.{domain}/redirect_uri/")
            app.save()
    return new_node_group,msg,emsg
