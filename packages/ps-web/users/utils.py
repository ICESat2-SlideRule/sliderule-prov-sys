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
from .models import Cluster, GranChoice, Membership, OrgAccount, OrgCost, User, OrgNumNode, PsCmdResult
import requests
from api.tokens import OrgRefreshToken
from api.serializers import MembershipSerializer
from rest_framework_simplejwt.settings import api_settings
from django_celery_results.models import TaskResult
from .tasks import get_ps_versions, get_org_queue_name_str, get_org_queue_name, forever_loop_main_task, getGranChoice, set_PROVISIONING_DISABLED, get_PROVISIONING_DISABLED, RedisInterface
from oauth2_provider.models import AbstractApplication
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

def get_db_org_cost(gran, orgAccountObj):
    granObj = getGranChoice(granularity=gran)
    LOG.info("%s %s", orgAccountObj.name,granObj.granularity)
    try:
        orgCost_qs0 = OrgCost.objects.filter(org=orgAccountObj)
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
        emsg = orgAccountObj.name + " " + gran+" report does not exist?"
        LOG.error(emsg)
        return False, None
    except Exception as e:
        emsg = orgAccountObj.name + " " + gran+" report does not exist?"
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



def getConsoleText(orgAccountObj, rrsp):
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

def create_org_queue(orgAccountObj):
    hostname = socket.gethostname()
    LOG.info(f"hostname:{hostname}")
    qn = get_org_queue_name_str(orgAccountObj.name)
    LOG.info(f"creating queue {qn}")
    SHELL_CMD=f"celery -A ps_web worker -n {orgAccountObj.name}@{hostname} -l error -E -Q {qn} --concurrency=1".split(" ")
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
    redis_interface = RedisInterface()

    set_PROVISIONING_DISABLED(redis_interface,'False')
    LOG.info(f"get_PROVISIONING_DISABLED:{get_PROVISIONING_DISABLED(redis_interface)}")

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

    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
    for orgAccountObj in orgs_qs:
        try:
            if orgAccountObj.name == 'uninitialized':
                LOG.error(f"Ignoring uninitialized OrgAccount.id:{OrgAccount.id}")
            else:
                p = create_org_queue(orgAccountObj)
                orgAccountObj.loop_count = 0 # reset this but not the others
                orgAccountObj.num_ps_cmd = 0
                orgAccountObj.num_ps_cmd_successful = 0
                orgAccountObj.num_owner_ps_cmd = 0
                orgAccountObj.num_onn = 0
                orgAccountObj.save(update_fields=['loop_count','num_ps_cmd','num_ps_cmd_successful','num_owner_ps_cmd','num_onn'])
                loop_count = orgAccountObj.loop_count
                LOG.info(f"Entering forever loop for {orgAccountObj.name} at loop_count:{orgAccountObj.loop_count} num_ps_cmd:{orgAccountObj.num_ps_cmd_successful}/{orgAccountObj.num_ps_cmd} num_onn:{orgAccountObj.num_onn}")
                clusterObj = Cluster.objects.get(org=orgAccountObj)
                clusterObj.provision_env_ready = False # this forces a SetUp 
                clusterObj.save(update_fields=['provision_env_ready'])
                LOG.info(f"Setting provision_env_ready to False to force initialization for {orgAccountObj.name} at loop_count:{orgAccountObj.loop_count} num_ps_cmd:{orgAccountObj.num_ps_cmd_successful}/{orgAccountObj.num_ps_cmd} num_onn:{orgAccountObj.num_onn}")
                forever_loop_main_task.apply_async((orgAccountObj.name,loop_count),queue=get_org_queue_name(orgAccountObj))
        except Exception as e:
            LOG.error(f"Caught an exception creating queues: {e}")
        LOG.info(f"forked subprocess--> {SHELL_CMD}")

def get_ps_server_versions():
    '''
        This will call the ps_server and get GIT and sw versions then set env variable to use by views.py
    '''
    PS_SERVER_DOCKER_TAG="unknown"
    PS_SERVER_GIT_VERSION="unknown"
    try:
        #LOG.info(f"{os.environ}")
        EFILE=os.path.join('/tmp', '.ps_server_versions') # temporary file to store env vars
        open(file=EFILE,mode='w').write(str(get_ps_versions()))
        environ.Env.read_env(env_file=EFILE)
        PS_SERVER_DOCKER_TAG = os.environ.get("PS_SERVER_DOCKER_TAG")
        PS_SERVER_GIT_VERSION = os.environ.get("PS_SERVER_GIT_VERSION")
        LOG.info("environ PS_SERVER_DOCKER_TAG:%s",PS_SERVER_DOCKER_TAG)
        LOG.info("environ PS_SERVER_GIT_VERSION:%s",PS_SERVER_GIT_VERSION)
    except Exception as e:
        PS_SERVER_DOCKER_TAG ="unknown"
        PS_SERVER_GIT_VERSION = "unknown"
        LOG.exception("caught exception:")
    #LOG.info(f"{os.environ}")
    return PS_SERVER_DOCKER_TAG,PS_SERVER_GIT_VERSION

def get_ps_server_versions_from_env():
    '''
        This will call the ps_server and get GIT and sw versions then set env variable to use by views.py
    '''
    PS_SERVER_DOCKER_TAG="unknown"
    PS_SERVER_GIT_VERSION="unknown"
    EFILE=os.path.join('/tmp', '.ps_server_versions') # temporary file to store env vars
    try:
        environ.Env.read_env(env_file=EFILE)
        PS_SERVER_DOCKER_TAG = os.environ.get("PS_SERVER_DOCKER_TAG",'unknown')
        PS_SERVER_GIT_VERSION = os.environ.get("PS_SERVER_GIT_VERSION",'unknown')
        #LOG.info("environ PS_SERVER_DOCKER_TAG:%s",PS_SERVER_DOCKER_TAG)
        #LOG.info("environ PS_SERVER_GIT_VERSION:%s",PS_SERVER_GIT_VERSION)
    except FileNotFoundError:
        LOG.info(f"{EFILE} does not exist;  calling ps_server to get versions")
        try:
            # This should only happen once after the web server is started. 
            # The file is fetched in get_ps_server_versions() if it does not exist
            get_ps_server_versions()            
        except Exception as e:
            LOG.exception("caught exception:")
            raise
    except Exception as e:
        LOG.exception("caught exception:")

    #LOG.info(f"{os.environ}")
    return PS_SERVER_DOCKER_TAG,PS_SERVER_GIT_VERSION

def get_memberships(request):
    membershipObjs = Membership.objects.filter(user=request.user,active=True)
    memberships = []
    for m in membershipObjs:
        if m.org is not None:
            memberships.append(m.org.name)
    return memberships

def user_in_one_of_these_groups(user,groups):
    for group in groups:
        if user.groups.filter(name=group).exists():
            return True
    return False

def has_admin_privilege(user,orgAccountObj):
    has_privilege = user_in_one_of_these_groups(user,[f'{orgAccountObj.name}_Admin','PS_Developer']) or (user == orgAccountObj.owner)
    LOG.info(f"has_admin_privilege: {has_privilege} user:{user} orgAccountObj.owner:{orgAccountObj.owner} orgAccountObj.name:{orgAccountObj.name}")
    return has_privilege