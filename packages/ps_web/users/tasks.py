from users.models import PsCmdResult,OwnerPSCmd
#from django_celery_results.models import TaskResult
from typing import Optional, Union
from redis import Redis
from redis.lock import Lock
from rq.job import Job
from rq import Queue, Worker
from rq.connections import NoRedisConnectionException
from django.db.models import F
from users.models import OrgAccount,PsCmdResult, Cluster, GranChoice, OrgAccount, OrgCost, User, OrgNumNode, PsCmdResult, Membership
from django.core.exceptions import ValidationError
from datetime import date, datetime, timedelta, timezone, tzinfo
import grpc
import pytz
import sys
import time
from pprint import pformat
from users import ps_client
import ps_server_pb2
import ps_server_pb2_grpc
from google.protobuf.json_format import MessageToJson
from django_pglocks import advisory_lock
import os
import json
from ansi2html import Ansi2HTMLConverter
import subprocess
from users.ps_errors import *
import calendar
from django.db.models import Max, Sum
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import get_user_model
from decimal import *
from uuid import UUID
from time import sleep
from users.global_constants import *
import redis
import json
import logging
from django.core.cache import cache
from rq_scheduler import Scheduler
import django_rq


LOG = logging.getLogger('django')

_scheduler = None

def get_scheduler():
    global _scheduler
    if _scheduler is None:
        LOG.info("Creating RQ scheduler")
        sched_queue = django_rq.get_queue('scheduled')
        _scheduler = Scheduler(queue=sched_queue, connection=sched_queue.connection)
    return _scheduler


def check_redis(log_label):
    try:
        client = cache.client.get_client()  # Get the underlying Redis client
        while not client.ping():  # Call the ping method on the Redis client
            LOG.critical(f"{log_label} waiting for redis to come up...")
            sleep(5)    # wait 5 seconds before trying again
    except Exception as e:
        LOG.critical(f"{log_label} check_redis got this exception:{str(e)}")
        sleep(5)

def set_PROVISIONING_DISABLED(val):
    try:
        if val == 'True':
            LOG.critical(f"set_PROVISIONING_DISABLED({val})")
        else:
            LOG.info(f"set_PROVISIONING_DISABLED({val})")
        cache.set('PROVISIONING_DISABLED', val)
    except Exception as e:
        LOG.critical(f"set_PROVISIONING_DISABLED({val}) failed with {e}")

def get_PROVISIONING_DISABLED():
    try:
        state = cache.get('PROVISIONING_DISABLED')
        if state is None:
            # initialize to False
            LOG.critical("PROVISIONING_DISABLED is None (connection timeout); Setting to False")
            set_PROVISIONING_DISABLED('False')
            state = 'False'
        if state == 'True':
            LOG.critical(f"PROVISIONING_DISABLED is {state}")
    except Exception as e:
        LOG.critical(f"get_PROVISIONING_DISABLED() failed with {e}")
        state = 'False'
    return state == 'True'

def get_org_queue_name_str(orgAccount_name):
    return f"ps-cmd-{orgAccount_name}"

def get_org_queue_name(orgAccountObj):
    return get_org_queue_name_str(orgAccountObj.name)

def flush_expired_refresh_tokens():
    SHELL_CMD=f"python manage.py flushexpiredtokens".split(" ")
    LOG.info(f"subprocess--> {SHELL_CMD}")
    subprocess.Popen(SHELL_CMD)

def format_onn(onn):
    return "{"+ f"{OrgAccount.objects.get(id=onn.org_id).name},{onn.user.username},{onn.desired_num_nodes},{onn.expiration.strftime('%Y-%m-%d %H:%M:%S %Z')if onn.expiration is not None else 'None'} " +"}"

def format_num_nodes_tbl(org):
    msg = '['
    n=0
    for onn in OrgNumNode.objects.filter(org=org).order_by('expiration'):
        if n != 0:
            msg = msg + ","
        msg = msg + format_onn(onn)
        n = n + 1
    msg = msg + "]"
    return msg 

def sort_ONN_by_nn_exp(orgAccountObj):
    return OrgNumNode.objects.filter(org=orgAccountObj).order_by('-desired_num_nodes','expiration')

def log_highest_nodes_per_user(highest_nodes_per_user,orgAccountObj):
    msg = '['
    n=0
    for entry in highest_nodes_per_user:
        #LOG.critical(f"entry:{entry}")
        if n != 0:
            msg = msg + ","
        msg = msg + "{" + f"org:{orgAccountObj} {User.objects.get(id=entry['user']).username},{entry['max_nodes']}" + "}"
        n = n + 1
    msg = msg + "]"
    LOG.info(f"highest_nodes_per_user for {orgAccountObj}:{msg}")

def sum_of_highest_nodes_for_each_user(orgAccountObj):
    '''
        This routine is used to determine the number of nodes to use for the cluster.
        First, fetch the maximum desired_num_nodes for each user using annotate.
        Then, filter the OrgNumNode table again to get the entries that match these maximum values for each user.
        Finally, calculate the total and gather the list of IDs.
    
    '''
    # Get the highest desired_num_nodes for each user within the provided OrgAccount instance
    highest_nodes_per_user = (OrgNumNode.objects
                              .filter(org=orgAccountObj)
                              .values('user')
                              .annotate(max_nodes=Max('desired_num_nodes')))

    # Filter the OrgNumNode table to get the entries that match these maximum values for each user within the OrgAccount
    ids_list = []
    for entry in highest_nodes_per_user:
        ids = (OrgNumNode.objects
               .filter(user_id=entry['user'], desired_num_nodes=entry['max_nodes'], org=orgAccountObj)
               .values_list('id', flat=True))
        string_ids = [str(id) for id in ids]  # Convert each UUID to string
        ids_list.extend(string_ids)
    log_highest_nodes_per_user(highest_nodes_per_user,orgAccountObj)
    # Sum up the highest nodes for all users within the OrgAccount
    num_nodes_to_deploy = sum(entry['max_nodes'] for entry in highest_nodes_per_user)
    if (int(num_nodes_to_deploy) < orgAccountObj.min_node_cap):
        #LOG.info(f"Clamped num_nodes_to_deploy to min_node_cap:{orgAccountObj.min_node_cap} from {num_nodes_to_deploy}")
        num_nodes_to_deploy = orgAccountObj.min_node_cap
    if(int(num_nodes_to_deploy) > orgAccountObj.max_node_cap):
        #LOG.info(f"Clamped num_nodes_to_deploy to max_node_cap:{orgAccountObj.max_node_cap} from {num_nodes_to_deploy}")
        num_nodes_to_deploy = orgAccountObj.max_node_cap
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    clusterObj.cnnro_ids = ids_list
    clusterObj.save(update_fields=['cnnro_ids'])
    return num_nodes_to_deploy, ids_list

def cull_expired_entries(org,tm):
    LOG.debug(f"started with {OrgNumNode.objects.filter(org=org).count()} OrgNumNode for {org.name}")
    for onn in OrgNumNode.objects.filter(org=org).order_by('expiration'):
        LOG.debug(f"onn.expiration:{onn.expiration} tm(now):{tm}")
        if(onn.expiration <= tm):
            LOG.info(f"deleting expired/null OrgNumNode request {format_onn(onn)}")
            onn.delete()
        else:
            LOG.debug("nothing to delete")
            break
    LOG.debug(f"ended with {OrgNumNode.objects.filter(org=org).count()} OrgNumNode for {org.name}")

def need_destroy_for_changed_version_or_is_public(orgAccountObj):
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    # LOG.debug(f"cluster v:{clusterObj.cur_version} ip:{clusterObj.is_public} is_deployed:{clusterObj.is_deployed}")
    # LOG.debug(f"    org v:{orgAccountObj.version} ip:{orgAccountObj.is_public}")
    changed_version = (clusterObj.cur_version != clusterObj.prov_env_version)
    changed_is_public = (clusterObj.is_public != clusterObj.prov_env_is_public )
    LOG.info(f"changed_version:{changed_version} changed_is_public:{changed_is_public}")
    if clusterObj.is_deployed and (changed_version or changed_is_public):
        LOG.info(f"TRIGGERED Destroy {orgAccountObj.name} --> cluster v:{clusterObj.cur_version} cluster is_public:{clusterObj.is_public} is_deployed:{clusterObj.is_deployed} org v:{orgAccountObj.version} orgAccount ip:{orgAccountObj.is_public} orgAccountObj.desired_num_nodes:{orgAccountObj.desired_num_nodes}")
        process_Destroy_cmd(orgAccountObj=orgAccountObj, username=orgAccountObj.owner.username)
        enqueue_process_state_change(orgAccountObj.name) # one for Destroy
        enqueue_process_state_change(orgAccountObj.name) # one for Update
        return True
    return False

def clean_up_ONN_cnnro_ids(orgAccountObj,suspend_provisioning):
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    if clusterObj.cnnro_ids is not None:

        # Get the list of string UUIDs from the JSONField
        string_uuids = clusterObj.cnnro_ids

        # Convert each string UUID to a UUID object
        uuids_list = [UUID(id) for id in string_uuids]

        # Fetch the OrgNumNode instances
        onns = OrgNumNode.objects.filter(id__in=uuids_list)
        LOG.info(f"REMOVING OrgNumNode clusterObj.cnnro_ids: {onns}")
        for onn in onns:
            onn.delete()
        cnt = OrgNumNode.objects.filter(org=orgAccountObj).count()
        clusterObj.cnnro_ids = []
        clusterObj.save(update_fields=['cnnro_ids'])
    if suspend_provisioning:
        orgAccountObj.provisioning_suspended = True
        orgAccountObj.save(update_fields=['provisioning_suspended'])
        LOG.warning(f"provisioning_suspended for {orgAccountObj.name}")

def check_provision_env_ready(orgAccountObj):
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    setup_occurred = False
    if not clusterObj.provision_env_ready:
        clusterObj.provision_env_ready,setup_occurred = process_SetUp_cmd(orgAccountObj=orgAccountObj)
    return clusterObj.provision_env_ready,setup_occurred       

def process_num_node_table(orgAccountObj,prior_need_refresh):
    '''
    If the the OrgNumNode table changed and the highest num nodes desired 
    in the table is different than what is currently running
    then it will send and update to set desired num nodes to value in table 
                OR 
    there are no entries left and
    the min node cap is zero 
    then it will destroy the cluster
               OR
    there are no entries left and
    the current desired num nodes is not the min node cap
    then it will set desired num nodes to min node cap 
    '''
    try:
        if not orgAccountObj.provisioning_suspended: 
            env_ready,setup_occurred = check_provision_env_ready(orgAccountObj)
            start_num_ps_cmds = orgAccountObj.num_ps_cmd
            if env_ready:
                cull_expired_entries(orgAccountObj,datetime.now(timezone.utc))
                num_nodes_to_deploy,cnnro_ids = sum_of_highest_nodes_for_each_user(orgAccountObj)
                expire_time = None
                onnTop = sort_ONN_by_nn_exp(orgAccountObj).first()
                if onnTop is not None:
                    user = onnTop.user
                    expire_time = onnTop.expiration
                    if num_nodes_to_deploy != orgAccountObj.desired_num_nodes: 
                        deploy_values ={'min_node_cap': orgAccountObj.min_node_cap, 'desired_num_nodes': num_nodes_to_deploy , 'max_node_cap': orgAccountObj.max_node_cap, 'version': orgAccountObj.version, 'is_public': orgAccountObj.is_public, 'expire_time': expire_time }
                        LOG.info(f"{orgAccountObj.name} Using top entries of each user sorted by num/exp_tm  with num_nodes_to_set:{onnTop.desired_num_nodes} exp_time:{expire_time} ")
                        try:
                            process_Update_cmd(orgAccountObj=orgAccountObj, username=user.username, deploy_values=deploy_values, expire_time=expire_time)
                        except Exception as e:
                            LOG.exception(f"{e.message} processing top ONN id:{onnTop.id} Update {orgAccountObj.name} {user.username} {deploy_values} Exception:")
                            clean_up_ONN_cnnro_ids(orgAccountObj,suspend_provisioning=False)
                            LOG.info(f"{orgAccountObj.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                            sleep(COOLOFF_SECS)
                        
                        LOG.info(f"Update {orgAccountObj.name} processed")
                else:
                    # No entries in table
                    user = orgAccountObj.owner
                    if orgAccountObj.destroy_when_no_nodes and (orgAccountObj.min_node_cap == 0):
                        clusterObj = Cluster.objects.get(org=orgAccountObj)
                        if clusterObj.is_deployed:
                            LOG.info(f"org:{orgAccountObj.name} destroy_when_no_nodes:{orgAccountObj.destroy_when_no_nodes} min_node_cap:{orgAccountObj.min_node_cap}")
                            try:
                                process_Destroy_cmd(orgAccountObj=orgAccountObj, username=user.username)
                            except Exception as e:
                                LOG.exception("ERROR processing Destroy {orgAccountObj.name} when no entries in ONN: caught exception:")
                                LOG.warning(f"Destroy {orgAccountObj.name} FAILED when no entries in ONN; Setting destroy_when_no_nodes to False")
                                orgAccountObj.destroy_when_no_nodes = False
                                orgAccountObj.min_node_cap = 0 
                                orgAccountObj.desired_num_nodes = 0
                                orgAccountObj.save(update_fields=['destroy_when_no_nodes','min_node_cap','desired_num_nodes'])
                                LOG.info(f"{orgAccountObj.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                                sleep(COOLOFF_SECS)

                            
                            LOG.info(f"{orgAccountObj.name} Destroy processed")
                    else:
                        if orgAccountObj.min_node_cap != orgAccountObj.desired_num_nodes: 
                            num_entries = OrgNumNode.objects.filter(org=orgAccountObj).count()
                            LOG.info(f"{orgAccountObj.name} ({num_entries} (i.e. no) entries left; using min_node_cap:{orgAccountObj.min_node_cap} exp_time:None")
                            deploy_values ={'min_node_cap': orgAccountObj.min_node_cap, 'desired_num_nodes': orgAccountObj.min_node_cap, 'max_node_cap': orgAccountObj.max_node_cap,'version': orgAccountObj.version, 'is_public': orgAccountObj.is_public, 'expire_time': expire_time }
                            try:
                                process_Update_cmd(orgAccountObj=orgAccountObj, username=user.username, deploy_values=deploy_values, expire_time=expire_time)
                            except Exception as e:
                                LOG.exception("ERROR in Update {orgAccountObj.name} ps_cmd when no entries in ONN and min != desired: caught exception:")
                                LOG.warning(f"Setting {orgAccountObj.name} min_node_cap to zero; Update FAILED when no entries in ONN and min_node_cap != desired_num_nodes (i.e. current target, assumed num nodes)")
                                orgAccountObj.min_node_cap = 0 
                                orgAccountObj.desired_num_nodes = 0
                                orgAccountObj.save(update_fields=['min_node_cap','desired_num_nodes'])
                                LOG.info(f"{orgAccountObj.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                                sleep(COOLOFF_SECS)

                            
                            LOG.info(f"{orgAccountObj.name} Update processed")
                # if we setup the env but did not process any commands, then we need to Refresh to set state
                need_refresh = False
                if setup_occurred:
                    if orgAccountObj.num_ps_cmd == start_num_ps_cmds:
                        # setup with no commands processed
                        need_refresh = True
                else:
                    if prior_need_refresh and (orgAccountObj.num_ps_cmd == start_num_ps_cmds):
                        # setup with no commands processed
                        need_refresh = True
                if need_refresh:
                    try:
                        LOG.info(f"Refresh {orgAccountObj.name} post SetUp")
                        process_Refresh_cmd(orgAccountObj=orgAccountObj, username=orgAccountObj.owner.username)
                    except Exception as e:
                        LOG.exception("ERROR processing Refresh {orgAccountObj.name} caught exception:")
                        LOG.info(f"{orgAccountObj.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                        sleep(COOLOFF_SECS)
    except Exception as e:
        LOG.exception(f"{orgAccountObj.name} caught exception:")
        LOG.info(f"{orgAccountObj.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
        for handler in LOG.handlers:
            handler.flush()
        sleep(COOLOFF_SECS)

def schedule_process_state_change(tm,orgAccountObj): 
    '''
    This routine is called whenever an org num node request is made with and expiration.
    It will schedule a job to process the state change for the orgAccountObj
    '''
    try:
        LOG.info(f"schedule_process_state_change({tm},{orgAccountObj.name})")
        if orgAccountObj is not None:
            if orgAccountObj.name is not None:
                if orgAccountObj.name != '':
                    LOG.debug(f"schedule_process_state_change({tm},{orgAccountObj.name})")
                    get_scheduler().enqueue_at(tm,enqueue_process_state_change,orgAccountObj.name)
                else: 
                    LOG.error(f"schedule_process_state_change({tm},orgAccountObj.name is blank)")
            else:
                LOG.error(f"schedule_process_state_change({tm},orgAccountObj.name is None)")
        else:
            LOG.error(f"schedule_process_state_change({tm},orgAccountObj is None)")
    except Exception as e:
        LOG.exception(f"{orgAccountObj.name} caught exception:")
        LOG.error(f"{orgAccountObj.name} got {str(e)}")

def get_or_create_OrgNumNodes(orgAccountObj,user,desired_num_nodes,expire_date):
    # if it doesn't exist create it then process all orgNumNodes for org                                          
    # if expire date comes from jwt then it will match
    # First try exact match
    # then create one
    orgNumNode = None
    redundant = False
    msg = ''
    try:
        orgNumNode,created = OrgNumNode.objects.get_or_create(user=user,org=orgAccountObj, desired_num_nodes=desired_num_nodes,expiration=expire_date)
        if created:
            if expire_date is not None:
                msg = f"Created new entry for {orgAccountObj.name} {user.username} desired_num_nodes {orgNumNode.desired_num_nodes} uuid:{orgNumNode.id} expiration:{expire_date.strftime(FMT) if expire_date is not None else 'None'}"
                #get_scheduler().enqueue_at(expire_date,enqueue_process_state_change,orgAccountObj.name)
                schedule_process_state_change(expire_date,orgAccountObj)
            else:
                msg = f"Created new entry for {orgAccountObj.name} {user.username} desired_num_nodes {orgNumNode.desired_num_nodes} uuid:{orgNumNode.id} with NO expiration"
        else:         
            msg = f"An entry already exists for {orgAccountObj.name} {user.username} desired_num_nodes {orgNumNode.desired_num_nodes} uuid:{orgNumNode.id} expiration:{expire_date.strftime(FMT) if expire_date is not None else 'None'}"
            redundant = True
        LOG.info(f"{msg} cnt:{OrgNumNode.objects.count()}")

    except Exception as e:
        LOG.exception("caught exception:")
        orgNumNode = None
    return orgNumNode,redundant,msg

def process_num_nodes_api(name,user,desired_num_nodes,expire_time,is_owner_ps_cmd):
    '''
        processes the APIs for setting desired num nodes
    '''
    try:
        jstatus = ''
        LOG.info(f"process_num_nodes_api({name},{user},{desired_num_nodes},{expire_time})")
        if int(desired_num_nodes) < 0:
            msg = f"desired_num_nodes:{desired_num_nodes} must be >= 0"
            raise ValidationError(msg)
        orgAccountObj = OrgAccount.objects.get(name=name)
        clusterObj = Cluster.objects.get(org=orgAccountObj)
        if (not clusterObj.is_deployed) and (not orgAccountObj.allow_deploy_by_token):
            msg = f"Org {orgAccountObj.name} is not configured to allow deploy by token"
            raise ClusterDeployAuthError(msg)
        if(not clusterObj.is_deployed):
            msg = f"Deploying {orgAccountObj.name} cluster"
        else:
            msg = f"Updating {orgAccountObj.name} cluster"

        orgNumNode,redundant,onn_msg = get_or_create_OrgNumNodes(user=user,
                                                                orgAccountObj=orgAccountObj,
                                                                desired_num_nodes=desired_num_nodes,
                                                                expire_date=expire_time)
        msg += f" {onn_msg}"
        if orgNumNode:
            if redundant:
                msg += f" using identical queued capacity request for {orgNumNode.org.name} from {orgNumNode.user.username} with {desired_num_nodes} nodes to expire:{expire_time.strftime(FMT) if expire_time is not None else 'exp_tm:None'}"
                jstatus = 'REDUNDANT'
            else:
                msg += f" created and queued capacity request for {orgNumNode.org.name} from {user.username} with {desired_num_nodes} nodes to expire:{expire_time.strftime(FMT) if expire_time is not None else 'exp_tm:None'}"
                jstatus = 'QUEUED'
            jrsp = {'status':jstatus,"msg":msg,'error_msg':''}
            status = 200
            enqueue_process_state_change(orgAccountObj.name)
        else:
            emsg = f"FAILED to process request for {name} {user} {desired_num_nodes} {expire_time} - Server Error"
            jrsp = {'status':'FAILED',"msg":'','error_msg':emsg}
            status = 500
    except ClusterDeployAuthError as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED",'msg':msg,"error_msg":""}
        status = 503
    except ValidationError as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED",'msg':msg,"error_msg":e.message}
        status = 400
    except Exception as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED",'msg':'',"error_msg":"Server Error"}
        status = 500
    LOG.info(f"status:{status} jrsp['status']:{jrsp['status']} msg:'{jrsp['msg']}' error_msg:'{jrsp['error_msg']}' ")
    return jrsp,status

def get_versions_for_org(name):
    try:
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            versions_rsp = stub.GetVersions(ps_server_pb2.GetVersionsReq(name=name))
            #LOG.info(f"versions_rsp:{versions_rsp}")
            return versions_rsp.versions
    except Exception as e:
        LOG.exception("caught exception:")

def get_ps_versions():
    LOG.info("get_ps_versions")
    try:
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            return stub.GetPSVersions(ps_server_pb2.GetPSVersionsReq()).ps_versions
    except Exception as e:
        LOG.exception("caught exception:")

def perform_cost_accounting_for_all_orgs():
    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
    for org in orgs_qs:
        cost_accounting(org)

def get_current_cost_report(name, gran, time_now):
    now_str = datetime.strftime(time_now, FMT_Z)
    LOG.info(f"{name} {gran} {now_str}")
    with ps_client.create_client_channel("account") as channel:
        ac = ps_server_pb2_grpc.AccountStub(channel)
        rsp = ac.CurrentCost(
            ps_server_pb2.CurrentCostReq(name=name, granularity=gran, tm=now_str))
    #LOG.info("Sending rsp...")
    return MessageToJson(rsp), rsp

def get_org_cost_data(orgAccountObj, granObj, orgCostObj):
    THIS_FMT = FMT_DAILY
    updated = False
    time_now = datetime.now(timezone.utc)
    time_stale = time_now - orgCostObj.cost_refresh_time
    LOG.info("%s %s now:%s/%s orgCostObj.cost_refresh_time:%s time_stale:%s > %s ?", orgAccountObj.name,
             granObj.granularity, datetime.now(timezone.utc), datetime.now(), orgCostObj.cost_refresh_time, time_stale, timedelta(hours=8))
    if time_stale > timedelta(hours=8) or str(orgCostObj.ccr) == "{}" or str(orgCostObj.ccr) == NULL_CCR:
        #LOG.info("Calling get_current_cost_report <<<<<<<<<<<-------------->>>>>>>>>>>")
        ccr, rsp = get_current_cost_report(orgAccountObj.name, granObj.granularity, time_now)
        if rsp.server_error == False:
            orgCostObj.cost_refresh_time = time_now
            orgAccountObj.most_recent_recon_time = time_now 
            if len(rsp.tm) > 0:
                if(orgCostObj.gran.granularity == 'HOURLY'):
                    THIS_FMT = FMT_Z
                most_recent_tm_str = rsp.tm[len(rsp.tm)-1]
                most_recent_tm = datetime.strptime(most_recent_tm_str, THIS_FMT).replace(tzinfo=pytz.utc)
                orgCostObj.tm = most_recent_tm
                orgCostObj.cnt = rsp.stats.cnt
                orgCostObj.avg = rsp.stats.avg
                orgCostObj.min = rsp.stats.min
                orgCostObj.max = rsp.stats.max
                orgCostObj.std = rsp.stats.std
                orgCostObj.ccr = ccr
                updated = True
                #LOG.info("Saved %s orgCostObj for:%s tm:%s ccr=%s",
                #         orgCostObj.gran.granularity,  orgCostObj.org.name, orgCostObj.tm, ccr)
                LOG.info("Saved %s orgCostObj for:%s tm:%s",
                        orgCostObj.gran.granularity,  orgCostObj.org.name, orgCostObj.tm)
            else:
                LOG.info("No cost data for %s %s",orgAccountObj.name, granObj.granularity)
                if str(orgCostObj.ccr) == "{}":
                    orgCostObj.ccr = "{ }" # so keep from reading null CCRs
            orgCostObj.save() # this only saves the updated orgCostObj.cost_refresh_time
        else:
            LOG.error(f"received error from ps_server:{rsp.error_msg}")
    return updated

def getGranChoice(granularity):
    try:
        granObj = GranChoice.objects.get(granularity=granularity)
    except GranChoice.DoesNotExist as e:
        LOG.warning(f"no GranChoice for {granularity} creating one")
        granObj = GranChoice(granularity=granularity)
    except Exception as e:
        LOG.exception("caught exception:")
        raise
    return granObj

def update_orgCost(orgAccountObj, gran):
    granObj = getGranChoice(gran)
    get_data = False
    orgCostObj = None
    try:
        orgCostObj = OrgCost.objects.get(org=orgAccountObj, gran=granObj)
    except ObjectDoesNotExist as e:
        LOG.warning(f"no orgCostObj for {orgAccountObj.name} {granObj.granularity}")
        orgCostObj = OrgCost(org=orgAccountObj, gran=granObj, cost_refresh_time=datetime.now(timezone.utc)-timedelta(weeks=52),tm=datetime.now(timezone.utc))
        LOG.info("%s %s %s New orgCostObj created", orgCostObj.org.name,orgCostObj.gran.granularity, orgCostObj.tm)
        get_data = True
    if orgCostObj is not None:
        #LOG.info(datetime.now(timezone.utc))
        diff_tm = datetime.now(timezone.utc) - orgCostObj.cost_refresh_time
        #LOG.info("%s %s %s - %s = %s", orgAccountObj.name, gran,datetime.now(timezone.utc), orgCostObj.tm, diff_tm)
        if str(orgCostObj.ccr) == "{}" or str(orgCostObj.ccr) == NULL_CCR:
            get_data = True
            LOG.info("Triggered by empty set")
        else:
            # the aws cost explorer updates 3x a day
            if diff_tm > timedelta(hours=8):
                LOG.info("Triggered by stale ccr > 8 hrs")
                get_data = True
    else:
        LOG.error("FAILED to create orgCostObj for %s %s",orgAccountObj.name, granObj.granularity)
        get_data = False
    updated = False
    if get_data:
        # will create orgCostObj if needed
        LOG.info("calling get_org_cost_data for %s %s",orgAccountObj.name, granObj.granularity)
        updated = get_org_cost_data(orgAccountObj, granObj, orgCostObj)

    next_refresh_time = orgCostObj.cost_refresh_time +  timedelta(hours=8)
    if updated:
        LOG.info("%s CCR DID     update. Last refresh was: %s next refresh will be: %s",gran,orgCostObj.cost_refresh_time , next_refresh_time )
    else:
        LOG.info("%s CCR did not update. Last refresh was: %s next refresh will be: %s",gran,orgCostObj.cost_refresh_time , next_refresh_time )

    return updated

def update_ccr(org):
    updated = (update_orgCost(org, "HOURLY") or update_orgCost(org, "DAILY") or update_orgCost(org, "MONTHLY"))
    LOG.info("updated:%s",updated)
    return updated

def update_cur_num_nodes(orgAccountObj):
    #LOG.info(f"update_cur_num_nodes:{orgAccountObj.name}")
    with ps_client.create_client_channel("account") as channel:
        try:
            clusterObj = Cluster.objects.get(org=orgAccountObj) 
            ac = ps_server_pb2_grpc.AccountStub(channel)
            region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
            req = ps_server_pb2.NumNodesReq(name=clusterObj.org.name,version=clusterObj.org.version,region=region)
            #LOG.info(req)
            rsp = ac.NumNodes(req)
            clusterObj.cur_nodes = rsp.num_nodes
            clusterObj.save(update_fields=['cur_nodes'])
            LOG.info(f"update_cur_num_nodes:{orgAccountObj.name} cur_nodes:{clusterObj.cur_nodes}")
            return clusterObj.cur_nodes
        except Exception as e:
            LOG.error(f"FAILED: caught exception on NumNodesReq")
            raise

def calculate_ddt(label, dollar_balance, dollar_allowance, dollar_hourly_burn_rate):
    # Convert all inputs to Decimal
    dollar_balance = Decimal(dollar_balance)
    start_balance = dollar_balance
    dollar_allowance = Decimal(dollar_allowance)
    dollar_hourly_burn_rate = Decimal(dollar_hourly_burn_rate)

    # Start from the current time
    current_time = datetime.now(timezone.utc)

    # Calculate the end time as 10 years from now
    end_time = current_time + timedelta(days=TEN_YEARS_IN_DAYS)

    # Log template
    log_template = (f"{label} calculate_ddt: {{time}} Starting Balance: {start_balance:.2f} Monthly Allowance: {dollar_allowance:.2f} Hourly Burn Rate: {dollar_hourly_burn_rate:.2f}")

    # Keep checking every hour until balance is 0 or 10 years have passed
    while current_time <= end_time:
        # If it's midnight on the first day of the month, add the dollar allowance
        if current_time.hour == 0 and current_time.day == 1:
            dollar_balance += dollar_allowance

        # Subtract the hourly burn rate
        dollar_balance -= dollar_hourly_burn_rate

        # If balance is 0 or less, log and return the current time
        if dollar_balance <= 0:
            formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
            LOG.info(log_template.format(time=formatted_time))
            return current_time

        # Increment current time by 1 hour
        current_time += timedelta(hours=1)

    # If past end time, log and return end time
    formatted_end_time = end_time.strftime('%Y-%m-%d %H:%M:%S')
    #LOG.info(log_template.format(time=formatted_end_time))
    return end_time

def update_ddt(orgAccountObj):
    orgAccountObj.min_ddt = calculate_ddt('min',orgAccountObj.balance, orgAccountObj.monthly_allowance, orgAccountObj.min_hrly)
    orgAccountObj.cur_ddt = calculate_ddt('cur', orgAccountObj.balance, orgAccountObj.monthly_allowance, orgAccountObj.cur_hrly)
    orgAccountObj.max_ddt = calculate_ddt('max',orgAccountObj.balance, orgAccountObj.monthly_allowance, orgAccountObj.max_hrly)
    LOG.info(f"update_ddt:{orgAccountObj.name} min_ddt:{orgAccountObj.min_ddt} cur_ddt:{orgAccountObj.cur_ddt} max_ddt:{orgAccountObj.max_ddt}")
    orgAccountObj.save(update_fields=['min_ddt','cur_ddt','max_ddt'])

def update_burn_rates(orgAccountObj):
    '''
     This routine calcuates the burn rates for minimum nodes current nodes and maximum nodes configurations.
    '''
    global FMT
    MINIMUM_HRLY_RATE = 0.0001
    try:
        update_cur_num_nodes(orgAccountObj)

        if orgAccountObj.min_node_cap > 0:
            orgAccountObj.min_hrly = orgAccountObj.node_mgr_fixed_cost + orgAccountObj.min_node_cap*orgAccountObj.node_fixed_cost
        else:
            if not orgAccountObj.destroy_when_no_nodes:
                orgAccountObj.min_hrly = max(orgAccountObj.node_mgr_fixed_cost,MINIMUM_HRLY_RATE)
            else:
                orgAccountObj.min_hrly = MINIMUM_HRLY_RATE

        clusterObj = Cluster.objects.get(org=orgAccountObj)
        if clusterObj.cur_nodes > 0:
            orgAccountObj.cur_hrly = orgAccountObj.node_mgr_fixed_cost + clusterObj.cur_nodes*orgAccountObj.node_fixed_cost
        else:
            orgAccountObj.cur_hrly = MINIMUM_HRLY_RATE

        orgAccountObj.max_hrly = orgAccountObj.node_mgr_fixed_cost + orgAccountObj.max_node_cap*orgAccountObj.node_fixed_cost

        orgAccountObj.save(update_fields=['min_hrly','cur_hrly','max_hrly'])
        #LOG.info(f"{orgAccountObj.name} forecast min/cur/max hrly burn rate {orgAccountObj.min_hrly}/{orgAccountObj.cur_hrly}/{orgAccountObj.max_hrly}")
        #LOG.info(f"{orgAccountObj.name} min_hrly: {orgAccountObj.min_hrly} cur_hrly: {orgAccountObj.cur_hrly} max_hrly: {orgAccountObj.max_hrly}")
        #LOG.info(f"{orgAccountObj.name}  min_ddt: {datetime.strftime(orgAccountObj.min_ddt, FMT)} cur_ddt: {datetime.strftime(orgAccountObj.cur_ddt, FMT)} max_ddt: {datetime.strftime(orgAccountObj.max_ddt, FMT)}")

 
    except Exception as e:
        LOG.exception('Exception caught')
        return None, None, None

def update_all_burn_rates():
    orgs_qs = OrgAccount.objects.all()
    #LOG.info("orgs_qs:%s", repr(orgs_qs))
    for o in orgs_qs:
        update_burn_rates(o)

def reconcile_all_orgs():
    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
    for o in orgs_qs:
        reconcile_org(o)

def getFiscalStartDate():
    now = datetime.now(timezone.utc)
    thisYearFSD = now.replace(month=10,day=1,hour=0,minute=0,second=0,microsecond=0) # i.e. October 1
    if now < thisYearFSD:
        return thisYearFSD.replace(year=now.year-1) 
    else:
        return thisYearFSD

def get_utc_tm(tm,tm_fmt_to_use):
    return pytz.utc.localize(datetime.strptime(tm, tm_fmt_to_use)) #localize handles daylight savings time

def calculate_account_bal_and_fytd_bal(org,rsp):
    '''
    This routine does not and should not change org 
    It is READ ONLY
    '''
    new_fytd_accrued_cost = org.fytd_accrued_cost
    fytd_date = getFiscalStartDate()
    new_mrct = org.most_recent_charge_time
    new_balance = Decimal(org.balance)
    most_recent_tm_str = rsp.tm[len(rsp.tm)-1]
    if 'Z' in most_recent_tm_str: # i.e. are these hourly timestamps
        tm_fmt_to_use = FMT_Z
    else:
        tm_fmt_to_use = FMT_DAILY
    LOG.info(f"{org.name} most_recent_tm_str:{most_recent_tm_str} tm_fmt_to_use:{tm_fmt_to_use} len(rsp.cost):{len(rsp.cost)}")
    most_recent_tm = get_utc_tm(most_recent_tm_str, tm_fmt_to_use)
    LOG.info(f"now:{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} new_mrct:{new_mrct.strftime('%Y-%m-%d %H:%M:%S')} most_recent_tm:{most_recent_tm.strftime('%Y-%m-%d %H:%M:%S')} fytd_date:{fytd_date.strftime('%Y-%m-%d %H:%M:%S')}")
    if new_mrct < fytd_date and most_recent_tm > fytd_date:
        LOG.info(f"{org.name} is resetting FYTD accrued cost (tm crossed FY boundary)")
        new_fytd_accrued_cost = Decimal(0.00)
    bal_changed = False
    fytd_changed = False
    ndx = 0
    total_current = Decimal(0.00)
    while ndx < len(rsp.cost):
        tm = get_utc_tm(rsp.tm[ndx], tm_fmt_to_use)
        cost = Decimal(rsp.cost[ndx])
        # check and see if we crossed the fiscal year boundary with charges
        if tm > new_mrct:
            bal_changed = True
            if tm > fytd_date:
                fytd_changed = True
                new_fytd_accrued_cost += Decimal(cost)
            new_mrct = tm
            #LOG.info(f"{org.name} for rsp.tm[{ndx}]:{tm} debiting {cost:.2f}")
            new_balance = new_balance - cost
            total_current = total_current + Decimal(cost) # for logging convenience
        ndx = ndx + 1
    if fytd_changed:  # use flag (avoid compare of float type?)
        LOG.info(f"{org.name} is debited (tm_fmt:{tm_fmt_to_use}) up to {datetime.strftime(org.most_recent_charge_time, FMT)} {total_current} with new new_fytd_accrued_cost:{new_fytd_accrued_cost:.2f}")
    else:
        LOG.info(f"{org.name} is debited (tm_fmt:{tm_fmt_to_use}) up to {datetime.strftime(org.most_recent_charge_time, FMT)} NO CHANGE fytd:{org.fytd_accrued_cost:.2f}")
    if bal_changed:  # use flag (avoid compare of float type?)
        LOG.info(f"{org.name} is debited (tm_fmt:{tm_fmt_to_use}) {total_current:.2f} up to {datetime.strftime(org.most_recent_charge_time, FMT)} with new balance:{new_balance:.2f}")
    else:
        LOG.info(f"{org.name} is debited (tm_fmt:{tm_fmt_to_use}) up to {datetime.strftime(org.most_recent_charge_time, FMT)} NO CHANGE balance:{org.balance:.2f}")
    return new_balance,new_fytd_accrued_cost,new_mrct

def get_tm_now_tuple():
    time_now = datetime.now(timezone.utc)
    time_now_str =  datetime.strftime(time_now, FMT_Z)
    return time_now,time_now_str

def reconcile_org(orgAccountObj):
    time_now,time_now_str = get_tm_now_tuple()
    global FMT, FMT_Z, FMT_DAILY
    with ps_client.create_client_channel("account") as channel:
        ac = ps_server_pb2_grpc.AccountStub(channel)
        # add any monthly credits due
        start_of_this_month = time_now.replace( day=1,
                                                hour=0,
                                                minute=0,
                                                second=0,
                                                microsecond=0)
        LOG.info(f"{orgAccountObj.name} now:{time_now_str} start_of_this_month:{start_of_this_month.strftime('%Y-%m-%d %H:%M:%S')} orgAccountObj.most_recent_credit_time:{orgAccountObj.most_recent_credit_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if start_of_this_month > orgAccountObj.most_recent_credit_time:
            # only ever give one month's credit
            orgAccountObj.balance = orgAccountObj.balance + orgAccountObj.monthly_allowance
            orgAccountObj.most_recent_credit_time = start_of_this_month
            LOG.info(f"{orgAccountObj.name} is credited up to {datetime.strftime(orgAccountObj.most_recent_credit_time, FMT)} with NEW balance:{orgAccountObj.balance:.2f} using {orgAccountObj.monthly_allowance:.2f} added")
        else:
            LOG.info(f"{orgAccountObj.name} is credited up to {datetime.strftime(orgAccountObj.most_recent_credit_time, FMT)} with NO CHANGE in balance:{orgAccountObj.balance:.2f} (nothing new to credit)")
        #
        # For Charges:
        # First charge every day until midnight last night, then do hourly of today
        #
        # truncating to start of day (i.e. uncharged days)
        start_of_today = time_now.replace(hour=0, minute=0, second=0, microsecond=0)
        LOG.info(f"{orgAccountObj.name} now:{time_now_str} orgAccountObj.most_recent_charge_time:{orgAccountObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')} start_of_today:{start_of_today.strftime('%Y-%m-%d %H:%M:%S')}")
        if orgAccountObj.most_recent_charge_time < start_of_today:
            # check if there are charges posted yet 
            start_tm_str    = datetime.strftime(orgAccountObj.most_recent_charge_time, FMT_Z)
            end_tm_str      = time_now_str
            #
            #  These are the Daily charges until midnight last night
            #  below is the Hourly for today
            #
            LOG.info(f"{orgAccountObj.name} now:{time_now_str} start_tm_str:{start_tm_str} end_tm_str:{end_tm_str}")
            rsp = ac.DailyHistCost(ps_server_pb2.DailyHistCostReq(name=orgAccountObj.name, start_tm=start_tm_str, end_tm=end_tm_str))
            if rsp.server_error:
                LOG.error(f"{orgAccountObj.name} DailyHistCost got this error:{rsp.error_msg}")
                raise Exception(f"ps server error caught:{rsp.error_msg}")            
            if len(rsp.tm) > 0:
                new_balance,new_fytd_accrued_cost,new_mrct = calculate_account_bal_and_fytd_bal(orgAccountObj,rsp)
                #LOG.info(f"orgAccountObj.balance:{orgAccountObj.balance} new_balance:{new_balance}")
                if orgAccountObj.balance != new_balance:
                    orgAccountObj.balance = new_balance
                #LOG.info(f"orgAccountObj.fytd_accrued_cost:{orgAccountObj.fytd_accrued_cost} new_fytd_accrued_cost:{new_fytd_accrued_cost}")
                if orgAccountObj.fytd_accrued_cost != new_fytd_accrued_cost:
                    orgAccountObj.fytd_accrued_cost = new_fytd_accrued_cost
                #LOG.info(f"orgAccountObj.most_recent_charge_time:{orgAccountObj.most_recent_charge_time} new_mrct:{new_mrct}")
                if orgAccountObj.most_recent_charge_time != new_mrct:
                    LOG.info(f"####### {orgAccountObj.name} updating orgAccountObj.most_recent_charge_time:{orgAccountObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')}-->{new_mrct.strftime('%Y-%m-%d %H:%M:%S')} from DailyHistCost")
                    orgAccountObj.most_recent_charge_time = new_mrct
                orgAccountObj.save(update_fields=['balance','most_recent_charge_time','fytd_accrued_cost'])
            else:
                LOG.info(f"{orgAccountObj.name} DID NOT change because there were no charges between  orgAccountObj.most_recent_charge_time:{orgAccountObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')} and beginning of today:{datetime.strftime(start_of_today,FMT)} it is debited up to {datetime.strftime(orgAccountObj.most_recent_charge_time,FMT)} NO CHANGE balance:{orgAccountObj.balance:.2f} (Cost Explorer report came back empty?)")
        else:
            LOG.info(f"{orgAccountObj.name} is ALREADY debited daily mrct:{orgAccountObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')} up to start_of_today:{start_of_today.strftime('%Y-%m-%d %H:%M:%S')} NO CHANGE balance:%f (No new daily amounts to debit)")
        #
        #  Now do hourly 
        #
        start_of_this_hour = time_now.replace(minute=0, second=0, microsecond=0)
        #LOG.info(f"orgAccountObj.most_recent_charge_time:{orgAccountObj.most_recent_charge_time} start_of_this_hour:{start_of_this_hour}")
        if orgAccountObj.most_recent_charge_time < start_of_this_hour:
            LOG.info(f"calling TodaysCost with {orgAccountObj.name} time_now_str:{time_now_str}")
            rsp = ac.TodaysCost(ps_server_pb2.TodaysCostReq(name=orgAccountObj.name,tm=time_now_str))
            if rsp.server_error:
                LOG.error(f"{orgAccountObj.name} TodaysCost got this error:{rsp.error_msg}")
                raise Exception(f"ps server error caught:{rsp.error_msg}")            
            LOG.info(f"len(rsp.tm):{len(rsp.tm)}")
            if len(rsp.tm) > 0:
                new_balance,new_fytd_accrued_cost,new_mrct = calculate_account_bal_and_fytd_bal(orgAccountObj,rsp)
                #LOG.info(f"orgAccountObj.balance:{orgAccountObj.balance} new_balance:{new_balance}")
                if orgAccountObj.balance != new_balance:
                    orgAccountObj.balance = new_balance
                #LOG.info(f"orgAccountObj.fytd_accrued_cost:{orgAccountObj.fytd_accrued_cost} new_fytd_accrued_cost:{new_fytd_accrued_cost}")
                if orgAccountObj.fytd_accrued_cost != new_fytd_accrued_cost:
                    orgAccountObj.fytd_accrued_cost = new_fytd_accrued_cost
                #LOG.info(f"orgAccountObj.most_recent_charge_time:{orgAccountObj.most_recent_charge_time} new_mrct:{new_mrct}")
                if orgAccountObj.most_recent_charge_time != new_mrct:
                    orgAccountObj.most_recent_charge_time = new_mrct
                    LOG.info(f"####### {orgAccountObj.name} updating mrct:{orgAccountObj.most_recent_charge_time} from TodaysCost")
                orgAccountObj.save(update_fields=['balance','most_recent_charge_time','fytd_accrued_cost'])
            else:
                LOG.info(f"{orgAccountObj.name} is debited hourly up to {datetime.strftime(orgAccountObj.most_recent_charge_time, FMT)} NO CHANGE balance:{orgAccountObj.balance:.2f}")
        else:
            LOG.info(f"{orgAccountObj.name} is ALREADY debited hourly mrct:{orgAccountObj.most_recent_credit_time} up to {datetime.strftime(start_of_this_hour,FMT)} NO CHANGE balance:{orgAccountObj.balance:.2f} (No new hourly amounts to debit)")
        #
        # Truncate to max allowed balance
        #
        if orgAccountObj.balance > orgAccountObj.max_allowance:
            orgAccountObj.balance = orgAccountObj.max_allowance
            orgAccountObj.save(update_fields=['balance'])
            LOG.info(f"{orgAccountObj.name} truncating balance to max_allowance:{orgAccountObj.max_allowance:.2f}")
    LOG.info("---done---")

def is_org_broke(orgAccountObj):
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    broke_status = False 
    if(clusterObj.is_deployed):
        if(clusterObj.org.balance < 0.5):
            LOG.info(f"Should destroy {clusterObj.org.name} with remaining balance of:{clusterObj.org.balance:.2f}")
            broke_status = True
        else:
            LOG.info(f"{clusterObj.org.name} has a remaining balance of:{clusterObj.org.balance:.2f}")
    else:
        LOG.info(f"{clusterObj.org.name} deployed_state is {clusterObj.deployed_state}")
    return broke_status

def create_forecast(orgAccountObj, hourlyRate, daily_days_to_forecast=None, hourly_days_to_forecast=None):
    ''' 
        This routine calculates hourly,daily,and monthly forecasts for a given hourly rate.
        The tm represents the start time of the given period
    '''
    daily_days_to_forecast = daily_days_to_forecast or 91
    hourly_days_to_forecast = hourly_days_to_forecast or 14
    LOG.info(f"create_forecast for {orgAccountObj.name} hourlyRate:{hourlyRate} daily_days_to_forecast:{daily_days_to_forecast} hourly_days_to_forecast:{hourly_days_to_forecast}")
    #LOG.info("%s %2g", orgAccountObj.name, hrlyRate)
    global FMT_HOURLY, FMT_DAILY
    hrlyRate = float(hourlyRate)
    days_of_week,num_days_in_month = calendar.monthrange(orgAccountObj.most_recent_recon_time.year, orgAccountObj.most_recent_recon_time.month)
    ############# HOURLY #############
    tms = []
    bals = []
    tm_bal_tuple = []
    fraction_of_hr = (59.0-orgAccountObj.most_recent_recon_time.minute)/60.0 # mins are 0-59
    partial_hr_mins_charge = hourlyRate*(fraction_of_hr)
    hr_to_start = (orgAccountObj.most_recent_recon_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) 
    tm = hr_to_start
    bal = float(orgAccountObj.balance)-partial_hr_mins_charge
    # tbd fraction of first hour?
    while tm < (hr_to_start + timedelta(days=hourly_days_to_forecast)):
        #LOG.info(f"tm:{tm}")
        if tm.day == 1 and tm.hour == 0:
            bal = bal + float(orgAccountObj.monthly_allowance)
        bals.append(bal)
        formatted_tm = datetime.strftime(tm, FMT_HOURLY)
        tms.append(formatted_tm)
        tm_bal_tuple.append((formatted_tm,bal))
        bal = bal - hrlyRate 
        if bal <= 0.00:
            bal = 0.00
        tm = tm + timedelta(hours=1)
    fc_hourly = json.dumps({'tm': tms, 'bal': bals})
    fc_hourly_tm_bal = json.dumps(tm_bal_tuple)
    ############# DAILY #############
    tms = []
    bals = []
    tm_bal_tuple = []
    partial_day_hrly_charge = (23-orgAccountObj.most_recent_recon_time.hour)*hrlyRate # hrs are 0-23
    # day_to_start is begining of first whole day
    day_to_start = orgAccountObj.most_recent_recon_time.replace(hour=0,minute=0,second=0,microsecond=0) + timedelta(days=1)    
    tm = day_to_start
    bal = float(orgAccountObj.balance) - partial_day_hrly_charge
    while tm < (day_to_start + timedelta(days=daily_days_to_forecast)):
        if tm.day == 1:
            bal = bal + float(orgAccountObj.monthly_allowance)
        bals.append(bal)
        formatted_tm = datetime.strftime(tm, FMT_DAILY)
        tms.append(formatted_tm)
        tm_bal_tuple.append((formatted_tm,bal))
        bal = bal - (hrlyRate*24)
        if bal < 0.00:
            bal = 0.00
        tm = tm + timedelta(days=1)
    fc_daily = json.dumps({'tm': tms, 'bal': bals})
    fc_daily_tm_bal = json.dumps(tm_bal_tuple)
    ############# MONTHLY #############
    partial_day_charge = partial_day_hrly_charge + partial_hr_mins_charge

    tms = []
    bals = []
    tm_bal_tuple = []
    weekday,num_days_in_month = calendar.monthrange(day_to_start.year, day_to_start.month)
    bal = float(orgAccountObj.balance) - partial_day_charge
    day = day_to_start.day  # 
    tm = day_to_start  # beginning of first full day
    if bal < 0:
        bal = 0.0
    #LOG.info(f"num_days_in_month:{num_days_in_month} day:{day}")
    bals.append(bal)
    formatted_tm = datetime.strftime(hr_to_start, FMT_MONTHLY)# MONTH fmt so need to be IN first partial month
    tms.append(formatted_tm)
    #LOG.info(f"appended:{formatted_tm} {bal}")
    tm_bal_tuple.append((formatted_tm,bal))
    while day <= num_days_in_month:  # 1 based - partial month
        if day == 1:
            bal = bal + float(orgAccountObj.monthly_allowance)
        bal = bal - (hrlyRate*24)
        if bal < 0.00:
            bal = 0.00
        day = day + 1
        tm = tm + timedelta(days=1)
    start_tm = tm  #  first full months
    #LOG.info(f"first day of whole months:{start_tm.strftime(FMT)}")
    while tm < (start_tm + timedelta(days=365)):
        # current month
        weekday,num_days_in_month = calendar.monthrange(tm.year,tm.month) # one month at a time
        bal = bal + float(orgAccountObj.monthly_allowance)
        bal = bal - (num_days_in_month*hrlyRate*24)
        if bal < 0.00:
            bal = 0.00
        bals.append(bal)
        formatted_tm = datetime.strftime(tm, FMT_MONTHLY)
        tms.append(formatted_tm)
        #LOG.info(f"appended:{formatted_tm} {bal}")
        tm_bal_tuple.append((formatted_tm,bal))
        tm = tm + timedelta(days=num_days_in_month)

    fc_monthly = json.dumps({'tm': tms, 'bal': bals})
    fc_monthly_tm_bal = json.dumps(tm_bal_tuple)
    return fc_hourly, fc_daily, fc_monthly, fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal

def create_all_forecasts(orgAccountObj):
    update_cur_num_nodes(orgAccountObj)
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    LOG.info(f"Hourly burn rates: {orgAccountObj.min_hrly}/{orgAccountObj.cur_hrly}/{orgAccountObj.max_hrly}")

    orgAccountObj.fc_min_hourly, orgAccountObj.fc_min_daily, orgAccountObj.fc_min_monthly,fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal  = create_forecast(orgAccountObj, orgAccountObj.min_hrly)
    #LOG.info(f"MIN fc_hourly_tm_bal:{fc_hourly_tm_bal} fc_daily_tm_bal:{fc_daily_tm_bal} fc_monthly_tm_bal:{fc_monthly_tm_bal} ")
    #LOG.info(f"MIN fc_min_hourly:{orgAccountObj.fc_min_hourly},fc_min_daily:{orgAccountObj.fc_min_daily},fc_min_monthly:{orgAccountObj.fc_min_monthly}")
    orgAccountObj.fc_cur_hourly, orgAccountObj.fc_cur_daily, orgAccountObj.fc_cur_monthly,fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal = create_forecast(orgAccountObj, orgAccountObj.cur_hrly)
    #LOG.info(f"CUR fc_hourly_tm_bal:{fc_hourly_tm_bal} fc_daily_tm_bal:{fc_daily_tm_bal} fc_monthly_tm_bal:{fc_monthly_tm_bal} ")
    #LOG.info(f"CUR fc_cur_hourly:{orgAccountObj.fc_cur_hourly},fc_cur_daily:{orgAccountObj.fc_cur_daily},fc_cur_monthly:{orgAccountObj.fc_cur_monthly}")
    orgAccountObj.fc_max_hourly, orgAccountObj.fc_max_daily, orgAccountObj.fc_max_monthly,fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal = create_forecast(orgAccountObj, orgAccountObj.max_hrly)
    #LOG.info(f"MAX fc_hourly_tm_bal:{fc_hourly_tm_bal} fc_daily_tm_bal:{fc_daily_tm_bal} fc_monthly_tm_bal:{fc_monthly_tm_bal} ")
    #LOG.info(f"MAX fc_max_hourly:{orgAccountObj.fc_max_hourly},fc_max_daily:{orgAccountObj.fc_max_daily},fc_max_monthly:{orgAccountObj.fc_max_monthly}")

    LOG.info(f"min_ddt:{orgAccountObj.min_ddt.strftime(FMT)},cur_ddt:{orgAccountObj.cur_ddt.strftime(FMT)},max_ddt:{orgAccountObj.max_ddt.strftime(FMT)}")
    orgAccountObj.save(update_fields=['min_ddt','cur_ddt','max_ddt','fc_min_hourly','fc_min_daily','fc_min_monthly','fc_cur_hourly','fc_cur_daily','fc_cur_monthly','fc_max_hourly','fc_max_daily','fc_max_monthly'])

def create_all_forecasts_for_all_orgs():
    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
    for o in orgs_qs:
        create_all_forecasts(o)

def ad_hoc_cost_reconcile_for_org(orgObj):
    update_ccr(orgObj)
    update_burn_rates(orgObj)
    update_ddt(orgObj)
    reconcile_org(orgObj)
    create_all_forecasts(orgObj)


def hourly_processing():
    LOG.info(f"hourly_processing started")
    try:
        perform_cost_accounting_for_all_orgs() # updates forecasts
        reconcile_all_orgs() # computes balance and FYTD cost
        # Now find all Orgs that ran out of funds (i.e. the are Broke)
        for orgAccountObj in find_broke_orgs():
            owner_ps_cmd = OwnerPSCmd.objects.create(user=orgAccountObj.owner, org=orgAccountObj, ps_cmd='Destroy', create_time=datetime.now(timezone.utc))
            owner_ps_cmd.save()
            LOG.info(f"Destroy {orgAccountObj.name} queued for processing because it ran out of funds")
        LOG.info(f"hourly_processing finished")
        return True
    except Exception as e:
        LOG.exception('Exception caught')
        LOG.error(f"hourly_processing finished with an exception")
        return False

def refresh_token_maintenance():
    LOG.info(f"flush_expired_refresh_tokens started")
    try:
        flush_expired_refresh_tokens()
        LOG.info(f"flush_expired_refresh_tokens finished")
        return True
    except Exception as e:
        LOG.exception('Exception caught')
        return False

def cost_accounting(orgObj):
    try:
        if update_ccr(orgObj):
            update_burn_rates(orgObj) # auto scaling changes num_nodes
            update_ddt(orgObj)
            create_all_forecasts(orgObj)
    except Exception as e:
        LOG.exception("Error in cost_accounting: %s", repr(e))

def find_broke_orgs():
    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
    broke_orgs = []
    for o in orgs_qs:
        if is_org_broke(o):
            broke_orgs.append(o)
    return broke_orgs

def get_cli_html(orgAccountObj, cli):
    conv = Ansi2HTMLConverter(inline=True)
    console_html = ''
    if(cli.valid):
        if(cli.cmd_args != ''):
            console_html += conv.convert(
                "".join(cli.cmd_args), full=False)
        if(cli.stdout != ''):
            console_html += conv.convert(
                "".join(cli.stdout), full=False)
        if(cli.stderr != ''):
            console_html += conv.convert(
                "".join(cli.stderr), full=False)
    return console_html

def getConsoleHtml(orgAccountObj, rrsp):
    console_html = ''
    try:
        console_html = get_cli_html(orgAccountObj, rrsp.cli)
        if(rrsp.ps_server_error):
            LOG.error("Error in server:\n %s", rrsp.error_msg)
            rsp_status = 500
        else:
            rsp_status = 200
        return rsp_status, console_html
    except Exception as e:
        LOG.exception("caught exception:")
        failed_cli = ps_server_pb2.cli_rsp(valid=False)
        rrsp = ps_server_pb2.Response(
            done=True, name=orgAccountObj.name, cli=failed_cli)
        return 500, ps_server_pb2.PS_AjaxResponseData(rsp=rrsp, console_html=console_html, web_error=True, web_error_msg='caught exception in web server')

def remove_num_node_requests(user,orgAccountObj,only_owned_by_user=None):
    try:
        only_owned_by_user = only_owned_by_user or False
        LOG.info(f"{user.username} cleaning up OrgNumNode for {orgAccountObj.name} {f'owned by:{user.username}' if only_owned_by_user else ''} only_owned_by_user:{only_owned_by_user} onn_cnt:{OrgNumNode.objects.count()}")
        if only_owned_by_user:
            onns = OrgNumNode.objects.filter(org=orgAccountObj,user=user)
        else:
            onns = OrgNumNode.objects.filter(org=orgAccountObj)
        clusterObj = Cluster.objects.get(org=orgAccountObj)
        for onn in onns:
            LOG.info(f"deleting OrgNumNode with org:{onn.org.name} with user:{onn.user.username}")
            if clusterObj.cnnro_ids is not None:
                if str(onn.id) in clusterObj.cnnro_ids:
                    LOG.info(f"Skipping active OrgNumNode.id:{onn.id} with org:{onn.org.name} user:{onn.user.username}")
                else:
                    onn.delete()
            else:
                onn.delete()
        jrsp = {'status': "SUCCESS","msg":f"{user.username} cleaned all PENDING org node reqs for {orgAccountObj.name} "}
        LOG.info(f"{user.username} cleaned up OrgNumNode for {orgAccountObj.name} {'owned by:{user.username}' if only_owned_by_user else ''} onn_cnt:{OrgNumNode.objects.count()}")
        enqueue_process_state_change(orgAccountObj.name)
        return jrsp
    except Exception as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED","error_msg":f"Server Error; Request by {user.username} to clean ALL org node reqs for {orgAccountObj.name} FAILED"}

def init_new_org_memberships(orgAccountObj):
    # automatically make owner an active member
    m = Membership()
    m.org = orgAccountObj
    m.user = orgAccountObj.owner
    m.active = True
    m.save()
    msg = f"{m.user.first_name} {m.user.last_name} ({m.user.username}) now owns new org/cluster:{m.org.name}"
    users = get_user_model().objects.all()
    for user in users:
        if(user.is_staff and not user.is_superuser and not (user == orgAccountObj.owner)):
            m = Membership()
            m.org = orgAccountObj
            m.user = user
            m.active = True
            m.save()
            LOG.info("Staff member %s %s (%s) is now a member of %s",
                    m.user.first_name, m.user.last_name, m.user.username, m.org.name)
    return msg

def ps_cmd_cleanup(orgAccountObj,st,org_cmd_str):
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    clusterObj.active_ps_cmd = ''  # ALWAYS clear this!
    clusterObj.save(update_fields=['active_ps_cmd'])
    update_cur_num_nodes(orgAccountObj)
    time_to_process = datetime.now(timezone.utc) - st
    time_to_process = time_to_process - timedelta(microseconds=time_to_process.microseconds)
    LOG.info(f"DONE {org_cmd_str} has completed in {str(time_to_process)}")

def process_rsp_generator(orgAccountObj, ps_cmd, rsp_gen, psCmdResultObj, org_cmd_str, deploy_values=None, expire_time=None):
    '''
    This function processes the response generator from the ps-server
        for Update, Refresh and Destroy commands. They all send the same response stream
    '''
    LOG.info(f"process_rsp_generator {org_cmd_str} {deploy_values if deploy_values is not None else ''} {expire_time.strftime(FMT) if expire_time is not None else ''}")
    clusterObj = Cluster.objects.get(org=orgAccountObj)
    clusterObj.active_ps_cmd = ps_cmd
    clusterObj.save(update_fields=['active_ps_cmd'])
    stopped = False
    iterations = 0
    got_ps_server_error = False
    try:
        while(not stopped): 
            # make the call to get cached streamed response messages from server
            #LOG.info(f"getting next response from ps-server...")
            rrsp = None
            # Read until rrsp.done is True or until StopIteration exception is caught
            try:
                rrsp = next(rsp_gen)  # grab the next one and process it
                iterations += 1
                if rrsp is None:
                    LOG.error(f"{org_cmd_str} iter:<{iterations}> got None response from ps_server!")
                    stopped = True # if we get here then we got a None response from the ps_server
                else:
                    rsp_status, console_html = getConsoleHtml(orgAccountObj, rrsp)
                    psCmdResultObj.ps_cmd_output += console_html
                    psCmdResultObj.save()
                    LOG.info(f"{org_cmd_str} iter:<{iterations}> state:{rrsp.state} rrsp.state.valid:{rrsp.state.valid} rrsp.done:{rrsp.done} rrsp.ps_server_error:{rrsp.ps_server_error}")
                    if rrsp.state.valid:
                        LOG.info(f"{org_cmd_str} iter:<{iterations}> got valid state in rsp with state:{rrsp.state} using deploy_values:{deploy_values}")
                        if deploy_values:
                            update_cur_num_nodes(orgAccountObj)
                            clusterObj = Cluster.objects.get(org=orgAccountObj)
                            clusterObj.cur_min_node_cap = deploy_values['min_node_cap']
                            clusterObj.cur_max_node_cap = deploy_values['max_node_cap']
                            clusterObj.cur_version = deploy_values['version']
                            clusterObj.is_public = deploy_values['is_public']
                            clusterObj.expire_time = expire_time
                            clusterObj.save(update_fields=['cur_min_node_cap','cur_max_node_cap','cur_version','is_public','expire_time'])
                            orgAccountObj.desired_num_nodes = int(deploy_values['desired_num_nodes'])
                            orgAccountObj.save(update_fields=['desired_num_nodes'])
                        if ps_cmd == 'Destroy': # must set to zero so future desired node requests will always be differnt than current to trigger deploy
                            orgAccountObj.desired_num_nodes = 0
                            orgAccountObj.save(update_fields=['desired_num_nodes'])
                        clusterObj.deployed_state = rrsp.state.deployed_state
                        clusterObj.is_deployed = rrsp.state.deployed
                        clusterObj.mgr_ip_address = rrsp.state.ip_address.replace('"', '')
                        if clusterObj.mgr_ip_address == '':
                            clusterObj.mgr_ip_address = '0.0.0.0'
                        if not clusterObj.is_deployed:
                            clusterObj.cur_version = ''
                        clusterObj.save(update_fields=['deployed_state','is_deployed','cur_version','mgr_ip_address'])
                        msg = f" Saving state of {orgAccountObj.name} cluster -> is_deployed:{clusterObj.is_deployed} deployed_state:{clusterObj.deployed_state} cur_version:{clusterObj.cur_version} mgr_ip_address:{clusterObj.mgr_ip_address}"
                        LOG.info(msg)
                    if rrsp.ps_server_error:
                        got_ps_server_error = True
                        error_msg =  f"ps-server returned this error: {org_cmd_str} iter:<{iterations}> FAILED with error:{rrsp.error_msg}"
                        LOG.error(error_msg)
                        psCmdResultObj.error = error_msg
                        psCmdResultObj.save()
                        raise ProvisionCmdError(f"{error_msg}")
                    if rrsp.done:
                        LOG.info(f"{org_cmd_str} iter:<{iterations}>  got rrsp done from ps_server!")
            except StopIteration:
                stopped = True
                if not got_ps_server_error:
                    LOG.info(f"{org_cmd_str} iter:<{iterations}> incrementing num_ps_cmd_successful")
                    orgAccountObj.num_ps_cmd_successful += 1
                    orgAccountObj.save(update_fields=['num_ps_cmd_successful'])
                psCmdResultObj.error = ''
                psCmdResultObj.save(update_fields=['error'])
                LOG.info(f"{org_cmd_str} iter:<{iterations}> got expected StopIteration exception")
    except ProvisionCmdError as e:
        error_msg = f"{org_cmd_str} iter:<{iterations}> caught ProvisionCmdError exception: "
        LOG.exception(error_msg) 
        psCmdResultObj.error = error_msg + repr(e)
        psCmdResultObj.save()
        raise ProvisionCmdError(f"{error_msg}:{str(e)}")
    except (grpc.RpcError) as e:
        error_msg = f"{org_cmd_str} iter:<{iterations}> caught gRpc exception: "
        LOG.exception(error_msg) 
        psCmdResultObj.error = error_msg + repr(e)
        psCmdResultObj.save()
        raise ProvisionCmdError(f"{error_msg}:{str(e)}")
    except subprocess.CalledProcessError as e:
        error_msg = f"{org_cmd_str} iter:<{iterations}> caught CalledProcessError exception: "
        LOG.exception(error_msg) 
        psCmdResultObj.error = error_msg + repr(e)
        psCmdResultObj.save()
        raise ProvisionCmdError(f"{error_msg}:{str(e)}")
    except Exception as e:
        error_msg = f"{org_cmd_str} iter:<{iterations}> caught UNKNOWN exception: "
        LOG.exception(error_msg) 
        psCmdResultObj.error = error_msg + repr(e)
        psCmdResultObj.save()
        raise ProvisionCmdError(f"{error_msg}:{str(e)}")
    LOG.info(f"{org_cmd_str} iter:<{iterations}> Done")

def get_psCmdResultObj(orgAccountObj, ps_cmd, version=None, username=None, is_adhoc=False):
    psCmdResultObj = PsCmdResult.objects.create(org=orgAccountObj)
    psCmdResultObj.error = 'still processing'
    psCmdResultObj.ps_cmd_output = ''
    if is_adhoc:
        psCmdResultObj.ps_cmd_summary_label = f" --- {ps_cmd} {orgAccountObj.name} Ad-Hoc "
    else:
        if ps_cmd == 'SetUp':
            psCmdResultObj.ps_cmd_summary_label = f" --- Configure {orgAccountObj.name} "
        else:
            psCmdResultObj.ps_cmd_summary_label = f" --- {ps_cmd} {orgAccountObj.name} "
    if version is not None:
        psCmdResultObj.ps_cmd_summary_label += f" with version {orgAccountObj.version}"
    psCmdResultObj.save()
    if ps_cmd == 'SetUp':
        orgAccountObj.num_setup_cmd += 1
        orgAccountObj.save(update_fields=['num_setup_cmd'])
    orgAccountObj.num_ps_cmd += 1
    orgAccountObj.save(update_fields=['num_ps_cmd'])
    if username is not None:
        try:
            get_user_model().objects.get(username=username)
        except (get_user_model().DoesNotExist):
            raise UnknownUserError(f" username:{username} does not exist")

    # add username to label being displayed 
    if username is not None:
        psCmdResultObj.ps_cmd_summary_label += f" {username}"
        psCmdResultObj.save()

    org_cmd_str = f"{orgAccountObj.name} cmd-{orgAccountObj.num_ps_cmd}: {ps_cmd} {username if username is not None else ''}"
    return psCmdResultObj,org_cmd_str

def process_SetUp_cmd(orgAccountObj):
    '''
        This function processes the SetUp command (that shows up as Configure in the results)
    '''
    LOG.info(f"Configure {orgAccountObj.name}")
    st = datetime.now(timezone.utc)
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(orgAccountObj, 'SetUp', version=orgAccountObj.version, username=orgAccountObj.owner, is_adhoc=False)
    LOG.info(f"STARTED {org_cmd_str} is_public:{orgAccountObj.is_public}")
    try:
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
            rsp_gen = stub.SetUp(
                ps_server_pb2.SetUpReq(
                    name=orgAccountObj.name,
                    version=orgAccountObj.version,
                    is_public=orgAccountObj.is_public,
                    now=datetime.now(timezone.utc).strftime(FMT)),
                    timeout=timeout)
            done = False
            setup_occurred = True
            clusterObj = Cluster.objects.get(org=orgAccountObj)
            clusterObj.active_ps_cmd = 'SetUp'
            clusterObj.save(update_fields=['active_ps_cmd'])
            while(not done): 
                # make the call to get cached streamed response messages from server
                #LOG.info(f"getting next response from ps-server...")
                rrsp = None
                try:
                    # Read until rrsp.done is True or until StopIteration exception is caught
                    rrsp = next(rsp_gen)  # grab the next one and process it
                    psCmdResultObj.ps_cmd_output += get_cli_html(orgAccountObj, rrsp.cli)
                    psCmdResultObj.save()
                    if rrsp.ps_server_error:
                        error_msg =  f"ps-server returned error for {org_cmd_str} FAILED with error:{rrsp.error_msg}"
                        LOG.error(error_msg)
                        psCmdResultObj.error = error_msg
                        psCmdResultObj.save()
                except StopIteration:
                    done = True
                    error_msg = f"{org_cmd_str} read off the end...caught StopIteration exception but should be able to read until done flag is sent: "
                    LOG.exception(error_msg) 
                    psCmdResultObj.error = error_msg 
                    psCmdResultObj.save()
                except (grpc.RpcError) as e:
                    done = True
                    error_msg = f"{org_cmd_str} caught gRpc exception: "
                    LOG.exception(error_msg) 
                    psCmdResultObj.error = error_msg + repr(e)
                    psCmdResultObj.save()
                    raise e
                except subprocess.CalledProcessError as e:
                    done = True
                    error_msg = f"{org_cmd_str} caught CalledProcessError exception: "
                    LOG.exception(error_msg) 
                    psCmdResultObj.error = error_msg + repr(e)
                    psCmdResultObj.save()
                    raise e
                except Exception as e:
                    done = True
                    error_msg = f"{org_cmd_str} caught UNKNOWN exception: "
                    LOG.exception(error_msg) 
                    psCmdResultObj.error = error_msg + repr(e)
                    psCmdResultObj.save()
                    raise e
                finally:
                    if rrsp is None:
                        done = True
                        error_msg = f"{org_cmd_str} rrsp is None?"
                        LOG.error(error_msg)
                        psCmdResultObj.error = error_msg
                        psCmdResultObj.save()
                        raise Exception(error_msg)
                    if rrsp.done:
                        done = True
                        psCmdResultObj.error = ''
                        psCmdResultObj.save(update_fields=['error'])
                        if not rrsp.ps_server_error:
                            clusterObj = Cluster.objects.get(org=orgAccountObj)
                            clusterObj.provision_env_ready = True
                            ps_server_pb2.GetCurrentSetUpCfgRsp()
                            rsp = stub.GetCurrentSetUpCfg(ps_server_pb2.GetCurrentSetUpCfgReq(name=orgAccountObj.name))
                            clusterObj.prov_env_version = rsp.setup_cfg.version
                            clusterObj.prov_env_is_public = rsp.setup_cfg.is_public
                            if rsp.setup_cfg.version != '':
                                need_destroy_for_changed_version_or_is_public(orgAccountObj=orgAccountObj)
                            else:
                                clusterObj.provision_env_ready = False
                                orgAccountObj.provisioning_suspended = True
                                orgAccountObj.save(update_fields=['provisioning_suspended'])
                                LOG.warning(f"{orgAccountObj.name} cluster current_version is null Suspending provisioning")
                            LOG.info(f"{orgAccountObj.name} cluster current_version:{clusterObj.cur_version} provision_env_ready:{clusterObj.provision_env_ready}")
                            clusterObj.save()
                            orgAccountObj.num_ps_cmd_successful += 1
                            orgAccountObj.num_setup_cmd_successful += 1
                            orgAccountObj.save(update_fields=['num_ps_cmd_successful','num_setup_cmd_successful'])
                        LOG.info(f"{org_cmd_str} got rrsp done from ps_server!")

    except Exception as e:
        error_msg = f"ERROR: {org_cmd_str}  {orgAccountObj.version}:"
        LOG.exception(f"{error_msg} caught exception:") 
        psCmdResultObj.error = 'Server Error'
        psCmdResultObj.ps_cmd_summary_label = f" --- {org_cmd_str} {orgAccountObj.version} ---"
        psCmdResultObj.save()

    finally:
        clusterObj = Cluster.objects.get(org=orgAccountObj)
        clusterObj.active_ps_cmd = ''
        clusterObj.save(update_fields=['active_ps_cmd'])
        time_to_process = datetime.now(timezone.utc) - st
        time_to_process = time_to_process - timedelta(microseconds=time_to_process.microseconds)
        LOG.info(f"DONE {org_cmd_str} org version:{orgAccountObj.version} cluster prov_env_version:{clusterObj.prov_env_version} cluster cur_version:{clusterObj.cur_version} has completed in {str(time_to_process)}")
        if orgAccountObj.provisioning_suspended != clusterObj.provision_env_ready:
            orgAccountObj.provisioning_suspended = not clusterObj.provision_env_ready
            orgAccountObj.save(update_fields=['provisioning_suspended'])
    return clusterObj.provision_env_ready,setup_occurred       

def process_Update_cmd(orgAccountObj, username, deploy_values, expire_time):
    global MIN_HRS_TO_LIVE_TO_START
    st = datetime.now(timezone.utc)
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(orgAccountObj=orgAccountObj,ps_cmd='Update')
    LOG.info(f"STARTED {org_cmd_str} org.dnn:{orgAccountObj.desired_num_nodes} {deploy_values if deploy_values is not None else 'no deploy values'} {expire_time.strftime(FMT) if expire_time is not None else 'no expire tm'} ")
    try:
        try:
            LOG.info(f"Update {orgAccountObj.name}")
            psCmdResultObj.expiration = expire_time
            if psCmdResultObj.expiration is None or psCmdResultObj.expiration > datetime.now(timezone.utc):
                update_ddt(orgAccountObj) ## update DDT to check for broke orgs
                LOG.info(f"Update {orgAccountObj.name} test times min_ddt:{orgAccountObj.min_ddt} max_ddt:{orgAccountObj.max_ddt} now:{datetime.now(timezone.utc)} MIN_HRS_TO_LIVE_TO_START:{timedelta(hours=MIN_HRS_TO_LIVE_TO_START)}")
                if (orgAccountObj.max_ddt - datetime.now(timezone.utc)) < timedelta(hours=MIN_HRS_TO_LIVE_TO_START):
                    emsg = f"cluster:{orgAccountObj.name} Raise LowBalanceError ddt:{orgAccountObj.max_ddt.strftime(FMT)}"
                    LOG.warning(emsg)
                    raise LowBalanceError(message=emsg)
            # add username to label being displayed 
            psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            # need to update
            with ps_client.create_client_channel("control") as channel:
                stub = ps_server_pb2_grpc.ControlStub(channel)
                timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
                LOG.info(f"gRpc: {org_cmd_str} deploy_args:{deploy_values} timeout:{timeout}")
                psCmdResultObj.ps_cmd_summary_label += f" {deploy_values['min_node_cap']}-{deploy_values['desired_num_nodes']}-{deploy_values['max_node_cap']} {deploy_values['version']}"
                if int(deploy_values['desired_num_nodes']) == 0:
                    LOG.info("Setting num_nodes_to_use to zero (i.e. deploy load balancer and monitor only)")
                clusterObj = Cluster.objects.get(org=orgAccountObj)
                LOG.info(f"Update {orgAccountObj.name} clusterObj.is_deployed:{clusterObj.is_deployed} clusterObj.cur_version:{clusterObj.cur_version} deploy_values['version']:{deploy_values['version']}")
                rsp_gen = stub.Update(
                    ps_server_pb2.UpdateRequest(
                        name        = orgAccountObj.name,
                        min_nodes   = int(deploy_values['min_node_cap']),
                        max_nodes   = int(deploy_values['max_node_cap']),
                        num_nodes   = int(deploy_values['desired_num_nodes']),
                        now         = datetime.now(timezone.utc).strftime(FMT),
                        reload      = bool(not clusterObj.is_deployed)),
                        timeout     = timeout)
                process_rsp_generator(  orgAccountObj=orgAccountObj,
                                        ps_cmd='Update', 
                                        rsp_gen=rsp_gen, 
                                        psCmdResultObj=psCmdResultObj, 
                                        org_cmd_str=org_cmd_str, 
                                        deploy_values=deploy_values, 
                                        expire_time=expire_time)
        except LowBalanceError as e:
            error_msg = f"{org_cmd_str} Low Balance Error: The account balance ({str(orgAccountObj.balance)}) of this organization is too low.The auto-shutdown time is {str(orgAccountObj.min_ddt)}  Check with the support team for assistance. Can NOT deploy with less than 8 hrs left until automatic shutdown"
            LOG.warning(error_msg)
            psCmdResultObj.error = error_msg
            psCmdResultObj.save()
            raise ProvisionCmdError(f"{error_msg}:{str(e)}",log_level=logging.INFO)                
    except Exception as e:
        error_msg = f"ERROR: {org_cmd_str} {deploy_values} {expire_time.strftime(FMT) if expire_time is not None else 'None'}"
        LOG.exception(f"{error_msg} caught exception:") 
        if isinstance(e,ProvisionCmdError):
            raise e
        else:
            psCmdResultObj.error = 'Server Error'
            psCmdResultObj.ps_cmd_summary_label = f" --- Update {orgAccountObj.name}"
            psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            raise ProvisionCmdError(f"An error occurred during command processing. {error_msg}")
    finally:
        long_str=f"{org_cmd_str} org.dnn:{orgAccountObj.desired_num_nodes} {deploy_values if deploy_values is not None else 'no deploy values'} {expire_time.strftime(FMT) if expire_time is not None else 'no expire tm'} "
        ps_cmd_cleanup(orgAccountObj,st,long_str)
        for handler in LOG.handlers:
            handler.flush()

def process_Refresh_cmd(orgAccountObj, username=None, owner_ps_cmd=None):
    global MIN_HRS_TO_LIVE_TO_START
    st = datetime.now(timezone.utc)
    is_ad_hoc = owner_ps_cmd is not None
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(orgAccountObj=orgAccountObj,ps_cmd='Refresh', username=username, is_adhoc=is_ad_hoc)
    LOG.info(f"STARTED {org_cmd_str}")
    try:
        # need to update
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
            LOG.info(f"gRpc: Refresh {orgAccountObj.name} timeout:{timeout}")
            rsp_gen = stub.Refresh(
                ps_server_pb2.RefreshRequest(
                    name=orgAccountObj.name,
                    now=datetime.now(timezone.utc).strftime(FMT)),
                    timeout=timeout)
            process_rsp_generator(orgAccountObj=orgAccountObj, 
                                  ps_cmd='Refresh', 
                                  rsp_gen=rsp_gen, 
                                  psCmdResultObj=psCmdResultObj, 
                                  org_cmd_str=org_cmd_str)        
    except Exception as e:
        error_msg = f"ERROR: {org_cmd_str}"
        LOG.exception(f"{error_msg} caught exception:") 
        if isinstance(e,ProvisionCmdError):
            raise e
        else:
            psCmdResultObj.error = 'Server Error'
            psCmdResultObj.ps_cmd_summary_label = f" --- 'Refresh' {orgAccountObj.name}"
            if username is not None:
                psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            raise ProvisionCmdError(f"An error occurred during command processing. {error_msg}")
    finally:
        if owner_ps_cmd is not None:
            OwnerPSCmd.objects.get(id=owner_ps_cmd.id).delete()
        ps_cmd_cleanup(orgAccountObj,st,org_cmd_str)
        for handler in LOG.handlers:
            handler.flush()

def process_Destroy_cmd(orgAccountObj, username=None, owner_ps_cmd=None):
    global MIN_HRS_TO_LIVE_TO_START
    st = datetime.now(timezone.utc)
    is_ad_hoc = owner_ps_cmd is not None
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(orgAccountObj=orgAccountObj,ps_cmd='Destroy', username=username, is_adhoc=is_ad_hoc)
    LOG.info(f"STARTED {org_cmd_str}")
    try:
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
            LOG.info(f"gRpc: 'Destroy {orgAccountObj.name} timeout:{timeout}")
            rsp_gen = stub.Destroy(
                ps_server_pb2.DestroyRequest(
                    name=orgAccountObj.name,
                    now=datetime.now(timezone.utc).strftime(FMT)),
                    timeout=timeout)
            process_rsp_generator(  orgAccountObj=orgAccountObj,
                                    ps_cmd='Destroy',
                                    rsp_gen=rsp_gen, 
                                    psCmdResultObj=psCmdResultObj,
                                    org_cmd_str=org_cmd_str)
    except Exception as e:
        error_msg = f"ERROR: {org_cmd_str}"
        LOG.exception(f"{error_msg} caught exception:") 
        if isinstance(e,ProvisionCmdError):
            raise e
        else:
            psCmdResultObj.error = 'Server Error'
            psCmdResultObj.ps_cmd_summary_label = f" --- Destroy {orgAccountObj.name}"
            if username is not None:
                psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            raise ProvisionCmdError(f"An error occurred during command processing. {error_msg}")
    finally:
        psCmdResultObj.save()
        if owner_ps_cmd is not None:
            OwnerPSCmd.objects.get(id=owner_ps_cmd.id).delete()
        ps_cmd_cleanup(orgAccountObj,st,org_cmd_str)
        for handler in LOG.handlers:
            handler.flush()

def process_owner_ps_cmd(orgAccountObj,owner_ps_cmd):
    try:
        # This is a synchronous blocking call
        if owner_ps_cmd.ps_cmd == "Refresh":
            process_Refresh_cmd(orgAccountObj=orgAccountObj,
                                username=owner_ps_cmd.user.username,
                                owner_ps_cmd=owner_ps_cmd)
        elif owner_ps_cmd.ps_cmd == "Destroy":
            process_Destroy_cmd(orgAccountObj=orgAccountObj,
                                username=owner_ps_cmd.user.username,
                                owner_ps_cmd=owner_ps_cmd)
        else:
            LOG.error(f"ERROR: process_owner_ps_cmd: unexpected ps_cmd:{owner_ps_cmd.ps_cmd}")
        LOG.info(f"DONE processing :{owner_ps_cmd.ps_cmd} {owner_ps_cmd.org} for {owner_ps_cmd.user.username} with {owner_ps_cmd.deploy_values} num_owner_ps_cmd:{orgAccountObj.num_owner_ps_cmd} id:{orgAccountObj.id}")
    except Exception as e:
        LOG.exception(f"ERROR processing OwnerPSCmd id:{owner_ps_cmd.id} {owner_ps_cmd.ps_cmd} {orgAccountObj.name} {owner_ps_cmd.user.username} {owner_ps_cmd.deploy_values} Exception:")
        LOG.info(f"sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
        sleep(COOLOFF_SECS)
    try:
        # if it still exists (because of an unhandled exception?). delete it
        OwnerPSCmd.objects.get(id=owner_ps_cmd.id).delete()
        LOG.info(f"Deleted :{owner_ps_cmd.id}") 
    except OwnerPSCmd.DoesNotExist:
        pass # normally it's deleted inside process_provision_cmd
    except Exception as e:
        LOG.exception(f"ERROR deleting processed OwnerPSCmd id:{owner_ps_cmd.id} {owner_ps_cmd.ps_cmd} {orgAccountObj.name} {owner_ps_cmd.user.username} {owner_ps_cmd.deploy_values} Exception:")
        orgAccountObj.provisioning_suspended = True
        orgAccountObj.save(update_fields=['provisioning_suspended'])

def process_owner_ps_cmds_table(orgAccountObj):
    '''
    This function is called when a privileged user issues an ad-hoc Refresh or Delete command
    '''
    env_ready,setup_occurred = check_provision_env_ready(orgAccountObj)
    num_cmds_processed = 0
    if env_ready:
        qs = OwnerPSCmd.objects.filter(org=orgAccountObj)
        #LOG.info(f"Enter with qs.count():{qs.count()}")
        for owner_ps_cmd in qs:
            process_owner_ps_cmd(orgAccountObj,owner_ps_cmd)
            num_cmds_processed += 1
            orgAccountObj.num_owner_ps_cmd = orgAccountObj.num_owner_ps_cmd + 1
            orgAccountObj.save(update_fields=['num_owner_ps_cmd'])
    qs = OwnerPSCmd.objects.filter(org=orgAccountObj)
    #LOG.info(f"Exit with qs.count():{qs.count()}")
    return (qs.count()>0),setup_occurred,num_cmds_processed

def  process_prov_sys_tbls(orgAccountObj):
    '''
    This will empty the owner ps cmds table (OwnerPSCmd)
    then process the next org num node request 
    from the OrgNumNodes table if there is one ready
    Refresh and Destroy are the commands that are processed
    from this table
    '''
    try:
        start_cmd_cnt = orgAccountObj.num_ps_cmd
        #LOG.info(f"org:{orgAccountObj.name} num_ps_cmd:{num_ps_cmd} ")
        start_time = time.time() 
        # during deployments multiple versions of ps-web are running
        with advisory_lock(orgAccountObj.name) as acquired:
            end_time = time.time()  # record the ending time
            wait_time = end_time - start_time  # calculate the waiting time
            if wait_time > 3:  # only valid 
                LOG.warning(f'Waited {wait_time} seconds to acquire lock for {orgAccountObj.name}')
            has_more_ps_cmds = True
            setup_occurred = False
            num_cmds_processed = 0
            while has_more_ps_cmds:
                has_more_ps_cmds,setup_occurred_this_time,num_cmds_processed_this_time = process_owner_ps_cmds_table(orgAccountObj)
                setup_occurred = setup_occurred or setup_occurred_this_time
                num_cmds_processed += num_cmds_processed_this_time
            # check if at least one API called and/or onn expired and is not processed yet
            #LOG.info(f"clusterObj:{clusterObj.org.name} {Cluster.objects.count()} {clusterObj.org.id}")
            process_num_node_table(orgAccountObj,(num_cmds_processed==0 and setup_occurred))
    except Exception as e:
        LOG.exception(f'Exception caught for {orgAccountObj.name}')
        LOG.info(f"sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
        sleep(COOLOFF_SECS)
    return (orgAccountObj.num_ps_cmd == start_cmd_cnt) # task is idle if no new commands were processed 

def process_state_change(org_name):
    '''
    Process state changes for an organization account.

    Args:
        org_name (str): Name of the organization.

    Returns:
        tuple: A tuple containing a boolean indicating whether the org is idle 
               and an integer of the loop count.
    '''
    try:
        orgAccountObj = OrgAccount.objects.get(name=org_name)
    except OrgAccount.DoesNotExist:
        LOG.error(f"OrgAccount with name {org_name} does not exist.")
        return False, 0
    key = f"idle_cnt_{orgAccountObj.name}"
    
    LOG.info(f"BEFORE {'{:>10}'.format(orgAccountObj.loop_count)}/{cache.get(key, 0)} {orgAccountObj.name} ps:{orgAccountObj.num_ps_cmd} ops:{orgAccountObj.num_owner_ps_cmd} ")
    
    is_idle = process_prov_sys_tbls(orgAccountObj)
    
    idle_cnt = int(cache.get(f"idle_cnt_{orgAccountObj.name}", 0))
    if is_idle:
        LOG.info(f"{orgAccountObj.name} is idle")
        cache.set(f"idle_cnt_{orgAccountObj.name}", idle_cnt+1)
    
    OrgAccount.objects.filter(name=org_name).update(loop_count=F('loop_count') + 1) # F make this inline and an atomic update
    LOG.info(f"AFTER  {'{:>10}'.format(orgAccountObj.loop_count+1)}/{cache.get(key, 0)} {orgAccountObj.name} ps:{orgAccountObj.num_ps_cmd} ops:{orgAccountObj.num_owner_ps_cmd} ")
    return is_idle, orgAccountObj.loop_count+1 # +1 because we updated the loop_count above inline

def get_redis_host():
    return os.environ.get("REDIS_HOST", "redis")
def get_redis_port():
    return os.environ.get("REDIS_PORT", "6379")
def get_redis_db():
    return os.environ.get("REDIS_DB", "0")

def log_job_status(job):
    # Get the job's status
    status = job.get_status()
    
    # Log and handle different statuses
    if status == 'finished':
        LOG.info("Job completed successfully!")
    elif status == 'failed':
        LOG.error("Job failed!")
    elif status == 'started':
        LOG.info("Job has started...")
    elif status == 'queued':
        LOG.info("Job is queued.")
    elif status == 'deferred':
        LOG.warning("Job is deferred.")
    else:
        LOG.warning("Job has an unknown status: %s" % status)

def enqueue_process_state_change(name: str) -> bool:
    '''
    Enqueue a job for name to run the process_state_change function.

    This function will chain jobs for name together, ensuring the next job will
    not start until the previous one is done.

    Parameters:
    - name: str, The name for which the process_state_change job should be enqueued.

    Returns:
    - bool: True if the job was enqueued successfully, False otherwise.
    '''
    LOG.info(f"enqueue_process_state_change for {name}")
    #import pdb; pdb.set_trace()

    if get_PROVISIONING_DISABLED():
        LOG.critical(f"enqueue_process_state_change NOT enqueued for {name} because PROVISIONING_DISABLED is True")
        return False
    
    redis_key = f"process_state_change:{name}:job_id"
    
    with cache.lock(f"enqueue_lock:{name}", timeout=10):
        try:
            last_job_id = cache.get(redis_key)
            depends_on = None
            if last_job_id:
                cmd_queue = django_rq.get_queue('cmd')
                last_job = Job.fetch(last_job_id,connection=cmd_queue.connection)
                LOG.info(f"last_job_id:{last_job_id} last_job:{last_job}")
                if last_job.is_queued or last_job.is_started or last_job.is_deferred:
                    depends_on = last_job
        except (ConnectionError, KeyError) as e:  # Example of specific exceptions
            LOG.error(f"Failed to fetch or handle the last job for {name}. Error: {str(e)}", exc_info=True)  # Include traceback
            return False 
        except NoRedisConnectionException as e:
            LOG.error(f"Failed to connect to redis for {name} last_job_id:{last_job_id}. Error: {str(e)}", exc_info=True)    
            return False   
        try:
            cmd_queue = django_rq.get_queue('cmd')
            new_job = cmd_queue.enqueue(process_state_change, name, depends_on=depends_on)
            cache.set(redis_key, new_job.get_id())
        except Exception as e:
            LOG.error(f"Failed to enqueue the job for {name}. Error: {str(e)}")
            return False
        
    LOG.info(f"Job for {name} enqueued with ID: {new_job.get_id()} depends_on: {depends_on}")
    return True

def purge_old_PsCmdResultsForOrg(this_org):
    purge_time = datetime.now(timezone.utc)-timedelta(days=this_org.pcqr_retention_age_in_days)
    LOG.info(f"started with {PsCmdResult.objects.filter(org=this_org).count()} for {this_org.name} {purge_time}")
    PsCmdResult.objects.filter(expiration__lte=(purge_time)).filter(org=this_org).delete()    
    LOG.info(f"ended with {PsCmdResult.objects.filter(org=this_org).count()} for {this_org.name}")

