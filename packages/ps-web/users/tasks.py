from celery import shared_task,Task, app
from users.models import PsCmdResult,OwnerPSCmd
from django_celery_results.models import TaskResult
from users.models import OrgAccount,PsCmdResult, NodeGroup, GranChoice, OrgAccount, Cost, User, ClusterNumNode, PsCmdResult, Membership
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
from celery.schedules import crontab
from uuid import UUID
from time import sleep
from users.global_constants import *
import redis
import json
import logging
LOG = logging.getLogger('django')
#LOG.propagate = False

class RedisInterface:
    def __init__(self):
        self.redis_conn = None
        self.host = os.environ.get("REDIS_HOST", "localhost")

    def get_connection(self):
        if not self.redis_conn:
            self.redis_conn = redis.Redis(host=self.host, port=6379, db=1)
        return self.redis_conn

    def server_is_up(self):
        try:
            redis_connection = self.get_connection()
            if redis_connection.ping():
                pass
        except redis.ConnectionError:
            LOG.critical("The redis server isn't responding.")        
            return False
        return True

redis_interface = RedisInterface()
redis_connection = redis_interface.get_connection()

def set_PROVISIONING_DISABLED(val):
    LOG.critical(f"set_PROVISIONING_DISABLED({val})")
    try:
        redis_connection.set('PROVISIONING_DISABLED', val)
    except Exception as e:
        LOG.critical(f"set_PROVISIONING_DISABLED({val}) failed with {e} ")

def get_PROVISIONING_DISABLED():
    try:
        state = redis_connection.get('PROVISIONING_DISABLED').decode('utf-8')  == 'True'
        if state is None:
            # initialize to False
            LOG.critical("PROVISIONING_DISABLED is None; Setting to False")
            set_PROVISIONING_DISABLED('False')
            state = False
        if state != False:
            LOG.critical(f"PROVISIONING_DISABLED is {state}")
    except Exception as e:
        LOG.critical(f"get_PROVISIONING_DISABLED() failed with {e} ")
        state = False
    return state

def get_node_group_queue_name_str(cluster_name):
    return f"ps-cmd-{cluster_name}"

def get_node_group_queue_name(nodeGroupObj):
    return get_node_group_queue_name_str(nodeGroupObj.__str__()) # has org name and cluster name to be unique

def flush_expired_refresh_tokens():
    SHELL_CMD=f"python manage.py flushexpiredtokens".split(" ")
    LOG.info(f"subprocess--> {SHELL_CMD}")
    subprocess.Popen(SHELL_CMD)

def format_onn(cnn):
    return "{"+ f"{OrgAccount.objects.get(id=cnn.org_id).name},{cnn.user.username},{cnn.desired_num_nodes},{cnn.expiration.strftime('%Y-%m-%d %H:%M:%S %Z')if cnn.expiration is not None else 'None'} " +"}"

def format_num_nodes_tbl(org):
    msg = '['
    n=0
    for cnn in ClusterNumNode.objects.filter(org=org).order_by('expiration'):
        if n != 0:
            msg = msg + ","
        msg = msg + format_onn(cnn)
        n = n + 1
    msg = msg + "]"
    return msg 

def sort_CNN_by_nn_exp(nodeGroupObj):
    return ClusterNumNode.objects.filter(cluster=nodeGroupObj).order_by('-desired_num_nodes','expiration')

def sum_of_highest_nodes_for_each_user(nodeGroupObj):
    '''
        This routine is used to determine the number of nodes to use for the cluster.
        First, fetch the maximum desired_num_nodes for each user using annotate.
        Then, filter the ClusterNumNode table again to get the entries that match these maximum values for each user.
        Finally, calculate the total and gather the list of IDs.
    
    '''
    # Get the highest desired_num_nodes for each user within the provided OrgAccount instance
    highest_nodes_per_user = (ClusterNumNode.objects
                              .filter(cluster=nodeGroupObj)
                              .values('user')
                              .annotate(max_nodes=Max('desired_num_nodes')))

    # Filter the ClusterNumNode table to get the entries that match these maximum values for each user within the OrgAccount
    ids_list = []
    for entry in highest_nodes_per_user:
        ids = (ClusterNumNode.objects
               .filter(user_id=entry['user'], desired_num_nodes=entry['max_nodes'], cluster=nodeGroupObj)
               .values_list('id', flat=True))
        string_ids = [str(id) for id in ids]  # Convert each UUID to string
        ids_list.extend(string_ids)
    # Sum up the highest nodes for all users within the OrgAccount
    num_nodes_to_deploy = sum(entry['max_nodes'] for entry in highest_nodes_per_user)
    if (int(num_nodes_to_deploy) < nodeGroupObj.cfg_asg.min):
        #LOG.info(f"Clamped num_nodes_to_deploy to min_node_cap:{nodeGroupObj.cfg_asg.min} from {num_nodes_to_deploy}")
        num_nodes_to_deploy = nodeGroupObj.cfg_asg.min
    if(int(num_nodes_to_deploy) > nodeGroupObj.cfg_asg.max):
        #LOG.info(f"Clamped num_nodes_to_deploy to max_node_cap:{nodeGroupObj.cfg_asg.max} from {num_nodes_to_deploy}")
        num_nodes_to_deploy = nodeGroupObj.cfg_asg.max
    nodeGroupObj.cnnro_ids = ids_list
    nodeGroupObj.save(update_fields=['cnnro_ids'])
    return num_nodes_to_deploy, ids_list

def cull_expired_entries(org,tm):
    LOG.debug(f"started with {ClusterNumNode.objects.filter(org=org).count()} ClusterNumNode for {org.name}")
    for cnn in ClusterNumNode.objects.filter(org=org).order_by('expiration'):
        LOG.debug(f"cnn.expiration:{cnn.expiration} tm(now):{tm}")
        if(cnn.expiration <= tm):
            LOG.info(f"deleting expired/null ClusterNumNode request {format_onn(cnn)}")
            cnn.delete()
        else:
            LOG.debug("nothing to delete")
            break
    LOG.debug(f"ended with {ClusterNumNode.objects.filter(org=org).count()} ClusterNumNode for {org.name}")


def need_destroy_for_changed_version_or_is_public(nodeGroupObj,num_nodes_to_deploy):
    # LOG.debug(f"cluster v:{nodeGroupObj.cur_version} ip:{nodeGroupObj.is_public} is_deployed:{nodeGroupObj.is_deployed}")
    # LOG.debug(f"    org v:{nodeGroupObj.version} ip:{nodeGroupObj.is_public}")
    if nodeGroupObj.is_deployed:
        changed_version = (nodeGroupObj.cur_version != nodeGroupObj.version)
        changed_is_public = (nodeGroupObj.is_public != nodeGroupObj.is_public)
        #LOG.debug(f"changed_version:{changed_version} changed_is_public:{changed_is_public}")
        if changed_version or changed_is_public:
            #LOG.debug(f"changed_version:{changed_version} changed_is_public:{changed_is_public}")
            if num_nodes_to_deploy != nodeGroupObj.cfg_asg.num: # we (changed version or is_public) and we are processing a new set of top items (new deployment request)
                LOG.info(f"num_nodes_to_deploy:{num_nodes_to_deploy} nodeGroupObj.cfg_asg.num:{nodeGroupObj.cfg_asg.num} need_destroy_for_changed_version_or_is_public: True")
                return True
    return False

def clean_up_CNN_cnnro_ids(nodeGroupObj,suspend_provisioning):
    if nodeGroupObj.cnnro_ids is not None:

        # Get the list of string UUIDs from the JSONField
        string_uuids = nodeGroupObj.cnnro_ids

        # Convert each string UUID to a UUID object
        uuids_list = [UUID(id) for id in string_uuids]

        # Fetch the ClusterNumNode instances
        cnns = ClusterNumNode.objects.filter(id__in=uuids_list)
        LOG.info(f"REMOVING ClusterNumNode nodeGroupObj.cnnro_ids: {cnns}")
        for cnn in cnns:
            cnn.delete()
        cnt = ClusterNumNode.objects.filter(cluster=nodeGroupObj).count()
        nodeGroupObj.cnnro_ids = None
        nodeGroupObj.save(update_fields=['cnnro_ids'])
    if suspend_provisioning:
        nodeGroupObj.provisioning_suspended = True
        nodeGroupObj.save(update_fields=['provisioning_suspended'])
        LOG.warning(f"provisioning_suspended for {nodeGroupObj.name}")

def check_provision_env_ready(nodeGroupObj):
    setup_occurred = False
    if not nodeGroupObj.provision_env_ready:
        st = datetime.now(timezone.utc)
        psCmdResultObj,org_cmd_str = get_psCmdResultObj(nodeGroupObj, 'SetUp', version=nodeGroupObj.version, username=nodeGroupObj.org.owner, is_adhoc=False)
        LOG.info(f"STARTED {org_cmd_str} is_public:{nodeGroupObj.is_public}")
        try:
            with ps_client.create_client_channel("control") as channel:
                stub = ps_server_pb2_grpc.ControlStub(channel)
                timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
                rsp_gen = stub.SetUp(
                    ps_server_pb2.SetUpReq(
                        name=nodeGroupObj.name,
                        version=nodeGroupObj.version,
                        is_public=nodeGroupObj.is_public,
                        now=datetime.now(timezone.utc).strftime(FMT)),
                        timeout=timeout)
                done = False
                setup_occurred = True
                nodeGroupObj.active_ps_cmd = 'SetUp'
                nodeGroupObj.save(update_fields=['active_ps_cmd'])
                while(not done): 
                    # make the call to get cached streamed response messages from server
                    #LOG.info(f"getting next response from ps-server...")
                    rrsp = None
                    try:
                        # Read until rrsp.done is True or until StopIteration exception is caught
                        rrsp = next(rsp_gen)  # grab the next one and process it
                        psCmdResultObj.ps_cmd_output += get_cli_html(rrsp.cli)
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
                                nodeGroupObj.provision_env_ready = True
                                ps_server_pb2.GetCurrentSetUpCfgRsp()
                                rsp = stub.GetCurrentSetUpCfg(ps_server_pb2.GetCurrentSetUpCfgReq(name=nodeGroupObj.org.name,cluster_name=nodeGroupObj.name))
                                nodeGroupObj.prov_env_version = rsp.setup_cfg.version
                                nodeGroupObj.prov_env_is_public = rsp.setup_cfg.is_public
                                if nodeGroupObj.prov_env_version == '':
                                    nodeGroupObj.provision_env_ready = False
                                    nodeGroupObj.provisioning_suspended = True
                                    nodeGroupObj.save(update_fields=['provisioning_suspended'])
                                    LOG.warning(f"{nodeGroupObj.org.name} cluster current_version is null Suspending provisioning")
                                LOG.info(f"{nodeGroupObj.org.name} cluster current_version:{nodeGroupObj.cur_version} provision_env_ready:{nodeGroupObj.provision_env_ready}")
                                nodeGroupObj.num_ps_cmd_successful += 1
                                nodeGroupObj.num_setup_cmd_successful += 1
                                nodeGroupObj.save(update_fields=['num_ps_cmd_successful','num_setup_cmd_successful'])
                                nodeGroupObj.save()
                            LOG.info(f"{org_cmd_str} got rrsp done from ps_server!")
        except Exception as e:
            error_msg = f"ERROR: {org_cmd_str}  {nodeGroupObj.version}:"
            LOG.exception(f"{error_msg} caught exception:") 
            psCmdResultObj.error = 'Server Error'
            psCmdResultObj.ps_cmd_summary_label = f" --- {org_cmd_str} {nodeGroupObj.version} ---"
            psCmdResultObj.save()

        finally:
            nodeGroupObj.active_ps_cmd = ''
            nodeGroupObj.save(update_fields=['active_ps_cmd'])
            time_to_process = datetime.now(timezone.utc) - st
            time_to_process = time_to_process - timedelta(microseconds=time_to_process.microseconds)
            LOG.info(f"DONE {org_cmd_str} {nodeGroupObj.version} has completed in {str(time_to_process)}")
            if nodeGroupObj.provisioning_suspended != nodeGroupObj.provision_env_ready:
                nodeGroupObj.provisioning_suspended = not nodeGroupObj.provision_env_ready
                nodeGroupObj.save(update_fields=['provisioning_suspended'])
    return nodeGroupObj.provision_env_ready,setup_occurred       

def process_num_node_table(nodeGroupObj,prior_need_refresh):
    '''
    This routine is called in the main loop (high frequency).
    If the the ClusterNumNode table changed and the highest num nodes desired 
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
    # NOTE: Be careful where you put log statements in this routine
    try:
        if not nodeGroupObj.provisioning_suspended: 
            env_ready,setup_occurred = check_provision_env_ready(nodeGroupObj)
            start_num_ps_cmds = nodeGroupObj.num_ps_cmd
            if env_ready:
                cull_expired_entries(nodeGroupObj,datetime.now(timezone.utc))
                num_nodes_to_deploy,cnnro_ids = sum_of_highest_nodes_for_each_user(nodeGroupObj)
                expire_time = None
                onnTop = sort_CNN_by_nn_exp(nodeGroupObj).first()
                if onnTop is not None:
                    if need_destroy_for_changed_version_or_is_public(nodeGroupObj,num_nodes_to_deploy):
                        try:
                            LOG.info(f"TRIGGERED Destroy {nodeGroupObj.org.name} --> cluster v:{nodeGroupObj.cur_version} cluster is_public:{nodeGroupObj.is_public} is_deployed:{nodeGroupObj.is_deployed} v:{nodeGroupObj.version} orgAccount ip:{nodeGroupObj.is_public} onnTop.desired_num_nodes:{onnTop.desired_num_nodes} nodeGroupObj.cfg_asg.num:{nodeGroupObj.cfg_asg.num}")
                            process_Destroy_cmd(nodeGroupObj=nodeGroupObj, username=nodeGroupObj.org.owner.username)
                        except Exception as e:
                            LOG.exception("ERROR processing Destroy when version or is_public changes in CNN: caught exception:")
                            clean_up_CNN_cnnro_ids(nodeGroupObj,suspend_provisioning=True)
                            LOG.info(f"{nodeGroupObj.org.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                            sleep(COOLOFF_SECS)
                    else:
                        user = onnTop.user
                        expire_time = onnTop.expiration
                        if num_nodes_to_deploy != nodeGroupObj.cfg_asg.num: 
                            deploy_values ={'min_node_cap': nodeGroupObj.cfg_asg.min, 'desired_num_nodes': num_nodes_to_deploy , 'max_node_cap': nodeGroupObj.cfg_asg.max, 'version': nodeGroupObj.version, 'is_public': nodeGroupObj.is_public, 'expire_time': expire_time }
                            LOG.info(f"{nodeGroupObj.org.name} Using top entries of each user sorted by num/exp_tm  with num_nodes_to_set:{onnTop.desired_num_nodes} exp_time:{expire_time} ")
                            try:
                                process_Update_cmd(nodeGroupObj=nodeGroupObj, username=user.username, deploy_values=deploy_values, expire_time=expire_time)
                            except Exception as e:
                                LOG.exception(f"{e.message} processing top CNN id:{onnTop.id} Update {nodeGroupObj.org.name} {user.username} {deploy_values} Exception:")
                                clean_up_CNN_cnnro_ids(nodeGroupObj,suspend_provisioning=False)
                                LOG.info(f"{nodeGroupObj.org.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                                sleep(COOLOFF_SECS)
                            nodeGroupObj.num_onn += 1
                            nodeGroupObj.save(update_fields=['num_onn'])
                            LOG.info(f"Update {nodeGroupObj.org.name} processed")
                else:
                    # No entries in table
                    user = nodeGroupObj.org.owner
                    if nodeGroupObj.destroy_when_no_nodes and (nodeGroupObj.cfg_asg.min == 0):
                        if nodeGroupObj.is_deployed:
                            LOG.info(f"org:{nodeGroupObj.org.name} destroy_when_no_nodes:{nodeGroupObj.destroy_when_no_nodes} min_node_cap:{nodeGroupObj.cfg_asg.min}")
                            try:
                                process_Destroy_cmd(nodeGroupObj=nodeGroupObj, username=user.username)
                            except Exception as e:
                                LOG.exception("ERROR processing Destroy {nodeGroupObj.org.name} when no entries in CNN: caught exception:")
                                LOG.warning(f"Destroy {nodeGroupObj.org.name} FAILED when no entries in CNN; Setting destroy_when_no_nodes to False")
                                nodeGroupObj.destroy_when_no_nodes = False
                                nodeGroupObj.cfg_asg.min = 0 
                                nodeGroupObj.cfg_asg.num = 0
                                nodeGroupObj.save(update_fields=['destroy_when_no_nodes','min_node_cap','desired_num_nodes'])
                                LOG.info(f"{nodeGroupObj.org.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                                sleep(COOLOFF_SECS)

                            nodeGroupObj.num_onn += 1
                            nodeGroupObj.save(update_fields=['num_onn'])
                            LOG.info(f"{nodeGroupObj.org.name} Destroy processed")
                    else:
                        if nodeGroupObj.cfg_asg.min != nodeGroupObj.cfg_asg.num: 
                            num_entries = ClusterNumNode.objects.filter(cluster=nodeGroupObj).count()
                            LOG.info(f"{nodeGroupObj.org.name} ({num_entries} (i.e. no) entries left; using min_node_cap:{nodeGroupObj.cfg_asg.min} exp_time:None")
                            deploy_values ={'min_node_cap': nodeGroupObj.cfg_asg.min, 'desired_num_nodes': nodeGroupObj.cfg_asg.min, 'max_node_cap': nodeGroupObj.cfg_asg.max,'version': nodeGroupObj.version, 'is_public': nodeGroupObj.is_public, 'expire_time': expire_time }
                            try:
                                process_Update_cmd(nodeGroupObj=nodeGroupObj, username=user.username, deploy_values=deploy_values, expire_time=expire_time)
                            except Exception as e:
                                LOG.exception("ERROR in Update {nodeGroupObj.org.name} ps_cmd when no entries in CNN and min != desired: caught exception:")
                                LOG.warning(f"Setting {nodeGroupObj.org.name} min_node_cap to zero; Update FAILED when no entries in CNN and min_node_cap != desired_num_nodes (i.e. current target, assumed num nodes)")
                                nodeGroupObj.cfg_asg.min = 0 
                                nodeGroupObj.cfg_asg.num = 0
                                nodeGroupObj.save(update_fields=['min_node_cap','desired_num_nodes'])
                                LOG.info(f"{nodeGroupObj.org.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                                sleep(COOLOFF_SECS)

                            nodeGroupObj.num_onn += 1
                            nodeGroupObj.save(update_fields=['num_onn'])
                            LOG.info(f"{nodeGroupObj.org.name} Update processed")
                # if we setup the env but did not process any commands, then we need to Refresh to set state
                need_refresh = False
                if setup_occurred:
                    if nodeGroupObj.num_ps_cmd == start_num_ps_cmds:
                        # setup with no commands processed
                        need_refresh = True
                else:
                    if prior_need_refresh and (nodeGroupObj.num_ps_cmd == start_num_ps_cmds):
                        # setup with no commands processed
                        need_refresh = True
                if need_refresh:
                    try:
                        LOG.info(f"Refresh {nodeGroupObj.org.name} post SetUp")
                        process_Refresh_cmd(nodeGroupObj=nodeGroupObj, username=nodeGroupObj.owner.username)
                    except Exception as e:
                        LOG.exception("ERROR processing Refresh {nodeGroupObj.org.name} caught exception:")
                        LOG.info(f"{nodeGroupObj.org.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
                        sleep(COOLOFF_SECS)
    except Exception as e:
        LOG.exception(f"{nodeGroupObj.org.name} caught exception:")
        LOG.info(f"{nodeGroupObj.org.name} sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
        for handler in LOG.handlers:
            handler.flush()
        sleep(COOLOFF_SECS)

def get_or_create_ClusterNumNodes(nodeGroupObj,user,desired_num_nodes,expire_date):
    # if it doesn't exist create it then process all clusterNumNodes for org                                          
    # if expire date comes from jwt then it will match
    # First try exact match
    # then create one
    clusterNumNode = None
    redundant = False
    msg = ''
    try:
        clusterNumNode,created = ClusterNumNode.objects.get_or_create(user=user,cluster=nodeGroupObj, desired_num_nodes=desired_num_nodes,expiration=expire_date)
        if created:
            if expire_date is not None:
                msg = f"Created new entry for {nodeGroupObj} {user.username} desired_num_nodes {clusterNumNode.desired_num_nodes} uuid:{clusterNumNode.id} expiration:{expire_date.strftime(FMT) if expire_date is not None else 'None'}"
            else:
                msg = f"Created new entry for {nodeGroupObj} {user.username} desired_num_nodes {clusterNumNode.desired_num_nodes} uuid:{clusterNumNode.id} with NO expiration"
        else:         
            msg = f"An entry already exists for {nodeGroupObj} {user.username} desired_num_nodes {clusterNumNode.desired_num_nodes} uuid:{clusterNumNode.id} expiration:{expire_date.strftime(FMT) if expire_date is not None else 'None'}"
            redundant = True
        LOG.info(f"{msg} cnt:{ClusterNumNode.objects.count()}")

    except Exception as e:
        LOG.exception("caught exception:")
        clusterNumNode = None
    return clusterNumNode,redundant,msg

def process_num_nodes_api(org_name,cluster_name,user,desired_num_nodes,expire_time):
    '''
        processes the APIs for setting desired num nodes
    '''
    try:
        jstatus = ''
        LOG.info(f"process_num_nodes_api({org_name},{cluster_name},{user},{desired_num_nodes},{expire_time})")
        if int(desired_num_nodes) < 0:
            msg = f"desired_num_nodes:{desired_num_nodes} must be >= 0"
            raise ValidationError(msg)
        nodeGroupObj = NodeGroup.objects.get(name=cluster_name)
        if (not nodeGroupObj.is_deployed) and (not nodeGroupObj.allow_deploy_by_token):
            msg = f"NodeGroup {nodeGroupObj} is not configured to allow deploy by token"
            raise ClusterDeployAuthError(msg)
        if(not nodeGroupObj.is_deployed):
            msg = f"Deploying {nodeGroupObj.org.name} cluster"
        else:
            msg = f"Updating {nodeGroupObj.org.name} cluster"

        clusterNumNode,redundant,onn_msg = get_or_create_ClusterNumNodes(user=user,
                                                                    cluster=nodeGroupObj,
                                                                    desired_num_nodes=desired_num_nodes,
                                                                    expire_date=expire_time)
        msg += f" {onn_msg}"
        if clusterNumNode:
            if redundant:
                msg += f" using identical queued capacity request for {clusterNumNode.org.name} from {clusterNumNode.user.username} with {desired_num_nodes} nodes to expire:{expire_time.strftime(FMT) if expire_time is not None else 'exp_tm:None'}"
                jstatus = 'REDUNDANT'
            else:
                msg += f" created and queued capacity request for {clusterNumNode.org.name} from {user.username} with {desired_num_nodes} nodes to expire:{expire_time.strftime(FMT) if expire_time is not None else 'exp_tm:None'}"
                jstatus = 'QUEUED'
            jrsp = {'status':jstatus,"msg":msg,'error_msg':''}
            status = 200
        else:
            emsg = f"FAILED to process request for {org_name} {cluster_name} {user} {desired_num_nodes} {expire_time} - Server Error"
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

def get_versions():
    try:
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            versions_rsp = stub.GetVersions(ps_server_pb2.GetVersionsReq())
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

def perform_cost_accounting_for_all_clusters():
    c_qs = NodeGroup.objects.all()
    LOG.info("orgs_qs:%s", repr(c_qs))
    for c in c_qs:
        cost_accounting(c)

def get_current_cost_report(name, gran, time_now):
    now_str = datetime.strftime(time_now, FMT_Z)
    LOG.info(f"{name} {gran} {now_str}")
    with ps_client.create_client_channel("account") as channel:
        ac = ps_server_pb2_grpc.AccountStub(channel)
        rsp = ac.CurrentCost(
            ps_server_pb2.CurrentCostReq(name=name, granularity=gran, tm=now_str))
    #LOG.info("Sending rsp...")
    return MessageToJson(rsp), rsp

def get_org_cost_data(nodeGroupObj, granObj, clusterCostObj):
    THIS_FMT = FMT_DAILY
    updated = False
    time_now = datetime.now(timezone.utc)
    time_stale = time_now - clusterCostObj.cost_refresh_time
    LOG.info("%s %s now:%s/%s clusterCostObj.cost_refresh_time:%s time_stale:%s > %s ?", nodeGroupObj.org.name,
             granObj.granularity, datetime.now(timezone.utc), datetime.now(), clusterCostObj.cost_refresh_time, time_stale, timedelta(hours=8))
    if time_stale > timedelta(hours=8) or str(clusterCostObj.ccr) == "{}" or str(clusterCostObj.ccr) == NULL_CCR:
        #LOG.info("Calling get_current_cost_report <<<<<<<<<<<-------------->>>>>>>>>>>")
        ccr, rsp = get_current_cost_report(nodeGroupObj.org.name, granObj.granularity, time_now)
        if rsp.server_error == False:
            clusterCostObj.cost_refresh_time = time_now
            nodeGroupObj.most_recent_recon_time = time_now 
            if len(rsp.tm) > 0:
                if(clusterCostObj.gran.granularity == 'HOURLY'):
                    THIS_FMT = FMT_Z
                most_recent_tm_str = rsp.tm[len(rsp.tm)-1]
                most_recent_tm = datetime.strptime(most_recent_tm_str, THIS_FMT).replace(tzinfo=pytz.utc)
                clusterCostObj.tm = most_recent_tm
                clusterCostObj.cnt = rsp.stats.cnt
                clusterCostObj.avg = rsp.stats.avg
                clusterCostObj.min = rsp.stats.min
                clusterCostObj.max = rsp.stats.max
                clusterCostObj.std = rsp.stats.std
                clusterCostObj.ccr = ccr
                updated = True
                #LOG.info("Saved %s clusterCostObj for:%s tm:%s ccr=%s",
                #         clusterCostObj.gran.granularity,  clusterCostObj.org.name, clusterCostObj.tm, ccr)
                LOG.info("Saved %s clusterCostObj for:%s tm:%s",
                        clusterCostObj.gran.granularity,  clusterCostObj.org.name, clusterCostObj.tm)
            else:
                LOG.info("No cost data for %s %s",nodeGroupObj.org.name, granObj.granularity)
                if str(clusterCostObj.ccr) == "{}":
                    clusterCostObj.ccr = "{ }" # so keep from reading null CCRs
            clusterCostObj.save() # this only saves the updated clusterCostObj.cost_refresh_time
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

def update_nodeGroupCost(nodeGroupObj, gran):
    granObj = getGranChoice(gran)
    get_data = False
    clusterCostObj = None
    try:
        clusterCostObj = Cost.objects.get(cluster=nodeGroupObj, gran=granObj)
    except ObjectDoesNotExist as e:
        LOG.warning(f"no clusterCostObj for {nodeGroupObj.org.name} {granObj.granularity}")
        clusterCostObj = Cost(cluster=nodeGroupObj, gran=granObj, cost_refresh_time=datetime.now(timezone.utc)-timedelta(weeks=52),tm=datetime.now(timezone.utc))
        LOG.info("%s %s %s New clusterCostObj created", clusterCostObj.org.name,clusterCostObj.gran.granularity, clusterCostObj.tm)
        get_data = True
    if clusterCostObj is not None:
        #LOG.info(datetime.now(timezone.utc))
        diff_tm = datetime.now(timezone.utc) - clusterCostObj.cost_refresh_time
        #LOG.info("%s %s %s - %s = %s", nodeGroupObj.org.name, gran,datetime.now(timezone.utc), clusterCostObj.tm, diff_tm)
        if str(clusterCostObj.ccr) == "{}" or str(clusterCostObj.ccr) == NULL_CCR:
            get_data = True
            LOG.info("Triggered by empty set")
        else:
            # the aws cost explorer updates 3x a day
            if diff_tm > timedelta(hours=8):
                LOG.info("Triggered by stale ccr > 8 hrs")
                get_data = True
    else:
        LOG.error("FAILED to create clusterCostObj for %s %s",nodeGroupObj.org.name, granObj.granularity)
        get_data = False
    updated = False
    if get_data:
        # will create clusterCostObj if needed
        LOG.info("calling get_org_cost_data for %s %s",nodeGroupObj.org.name, granObj.granularity)
        updated = get_org_cost_data(nodeGroupObj, granObj, clusterCostObj)

    next_refresh_time = clusterCostObj.cost_refresh_time +  timedelta(hours=8)
    if updated:
        LOG.info("%s CCR DID     update. Last refresh was: %s next refresh will be: %s",gran,clusterCostObj.cost_refresh_time , next_refresh_time )
    else:
        LOG.info("%s CCR did not update. Last refresh was: %s next refresh will be: %s",gran,clusterCostObj.cost_refresh_time , next_refresh_time )

    return updated


def update_ccr(nodeGroupObj):
    '''
        updates the current cost report from the ps-server 
    '''
    updated = (update_nodeGroupCost(nodeGroupObj, "HOURLY") or update_nodeGroupCost(nodeGroupObj, "DAILY") or update_nodeGroupCost(nodeGroupObj, "MONTHLY"))
    LOG.info("updated:%s",updated)
    return updated

def update_cur_num_nodes(nodeGroupObj):
    #LOG.info(f"update_cur_num_nodes:{nodeGroupObj.org.name}")
    with ps_client.create_client_channel("account") as channel:
        try:
            ac = ps_server_pb2_grpc.AccountStub(channel)
            region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
            req = ps_server_pb2.NumNodesReq(name=nodeGroupObj.org.name,cluster_name=nodeGroupObj.name,version=nodeGroupObj.org.version,region=region)
            #LOG.info(req)
            rsp = ac.NumNodes(req)
            nodeGroupObj.cur_asg.num = rsp.num_nodes
            nodeGroupObj.save(update_fields=['cur_nodes'])
            LOG.info(f"update_cur_num_nodes:{nodeGroupObj} cur_nodes:{nodeGroupObj.cur_asg.num}")
            return nodeGroupObj.cur_asg.num
        except Exception as e:
            LOG.error(f"FAILED: caught exception on NumNodesReq")
            raise

def clamp_time(hours):
    days = 0
    if hours < 0:
        hours = 0
    else:
        if hours > 24:
            days = int(hours/24)
            if days < 365*5:
                hours = hours - days*24
            else:
                days = 365*5
                hours = 0
    return days,hours


def update_burn_rates(nodeGroupObj):
    global FMT
    try:
        update_cur_num_nodes(nodeGroupObj)

        min_nodes = nodeGroupObj.cur_asg.min
        max_nodes = nodeGroupObj.cur_asg.max

        if min_nodes > 0:
            forecast_min_hrly = nodeGroupObj.node_mgr_fixed_cost + min_nodes*nodeGroupObj.node_cost_per_hr
        else:
            forecast_min_hrly = 0.0001

        if nodeGroupObj.cur_asg.num > 0:
            forecast_cur_hrly = nodeGroupObj.node_mgr_fixed_cost + nodeGroupObj.cur_asg.num*nodeGroupObj.node_cost_per_hr
        else:
            forecast_cur_hrly = 0.0001

        forecast_max_hrly = nodeGroupObj.node_mgr_fixed_cost + max_nodes*nodeGroupObj.node_cost_per_hr

        nodeGroupObj.min_hrly      = forecast_min_hrly
        nodeGroupObj.cur_hrly      = forecast_cur_hrly
        nodeGroupObj.max_hrly      = forecast_max_hrly
        nodeGroupObj.save(update_fields=['min_hrly','cur_hrly','max_hrly'])
        #LOG.info(f"{nodeGroupObj.org.name} forecast min/cur/max hrly burn rate {forecast_min_hrly}/{forecast_cur_hrly}/{forecast_max_hrly}")

        min_days_left,min_hrs_left = clamp_time(float(nodeGroupObj.balance)/forecast_min_hrly)
        # LOG.info("%s = %s/%s    min_hrs_left = balance/forecast_min_hrly (assuming no allowance) ",
        #          min_hrs_left, nodeGroupObj.balance, forecast_min_hrly)
        nodeGroupObj.min_ddt = datetime.now(timezone.utc)+timedelta(days=min_days_left,hours=min_hrs_left)

        cur_days_left,cur_hrs_left = clamp_time(float(nodeGroupObj.balance)/forecast_cur_hrly)
        # LOG.info("%s = %s/%s    cur_hrs_left = balance/forecast_cur_hrly  (assuming no allowance) ",
        #          cur_hrs_left, nodeGroupObj.balance, forecast_cur_hrly)
        nodeGroupObj.cur_ddt = datetime.now(timezone.utc)+timedelta(days=cur_days_left,hours=cur_hrs_left)

        max_days_left,max_hrs_left = clamp_time(float(nodeGroupObj.balance)/forecast_max_hrly)
        # LOG.info("%s = %s/%s    max_hrs_left = balance/forecast_max_hrly  (assuming no allowance) ",
        #          max_hrs_left, nodeGroupObj.balance, forecast_max_hrly)
        nodeGroupObj.max_ddt = datetime.now(timezone.utc)+timedelta(days=max_days_left,hours=max_hrs_left)

        nodeGroupObj.save(update_fields=['min_ddt','cur_ddt','max_ddt'])
        # LOG.info("Assuming no allowance.... min_ddt: %s cur_ddt: %s max_ddt: %s",
        #          datetime.strftime(min_ddt, FMT),
        #          datetime.strftime(cur_ddt, FMT),
        #          datetime.strftime(max_ddt, FMT))
        return forecast_min_hrly, forecast_cur_hrly, forecast_max_hrly

    except Exception as e:
        LOG.exception('Exception caught')
        return None, None, None

def update_all_burn_rates():
    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
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

def calculate_account_bal_and_fytd_bal(obj,rsp):
    '''
    This routine does not and should not change obj (i.e. the OrgAccount object or NodeGroup object)
    It is READ ONLY
    '''
    new_fytd_accrued_cost = obj.budget.fytd_accrued_cost
    fytd_date = getFiscalStartDate()
    new_mrct = obj.budget.most_recent_charge_time
    new_balance = Decimal(obj.budget.balance)
    most_recent_tm_str = rsp.tm[len(rsp.tm)-1]
    if 'Z' in most_recent_tm_str: # i.e. are these hourly timestamps
        tm_fmt_to_use = FMT_Z
    else:
        tm_fmt_to_use = FMT_DAILY
    LOG.info(f"{obj.__str__()} most_recent_tm_str:{most_recent_tm_str} tm_fmt_to_use:{tm_fmt_to_use} len(rsp.cost):{len(rsp.cost)}")
    most_recent_tm = get_utc_tm(most_recent_tm_str, tm_fmt_to_use)
    LOG.info(f"now:{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} new_mrct:{new_mrct.strftime('%Y-%m-%d %H:%M:%S')} most_recent_tm:{most_recent_tm.strftime('%Y-%m-%d %H:%M:%S')} fytd_date:{fytd_date.strftime('%Y-%m-%d %H:%M:%S')}")
    if new_mrct < fytd_date and most_recent_tm > fytd_date:
        LOG.info(f"{obj.__str__()} is resetting FYTD accrued cost (tm crossed FY boundary)")
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
            #LOG.info(f"{obj.__str__()} for rsp.tm[{ndx}]:{tm} debiting {cost:.2f}")
            new_balance = new_balance - cost
            total_current = total_current + Decimal(cost) # for logging convenience
        ndx = ndx + 1
    if fytd_changed:  # use flag (avoid compare of float type?)
        LOG.info(f"{obj.__str__()} is debited (tm_fmt:{tm_fmt_to_use}) up to {datetime.strftime(obj.budget.most_recent_charge_time, FMT)} {total_current} with new new_fytd_accrued_cost:{new_fytd_accrued_cost:.2f}")
    else:
        LOG.info(f"{obj.__str__()} is debited (tm_fmt:{tm_fmt_to_use}) up to {datetime.strftime(obj.budget.most_recent_charge_time, FMT)} NO CHANGE fytd:{obj.budget.fytd_accrued_cost:.2f}")
    if bal_changed:  # use flag (avoid compare of float type?)
        LOG.info(f"{obj.__str__()} is debited (tm_fmt:{tm_fmt_to_use}) {total_current:.2f} up to {datetime.strftime(obj.budget.most_recent_charge_time, FMT)} with new balance:{new_balance:.2f}")
    else:
        LOG.info(f"{obj.__str__()} is debited (tm_fmt:{tm_fmt_to_use}) up to {datetime.strftime(obj.budget.most_recent_charge_time, FMT)} NO CHANGE balance:{obj.budget.balance:.2f}")
    return new_balance,new_fytd_accrued_cost,new_mrct

def get_tm_now_tuple():
    time_now = datetime.now(timezone.utc)
    time_now_str =  datetime.strftime(time_now, FMT_Z)
    return time_now,time_now_str

def reconcile_org(orgAccountObj):
    '''
    reconcile the budget for each cluster in the org
    then reconcile the org budget
    '''
    clusters_qs = NodeGroup.objects.filter(org=orgAccountObj)
    LOG.info("clusters_qs:%s", repr(clusters_qs))
    for c in clusters_qs:
        reconcile_budget(c.budget)
    # TBD: reconcile the org budget


def reconcile_budget(budgetObj):
    '''
    This routine reconciles the budget for a cluster or org
    '''
    parent_name = ''
    parentObj = budgetObj.content_object
    parent_name = parentObj.__str__()
    org_name = ''
    cluster_name = ''
    if isinstance(budgetObj,OrgAccount):
        org_name = parent_name
        LOG.info(f"reconcile_budget for OrgAccount:{parent_name}")
    elif isinstance(budgetObj,NodeGroup):
        org_name = budgetObj.org.name
        cluster_name = budgetObj.name
        LOG.info(f"reconcile_budget for NodeGroup:{parent_name}")
        parent_name = budgetObj.org.name
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
        LOG.info(f"{parent_name} now:{time_now_str} start_of_this_month:{start_of_this_month.strftime('%Y-%m-%d %H:%M:%S')} budgetObj.most_recent_credit_time:{budgetObj.most_recent_credit_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if start_of_this_month > budgetObj.most_recent_credit_time:
            # only ever give one month's credit
            budgetObj.balance = budgetObj.balance + budgetObj.monthly_allowance
            budgetObj.most_recent_credit_time = start_of_this_month
            LOG.info(f"{budgetObj.org.name} is credited up to {datetime.strftime(budgetObj.most_recent_credit_time, FMT)} with NEW balance:{budgetObj.balance:.2f} using {budgetObj.monthly_allowance:.2f} added")
        else:
            LOG.info(f"{budgetObj.org.name} is credited up to {datetime.strftime(budgetObj.most_recent_credit_time, FMT)} with NO CHANGE in balance:{budgetObj.balance:.2f} (nothing new to credit)")
        #
        # For Charges:
        # First charge every day until midnight last night, then do hourly of today
        #
        # truncating to start of day (i.e. uncharged days)
        start_of_today = time_now.replace(hour=0, minute=0, second=0, microsecond=0)
        LOG.info(f"{budgetObj.org.name} now:{time_now_str} budgetObj.most_recent_charge_time:{budgetObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')} start_of_today:{start_of_today.strftime('%Y-%m-%d %H:%M:%S')}")
        if budgetObj.most_recent_charge_time < start_of_today:
            # check if there are charges posted yet 
            start_tm_str    = datetime.strftime(budgetObj.most_recent_charge_time, FMT_Z)
            end_tm_str      = time_now_str
            #
            #  These are the Daily charges until midnight last night
            #  below is the Hourly for today
            #
            LOG.info(f"{parent_name} now:{time_now_str} start_tm_str:{start_tm_str} end_tm_str:{end_tm_str}")
            rsp = ac.DailyHistCost(ps_server_pb2.DailyHistCostReq(name=nodeGroupObj.org.name, cluster_name=nodeGroupObj.name, start_tm=start_tm_str, end_tm=end_tm_str))
            if rsp.server_error:
                LOG.error(f"{parent_name} DailyHistCost got this error:{rsp.error_msg}")
                raise Exception(f"ps server error caught:{rsp.error_msg}")            
            if len(rsp.tm) > 0:
                new_balance,new_fytd_accrued_cost,new_mrct = calculate_account_bal_and_fytd_bal(nodeGroupObj,rsp)
                #LOG.info(f"budgetObj.balance:{budgetObj.balance} new_balance:{new_balance}")
                if budgetObj.balance != new_balance:
                    budgetObj.balance = new_balance
                #LOG.info(f"budgetObj.fytd_accrued_cost:{budgetObj.fytd_accrued_cost} new_fytd_accrued_cost:{new_fytd_accrued_cost}")
                if budgetObj.fytd_accrued_cost != new_fytd_accrued_cost:
                    budgetObj.fytd_accrued_cost = new_fytd_accrued_cost
                #LOG.info(f"budgetObj.most_recent_charge_time:{budgetObj.most_recent_charge_time} new_mrct:{new_mrct}")
                if budgetObj.most_recent_charge_time != new_mrct:
                    LOG.info(f"####### {parent_name} updating budgetObj.most_recent_charge_time:{budgetObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')}-->{new_mrct.strftime('%Y-%m-%d %H:%M:%S')} from DailyHistCost")
                    budgetObj.most_recent_charge_time = new_mrct
                budgetObj.save()
            else:
                LOG.info(f"{parent_name} DID NOT change because there were no charges between  budgetObj.most_recent_charge_time:{budgetObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')} and beginning of today:{datetime.strftime(start_of_today,FMT)} it is debited up to {datetime.strftime(budgetObj.most_recent_charge_time,FMT)} NO CHANGE balance:{budgetObj.balance:.2f} (Cost Explorer report came back empty?)")
        else:
            LOG.info(f"{parent_name} is ALREADY debited daily mrct:{budgetObj.most_recent_charge_time.strftime('%Y-%m-%d %H:%M:%S')} up to start_of_today:{start_of_today.strftime('%Y-%m-%d %H:%M:%S')} NO CHANGE balance:%f (No new daily amounts to debit)")
        #
        #  Now do hourly 
        #
        start_of_this_hour = time_now.replace(minute=0, second=0, microsecond=0)
        #LOG.info(f"budgetObj.most_recent_charge_time:{budgetObj.most_recent_charge_time} start_of_this_hour:{start_of_this_hour}")
        if budgetObj.most_recent_charge_time < start_of_this_hour:
            LOG.info(f"calling TodaysCost with {parent_name} time_now_str:{time_now_str}")
            rsp = ac.TodaysCost(ps_server_pb2.TodaysCostReq(name=org_name, cluster_name=cluster_name, tm=time_now_str))
            if rsp.server_error:
                LOG.error(f"{parent_name} TodaysCost got this error:{rsp.error_msg}")
                raise Exception(f"ps server error caught:{rsp.error_msg}")            
            LOG.info(f"len(rsp.tm):{len(rsp.tm)}")
            if len(rsp.tm) > 0:
                new_balance,new_fytd_accrued_cost,new_mrct = calculate_account_bal_and_fytd_bal(budgetObj,rsp)
                #LOG.info(f"budgetObj.balance:{budgetObj.balance} new_balance:{new_balance}")
                if budgetObj.balance != new_balance:
                    budgetObj.balance = new_balance
                #LOG.info(f"budgetObj.fytd_accrued_cost:{budgetObj.fytd_accrued_cost} new_fytd_accrued_cost:{new_fytd_accrued_cost}")
                if budgetObj.fytd_accrued_cost != new_fytd_accrued_cost:
                    budgetObj.fytd_accrued_cost = new_fytd_accrued_cost
                #LOG.info(f"budgetObj.most_recent_charge_time:{budgetObj.most_recent_charge_time} new_mrct:{new_mrct}")
                if budgetObj.most_recent_charge_time != new_mrct:
                    budgetObj.most_recent_charge_time = new_mrct
                    LOG.info(f"####### {parent_name} updating mrct:{budgetObj.most_recent_charge_time} from TodaysCost")
                nodeGroupObj.save()
            else:
                LOG.info(f"{parent_name} is debited hourly up to {datetime.strftime(budgetObj.most_recent_charge_time, FMT)} NO CHANGE balance:{budgetObj.balance:.2f}")
        else:
            LOG.info(f"{parent_name} is ALREADY debited hourly mrct:{budgetObj.most_recent_credit_time} up to {datetime.strftime(start_of_this_hour,FMT)} NO CHANGE balance:{budgetObj.balance:.2f} (No new hourly amounts to debit)")
        #
        # Truncate to max allowed balance
        #
        if budgetObj.balance > budgetObj.max_allowance:
            budgetObj.balance = budgetObj.max_allowance
            budgetObj.save()
            LOG.info(f"{parent_name} truncating balance to max_allowance:{budgetObj.max_allowance:.2f}")
    LOG.info("---done---")

def is_budget_broke(budgetObj):
    parentObj = budgetObj.content_object
    parent_name = parentObj.__str__()
    if(budgetObj.balance < 0.5):
        LOG.info(f"{parent_name} is broke with remaining balance of:{budgetObj.balance:.2f}")
        broke_status = True
    else:
        LOG.info(f"{parent_name} has a remaining balance of:{budgetObj.balance:.2f}")
    return broke_status

def is_cluster_broke(nodeGroupObj):
    broke_status = False 
    if(nodeGroupObj.is_deployed):
        broke_status = is_budget_broke(nodeGroupObj.budget)
    else:
        LOG.info(f"{nodeGroupObj} deployed_state is {nodeGroupObj.deployed_state}")
    return broke_status


def is_org_broke(orgAccountObj):
    return is_budget_broke(orgAccountObj.budget)


def create_forecast(budgetObj, hourlyRate, daily_days_to_forecast=None, hourly_days_to_forecast=None):
    ''' 
        This routine calculates hourly,daily,and monthly forecasts for a given hourly rate.
        The tm represents the start time of the given period
    '''

    daily_days_to_forecast = daily_days_to_forecast or 91
    hourly_days_to_forecast = hourly_days_to_forecast or 14
    #LOG.info("%s %2g", {nodeGroupObj} {hrlyRate})
    global FMT_HOURLY, FMT_DAILY
    A_LONG_TIME_FROM_NOW = datetime.now(timezone.utc) + timedelta(days=DISPLAY_EXP_TM+DISPLAY_EXP_TM_MARGIN)
    drop_dead_time = A_LONG_TIME_FROM_NOW
    hrlyRate = float(hourlyRate)
    days_of_week,num_days_in_month = calendar.monthrange(budgetObj.most_recent_recon_time.year, budgetObj.most_recent_recon_time.month)
    ############# HOURLY #############
    tms = []
    bals = []
    tm_bal_tuple = []
    fraction_of_hr = (59.0-budgetObj.most_recent_recon_time.minute)/60.0 # mins are 0-59
    partial_hr_mins_charge = hourlyRate*(fraction_of_hr)
    hr_to_start = (budgetObj.most_recent_recon_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) 
    tm = hr_to_start
    bal = float(budgetObj.balance)-partial_hr_mins_charge
    # tbd fraction of first hour?
    while tm < (hr_to_start + timedelta(days=hourly_days_to_forecast)):
        #LOG.info(f"tm:{tm}")
        if tm.day == 1 and tm.hour == 0:
            bal = bal + float(budgetObj.monthly_allowance)
        bals.append(bal)
        formatted_tm = datetime.strftime(tm, FMT_HOURLY)
        tms.append(formatted_tm)
        tm_bal_tuple.append((formatted_tm,bal))
        bal = bal - hrlyRate 
        if bal <= 0.00:
            if tm < drop_dead_time:
                drop_dead_time = tm
            bal = 0.00
        tm = tm + timedelta(hours=1)
    fc_hourly = json.dumps({'tm': tms, 'bal': bals})
    fc_hourly_tm_bal = json.dumps(tm_bal_tuple)
    ############# DAILY #############
    tms = []
    bals = []
    tm_bal_tuple = []
    partial_day_hrly_charge = (23-budgetObj.most_recent_recon_time.hour)*hrlyRate # hrs are 0-23
    # day_to_start is begining of first whole day
    day_to_start = budgetObj.most_recent_recon_time.replace(hour=0,minute=0,second=0,microsecond=0) + timedelta(days=1)    
    tm = day_to_start
    bal = float(budgetObj.balance) - partial_day_hrly_charge
    while tm < (day_to_start + timedelta(days=daily_days_to_forecast)):
        if tm.day == 1:
            bal = bal + float(budgetObj.monthly_allowance)
        bals.append(bal)
        formatted_tm = datetime.strftime(tm, FMT_DAILY)
        tms.append(formatted_tm)
        tm_bal_tuple.append((formatted_tm,bal))
        bal = bal - (hrlyRate*24)
        if bal < 0.00:
            if tm < drop_dead_time:
                drop_dead_time = tm
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
    bal = float(budgetObj.balance) - partial_day_charge
    day = day_to_start.day  # 
    tm = day_to_start  # beginning of first full day
    if bal < 0:
         drop_dead_time = tm
         bal = 0.0
    #LOG.info(f"num_days_in_month:{num_days_in_month} day:{day}")
    bals.append(bal)
    formatted_tm = datetime.strftime(hr_to_start, FMT_MONTHLY)# MONTH fmt so need to be IN first partial month
    tms.append(formatted_tm)
    #LOG.info(f"appended:{formatted_tm} {bal}")
    tm_bal_tuple.append((formatted_tm,bal))
    while day <= num_days_in_month:  # 1 based - partial month
        if day == 1:
            bal = bal + float(budgetObj.monthly_allowance)
        bal = bal - (hrlyRate*24)
        if bal < 0.00:
            if tm < drop_dead_time:
                drop_dead_time = tm
            bal = 0.00
        day = day + 1
        tm = tm + timedelta(days=1)
    start_tm = tm  #  first full months
    #LOG.info(f"first day of whole months:{start_tm.strftime(FMT)}")
    while tm < (start_tm + timedelta(days=365)):
        # current month
        weekday,num_days_in_month = calendar.monthrange(tm.year,tm.month) # one month at a time
        bal = bal + float(budgetObj.monthly_allowance)
        bal = bal - (num_days_in_month*hrlyRate*24)
        if bal < 0.00:
            if tm < drop_dead_time:
                drop_dead_time = tm
            bal = 0.00
        bals.append(bal)
        formatted_tm = datetime.strftime(tm, FMT_MONTHLY)
        tms.append(formatted_tm)
        #LOG.info(f"appended:{formatted_tm} {bal}")
        tm_bal_tuple.append((formatted_tm,bal))
        tm = tm + timedelta(days=num_days_in_month)

    fc_monthly = json.dumps({'tm': tms, 'bal': bals})
    fc_monthly_tm_bal = json.dumps(tm_bal_tuple)
    return drop_dead_time, fc_hourly, fc_daily, fc_monthly, fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal

def create_all_forecasts(nodeGroupObj):
    update_cur_num_nodes(nodeGroupObj)
    LOG.info(f"Hourly burn rates: {nodeGroupObj.min_hrly}/{nodeGroupObj.cur_hrly}/{nodeGroupObj.max_hrly}")

    nodeGroupObj.min_ddt, nodeGroupObj.fc_min_hourly, nodeGroupObj.fc_min_daily, nodeGroupObj.fc_min_monthly,fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal  = create_forecast(nodeGroupObj, nodeGroupObj.min_hrly)
    #LOG.info(f"MIN fc_hourly_tm_bal:{fc_hourly_tm_bal} fc_daily_tm_bal:{fc_daily_tm_bal} fc_monthly_tm_bal:{fc_monthly_tm_bal} ")
    #LOG.info(f"MIN min_ddt:{nodeGroupObj.min_ddt.strftime(FMT)} fc_min_hourly:{nodeGroupObj.fc_min_hourly},fc_min_daily:{nodeGroupObj.fc_min_daily},fc_min_monthly:{nodeGroupObj.fc_min_monthly}")
    nodeGroupObj.cur_ddt, nodeGroupObj.fc_cur_hourly, nodeGroupObj.fc_cur_daily, nodeGroupObj.fc_cur_monthly,fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal = create_forecast(nodeGroupObj, nodeGroupObj.cur_hrly)
    #LOG.info(f"CUR fc_hourly_tm_bal:{fc_hourly_tm_bal} fc_daily_tm_bal:{fc_daily_tm_bal} fc_monthly_tm_bal:{fc_monthly_tm_bal} ")
    #LOG.info(f"CUR cur_ddt:{nodeGroupObj.cur_ddt.strftime(FMT)} fc_cur_hourly:{nodeGroupObj.fc_cur_hourly},fc_cur_daily:{nodeGroupObj.fc_cur_daily},fc_cur_monthly:{nodeGroupObj.fc_cur_monthly}")
    nodeGroupObj.max_ddt, nodeGroupObj.fc_max_hourly, nodeGroupObj.fc_max_daily, nodeGroupObj.fc_max_monthly,fc_hourly_tm_bal, fc_daily_tm_bal, fc_monthly_tm_bal = create_forecast(nodeGroupObj, nodeGroupObj.max_hrly)
    #LOG.info(f"MAX fc_hourly_tm_bal:{fc_hourly_tm_bal} fc_daily_tm_bal:{fc_daily_tm_bal} fc_monthly_tm_bal:{fc_monthly_tm_bal} ")
    #LOG.info(f"MAX max_ddt:{nodeGroupObj.max_ddt.strftime(FMT)} fc_max_hourly:{nodeGroupObj.fc_max_hourly},fc_max_daily:{nodeGroupObj.fc_max_daily},fc_max_monthly:{nodeGroupObj.fc_max_monthly}")

    LOG.info(f"min_ddt:{nodeGroupObj.min_ddt.strftime(FMT)},cur_ddt:{nodeGroupObj.cur_ddt.strftime(FMT)},max_ddt:{nodeGroupObj.max_ddt.strftime(FMT)}")
    nodeGroupObj.save(update_fields=['min_ddt','cur_ddt','max_ddt','fc_min_hourly','fc_min_daily','fc_min_monthly','fc_cur_hourly','fc_cur_daily','fc_cur_monthly','fc_max_hourly','fc_max_daily','fc_max_monthly'])


# def create_all_forecasts_for_all_orgs():
#     orgs_qs = OrgAccount.objects.all()
#     LOG.info("orgs_qs:%s", repr(orgs_qs))
#     for o in orgs_qs:
#         create_all_forecasts(o)

# def ad_hoc_cost_reconcile_for_org(orgObj):
#     update_ccr(orgObj)
#     update_burn_rates(orgObj)
#     reconcile_org(orgObj)
#     create_all_forecasts(orgObj)


# @shared_task(name="force_ad_hoc_cost_reconcile_uuid",bind=True)  # name is referenced in settings.py
# def force_ad_hoc_cost_reconcile_uuid(self,uuid):
#     LOG.info(f"Started -- force_ad_hoc_cost_reconcile_uuid({uuid}) {self.request.id}")
#     orgObj = OrgAccount.objects.get(id=uuid)
#     ad_hoc_cost_reconcile_for_org(orgObj)
#     LOG.info(f"Finished -- force_ad_hoc_cost_reconcile_uuid({uuid}) {self.request.id}")


# @shared_task(name="force_ad_hoc_cost_reconcile",bind=True)  # name is referenced in settings.py
# def force_ad_hoc_cost_reconcile(self):
#     LOG.info(f"Started -- force_ad_hoc_cost_reconcile {self.request.id}")
#     orgs_qs = OrgAccount.objects.all()
#     for orgObj in orgs_qs:
#         ad_hoc_cost_reconcile_for_org(orgObj)
#     LOG.info(f"Finished -- force_ad_hoc_cost_reconcile {self.request.id}")

def cost_accounting_org(orgAccountObj):
    try:
        qs = NodeGroup.objects.filter(org=orgAccountObj)
        for c in qs:
            cost_accounting_cluster(c)
            #sum all clusters for org
            orgAccountObj.sum_asg.num += c.cur_asg.num
            orgAccountObj.sum_asg.min += c.cur_asg.min
            orgAccountObj.sum_asg.max += c.cur_asg.max

        

    except Exception as e:
        LOG.exception("Error in cost_accounting: %s", repr(e))

def cost_accounting_cluster(nodeGroupObj):
    try:
        budgetObj = nodeGroupObj.budget
        if update_ccr(budgetObj):
            update_burn_rates(budgetObj) # auto scaling changes num_nodes
            create_all_forecasts(budgetObj)
    except Exception as e:
        LOG.exception("Error in cost_accounting: %s", repr(e))

def find_broke_clusters():
    qs = NodeGroup.objects.all()
    LOG.info("orgs_qs:%s", repr(qs))
    broke_clusters = []
    for c in qs:
        if is_cluster_broke(c):
            broke_clusters.append(c)
    return broke_clusters

def get_cli_html(cli):
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

def getConsoleHtml(nodeGroupObj, rrsp):
    console_html = ''
    try:
        console_html = get_cli_html(rrsp.cli)
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
            done=True, name=nodeGroupObj.org.name, cli=failed_cli)
        return 500, ps_server_pb2.PS_AjaxResponseData(rsp=rrsp, console_html=console_html, web_error=True, web_error_msg='caught exception in web server')

def remove_num_node_requests(user,nodeGroupObj,only_owned_by_user=None):
    try:
        only_owned_by_user = only_owned_by_user or False
        LOG.info(f"{user.username} cleaning up ClusterNumNode for {nodeGroupObj.org.name} {f'owned by:{user.username}' if only_owned_by_user else ''} onn_cnt:{ClusterNumNode.objects.count()}")
        if only_owned_by_user:
            cnns = ClusterNumNode.objects.filter(cluster=nodeGroupObj,user=user)
        else:
            cnns = ClusterNumNode.objects.filter(cluster=nodeGroupObj)
        for cnn in cnns:
            LOG.info(f"{cnn.user.username} deleting ClusterNumNode {cnn.org.name}")
            if nodeGroupObj.cnnro_ids is not None:
                if str(cnn.id) in nodeGroupObj.cnnro_ids:
                    LOG.info(f"Skipping active ClusterNumNode.id:{cnn.id}")
                else:
                    cnn.delete()
            else:
                cnn.delete()
        jrsp = {'status': "SUCCESS","msg":f"{user.username} cleaned all PENDING org node reqs for {nodeGroupObj} "}
        LOG.info(f"{user.username} cleaned up ClusterNumNode for {nodeGroupObj} {'owned by:{user.username}' if only_owned_by_user else ''} onn_cnt:{ClusterNumNode.objects.count()}")
        return jrsp
    except Exception as e:
        LOG.exception("caught exception:")
        jrsp = {'status': "FAILED","error_msg":f"Server Error; Request by {user.username} to clean ALL org node reqs for {nodeGroupObj} FAILED"}

def init_new_org_memberships(orgAccountObj):
    # automatically make owner an active member
    m = Membership()
    m.org = orgAccountObj
    m.user = orgAccountObj.owner
    m.active = True
    m.save()
    msg = f"{m.user.first_name} {m.user.last_name} ({m.user.username}) now owns new org/cluster:{m.org.name}"
    LOG.info(f"{msg}")
    users = get_user_model().objects.all()
    for user in users:
        if(user.groups.filter(name='PS_Developer').exists() and not user.is_superuser and not (user == orgAccountObj.owner)):
            m = Membership()
            m.org = orgAccountObj
            m.user = user
            m.active = True
            m.save()
            LOG.info(f" Developer {m.user.first_name} {m.user.last_name} ({m.user.username}) is now a member of {m.org.name}")
    return msg

def ps_cmd_cleanup(nodeGroupObj,st,org_cmd_str):
    nodeGroupObj.active_ps_cmd = ''  # ALWAYS clear this!
    nodeGroupObj.save(update_fields=['active_ps_cmd'])
    update_cur_num_nodes(nodeGroupObj)
    time_to_process = datetime.now(timezone.utc) - st
    time_to_process = time_to_process - timedelta(microseconds=time_to_process.microseconds)
    LOG.info(f"DONE {org_cmd_str} has completed in {str(time_to_process)}")

def process_rsp_generator(nodeGroupObj, ps_cmd, rsp_gen, psCmdResultObj, org_cmd_str, deploy_values=None, expire_time=None):
    '''
    This function processes the response generator from the ps-server
        for Update, Refresh and Destroy commands. They all send the same response stream
    '''
    LOG.info(f"process_rsp_generator {org_cmd_str} {deploy_values if deploy_values is not None else ''} {expire_time.strftime(FMT) if expire_time is not None else ''}")
    nodeGroupObj.active_ps_cmd = ps_cmd
    nodeGroupObj.save(update_fields=['active_ps_cmd'])
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
                    rsp_status, console_html = getConsoleHtml(nodeGroupObj, rrsp)
                    psCmdResultObj.ps_cmd_output += console_html
                    psCmdResultObj.save()
                    if rrsp.state.valid:
                        LOG.info(f"{org_cmd_str} iter:<{iterations}> got valid state in rsp with state:{rrsp.state} using deploy_values:{deploy_values}")
                        if deploy_values:
                            update_cur_num_nodes(nodeGroupObj) # this updates the cur_asg.num
                            nodeGroupObj.cur_asg.min= deploy_values['min_node_cap']
                            nodeGroupObj.cur_asg.max = deploy_values['max_node_cap']
                            nodeGroupObj.cur_version = deploy_values['version']
                            
                            nodeGroupObj.is_public = deploy_values['is_public']
                            nodeGroupObj.expire_time = expire_time
                            # the cfg_asg.num is the desired num nodes and is only set upon a successful completion of an Update command
                            nodeGroupObj.cfg_asg.num = int(deploy_values['desired_num_nodes'])
                            nodeGroupObj.save(update_fields=['cur_asg','cur_version','is_public','expire_time'])
                        if ps_cmd == 'Destroy': # must set to zero so future desired node requests will always be differnt than current to trigger deploy
                            nodeGroupObj.cfg_asg.num = 0
                            nodeGroupObj.save(update_fields=['asg'])
                        nodeGroupObj.deployed_state = rrsp.state.deployed_state
                        nodeGroupObj.is_deployed = rrsp.state.deployed
                        nodeGroupObj.mgr_ip_address = rrsp.state.ip_address.replace('"', '')
                        if nodeGroupObj.mgr_ip_address == '':
                            nodeGroupObj.mgr_ip_address = '0.0.0.0'
                        if not nodeGroupObj.is_deployed:
                            nodeGroupObj.cur_version = ''
                        nodeGroupObj.save(update_fields=['deployed_state','is_deployed','cur_version','mgr_ip_address'])
                        msg = f" Saving state of {nodeGroupObj.org.name} cluster -> is_deployed:{nodeGroupObj.is_deployed} deployed_state:{nodeGroupObj.deployed_state} cur_version:{nodeGroupObj.cur_version} mgr_ip_address:{nodeGroupObj.mgr_ip_address}"
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
                    nodeGroupObj.num_ps_cmd_successful += 1
                    nodeGroupObj.save(update_fields=['num_ps_cmd_successful'])
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

def get_psCmdResultObj(nodeGroupObj, ps_cmd, version=None, username=None, is_adhoc=False):
    psCmdResultObj = PsCmdResult.objects.create(cluster=nodeGroupObj)
    psCmdResultObj.error = 'still processing'
    psCmdResultObj.ps_cmd_output = ''
    if is_adhoc:
        psCmdResultObj.ps_cmd_summary_label = f" --- {ps_cmd} {nodeGroupObj.org.name} Ad-Hoc "
    else:
        if ps_cmd == 'SetUp':
            psCmdResultObj.ps_cmd_summary_label = f" --- Configure {nodeGroupObj.org.name} "
        else:
            psCmdResultObj.ps_cmd_summary_label = f" --- {ps_cmd} {nodeGroupObj.org.name} "
    if version is not None:
        psCmdResultObj.ps_cmd_summary_label += f" with version {nodeGroupObj.version}"
    psCmdResultObj.save()
    if ps_cmd == 'SetUp':
        nodeGroupObj.num_setup_cmd += 1
        nodeGroupObj.save(update_fields=['num_setup_cmd'])
    nodeGroupObj.num_ps_cmd += 1
    nodeGroupObj.save(update_fields=['num_ps_cmd'])
    if username is not None:
        try:
            get_user_model().objects.get(username=username)
        except (get_user_model().DoesNotExist):
            raise UnknownUserError(f" username:{username} does not exist")

    # add username to label being displayed 
    if username is not None:
        psCmdResultObj.ps_cmd_summary_label += f" {username}"
        psCmdResultObj.save()

    org_cmd_str = f"{nodeGroupObj.org.name} cmd-{nodeGroupObj.num_ps_cmd}: {ps_cmd} {username if username is not None else ''}"
    return psCmdResultObj,org_cmd_str

def process_Update_cmd(nodeGroupObj, username, deploy_values, expire_time):
    global MIN_HRS_TO_LIVE_TO_START
    st = datetime.now(timezone.utc)
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(nodeGroupObj=nodeGroupObj,ps_cmd='Update')
    LOG.info(f"STARTED {org_cmd_str} org.dnn:{nodeGroupObj.cfg_asg.num} {deploy_values if deploy_values is not None else 'no deploy values'} {expire_time.strftime(FMT) if expire_time is not None else 'no expire tm'} ")
    try:
        try:
            LOG.info(f"Update {nodeGroupObj.org.name}")
            psCmdResultObj.expiration = expire_time
            if psCmdResultObj.expiration is None or psCmdResultObj.expiration > datetime.now(timezone.utc):
                cost_accounting(nodeGroupObj) ## update DDT to check for broke orgs
                LOG.info(f"Update {nodeGroupObj.org.name} test times min_ddt:{nodeGroupObj.min_ddt} max_ddt:{nodeGroupObj.max_ddt} now:{datetime.now(timezone.utc)} MIN_HRS_TO_LIVE_TO_START:{timedelta(hours=MIN_HRS_TO_LIVE_TO_START)}")
                if (nodeGroupObj.max_ddt - datetime.now(timezone.utc)) < timedelta(hours=MIN_HRS_TO_LIVE_TO_START):
                    emsg = f"cluster:{nodeGroupObj.org.name} Raise LowBalanceError ddt:{nodeGroupObj.max_ddt.strftime(FMT)}"
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
                rsp_gen = stub.Update(
                    ps_server_pb2.UpdateRequest(
                        name    = nodeGroupObj.org.name,
                        min_nodes   = int(deploy_values['min_node_cap']),
                        max_nodes   = int(deploy_values['max_node_cap']),
                        num_nodes   = int(deploy_values['desired_num_nodes']),
                        now=datetime.now(timezone.utc).strftime(FMT)),
                        timeout=timeout)
                process_rsp_generator(  nodeGroupObj=nodeGroupObj,
                                        ps_cmd='Update', 
                                        rsp_gen=rsp_gen, 
                                        psCmdResultObj=psCmdResultObj, 
                                        org_cmd_str=org_cmd_str, 
                                        deploy_values=deploy_values, 
                                        expire_time=expire_time)
        except LowBalanceError as e:
            error_msg = f"{org_cmd_str} Low Balance Error: The account balance ({str(nodeGroupObj.balance)}) of this organization is too low.The auto-shutdown time is {str(nodeGroupObj.min_ddt)}  Check with the support team for assistance. Can NOT deploy with less than 8 hrs left until automatic shutdown"
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
            psCmdResultObj.ps_cmd_summary_label = f" --- Update {nodeGroupObj.org.name}"
            psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            raise ProvisionCmdError(f"An error occurred during command processing. {error_msg}")
    finally:
        long_str=f"{org_cmd_str} org.dnn:{nodeGroupObj.cfg_asg.num} {deploy_values if deploy_values is not None else 'no deploy values'} {expire_time.strftime(FMT) if expire_time is not None else 'no expire tm'} "
        ps_cmd_cleanup(nodeGroupObj,st,long_str)
        for handler in LOG.handlers:
            handler.flush()

def process_Refresh_cmd(nodeGroupObj, username=None, owner_ps_cmd=None):
    global MIN_HRS_TO_LIVE_TO_START
    st = datetime.now(timezone.utc)
    is_ad_hoc = owner_ps_cmd is not None
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(nodeGroupObj=nodeGroupObj,ps_cmd='Refresh', username=username, is_adhoc=is_ad_hoc)
    LOG.info(f"STARTED {org_cmd_str}")
    try:
        # need to update
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
            LOG.info(f"gRpc: Refresh {nodeGroupObj.org.name} timeout:{timeout}")
            rsp_gen = stub.Refresh(
                ps_server_pb2.RefreshRequest(
                    name=nodeGroupObj.org.name,
                    now=datetime.now(timezone.utc).strftime(FMT)),
                    timeout=timeout)
            process_rsp_generator(nodeGroupObj=nodeGroupObj, 
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
            psCmdResultObj.ps_cmd_summary_label = f" --- 'Refresh' {nodeGroupObj.org.name}"
            if username is not None:
                psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            raise ProvisionCmdError(f"An error occurred during command processing. {error_msg}")
    finally:
        if owner_ps_cmd is not None:
            OwnerPSCmd.objects.get(id=owner_ps_cmd.id).delete()
        ps_cmd_cleanup(nodeGroupObj,st,org_cmd_str)
        for handler in LOG.handlers:
            handler.flush()

def process_Destroy_cmd(nodeGroupObj, username=None, owner_ps_cmd=None):
    global MIN_HRS_TO_LIVE_TO_START
    st = datetime.now(timezone.utc)
    is_ad_hoc = owner_ps_cmd is not None
    psCmdResultObj,org_cmd_str = get_psCmdResultObj(nodeGroupObj=nodeGroupObj,ps_cmd='Destroy', username=username, is_adhoc=is_ad_hoc)
    LOG.info(f"STARTED {org_cmd_str}")
    try:
        with ps_client.create_client_channel("control") as channel:
            stub = ps_server_pb2_grpc.ControlStub(channel)
            timeout= int(os.environ.get("GRPC_TIMEOUT_SECS",900))
            LOG.info(f"gRpc: 'Destroy {nodeGroupObj.org.name} timeout:{timeout}")
            rsp_gen = stub.Destroy(
                ps_server_pb2.DestroyRequest(
                    name=nodeGroupObj.org.name,
                    now=datetime.now(timezone.utc).strftime(FMT)),
                    timeout=timeout)
            process_rsp_generator(  nodeGroupObj=nodeGroupObj,
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
            psCmdResultObj.ps_cmd_summary_label = f" --- Destroy {nodeGroupObj.org.name}"
            if username is not None:
                psCmdResultObj.ps_cmd_summary_label += f" {username}"
            psCmdResultObj.save()
            raise ProvisionCmdError(f"An error occurred during command processing. {error_msg}")
    finally:
        psCmdResultObj.save()
        if owner_ps_cmd is not None:
            OwnerPSCmd.objects.get(id=owner_ps_cmd.id).delete()
        ps_cmd_cleanup(nodeGroupObj,st,org_cmd_str)
        for handler in LOG.handlers:
            handler.flush()


def process_owner_ps_cmd(nodeGroupObj,owner_ps_cmd):
    try:
        # This is a synchronous blocking call
        if owner_ps_cmd.ps_cmd == "Refresh":
            process_Refresh_cmd(nodeGroupObj=nodeGroupObj,
                                username=owner_ps_cmd.user.username,
                                owner_ps_cmd=owner_ps_cmd)
        elif owner_ps_cmd.ps_cmd == "Destroy":
            process_Destroy_cmd(nodeGroupObj=nodeGroupObj,
                                username=owner_ps_cmd.user.username,
                                owner_ps_cmd=owner_ps_cmd)
        else:
            LOG.error(f"ERROR: process_owner_ps_cmd: unexpected ps_cmd:{owner_ps_cmd.ps_cmd}")
        LOG.info(f"DONE processing :{owner_ps_cmd.ps_cmd} {owner_ps_cmd.org} for {owner_ps_cmd.user.username} with {owner_ps_cmd.deploy_values} num_owner_ps_cmd:{nodeGroupObj.num_owner_ps_cmd} id:{nodeGroupObj.id}")
    except Exception as e:
        LOG.exception(f"ERROR processing OwnerPSCmd id:{owner_ps_cmd.id} {owner_ps_cmd.ps_cmd} {nodeGroupObj.org.name} {owner_ps_cmd.user.username} {owner_ps_cmd.deploy_values} Exception:")
        LOG.info(f"sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
        sleep(COOLOFF_SECS)
    try:
        # if it still exists (because of an unhandled exception?). delete it
        OwnerPSCmd.objects.get(id=owner_ps_cmd.id).delete()
        LOG.info(f"Deleted :{owner_ps_cmd.id}") 
    except OwnerPSCmd.DoesNotExist:
        pass # normally it's deleted inside process_provision_cmd
    except Exception as e:
        LOG.exception(f"ERROR deleting processed OwnerPSCmd id:{owner_ps_cmd.id} {owner_ps_cmd.ps_cmd} {nodeGroupObj.org.name} {owner_ps_cmd.user.username} {owner_ps_cmd.deploy_values} Exception:")
        nodeGroupObj.provisioning_suspended = True
        nodeGroupObj.save(update_fields=['provisioning_suspended'])

def process_owner_ps_cmds_table(nodeGroupObj):
    '''
    This function is called when a privileged user issues an ad-hoc Refresh or Delete command
    '''
    env_ready,setup_occurred = check_provision_env_ready(nodeGroupObj)
    num_cmds_processed = 0
    if env_ready:
        qs = OwnerPSCmd.objects.filter(cluster=nodeGroupObj)
        #LOG.info(f"Enter with qs.count():{qs.count()}")
        for owner_ps_cmd in qs:
            process_owner_ps_cmd(nodeGroupObj,owner_ps_cmd)
            num_cmds_processed += 1
            nodeGroupObj.num_owner_ps_cmd = nodeGroupObj.num_owner_ps_cmd + 1
            nodeGroupObj.save(update_fields=['num_owner_ps_cmd'])
    qs = OwnerPSCmd.objects.filter(cluster=nodeGroupObj)
    #LOG.info(f"Exit with qs.count():{qs.count()}")
    return (qs.count()>0),setup_occurred,num_cmds_processed

def  process_prov_sys_tbls(nodeGroupObj):
    '''
    This will empty the owner ps cmds table (OwnerPSCmd)
    then process the next org num node request 
    from the ClusterNumNodes table if there is one ready
    Refresh and Destroy are the commands that are processed
    from this table
    '''
    try:
        start_cmd_cnt = nodeGroupObj.num_ps_cmd
        #LOG.info(f"org:{nodeGroupObj.org.name} num_ps_cmd:{num_ps_cmd} num_onn:{num_onn}")
        start_time = time.time() 
        # during deployments multiple versions of ps-web are running
        with advisory_lock(nodeGroupObj.org.name) as acquired:
            end_time = time.time()  # record the ending time
            wait_time = end_time - start_time  # calculate the waiting time
            if wait_time > 3:  # only valid 
                LOG.warning(f'Waited {wait_time} seconds to acquire lock for {nodeGroupObj.org.name}')
            has_more_ps_cmds = True
            setup_occurred = False
            num_cmds_processed = 0
            while has_more_ps_cmds:
                has_more_ps_cmds,setup_occurred_this_time,num_cmds_processed_this_time = process_owner_ps_cmds_table(nodeGroupObj)
                setup_occurred = setup_occurred or setup_occurred_this_time
                num_cmds_processed += num_cmds_processed_this_time
            # check if at least one API called and/or cnn expired and is not processed yet
            #LOG.info(f"nodeGroupObj:{nodeGroupObj.org.name} {NodeGroup.objects.count()} {nodeGroupObj.org.id}")
            process_num_node_table(nodeGroupObj,(num_cmds_processed==0 and setup_occurred))
    except Exception as e:
        LOG.exception(f'Exception caught for {nodeGroupObj.org.name}')
        LOG.info(f"sleeping... {COOLOFF_SECS} seconds give terraform time to clean up")
        sleep(COOLOFF_SECS)
    return (nodeGroupObj.num_ps_cmd == start_cmd_cnt) # task is idle if no new commands were processed 

def loop_iter(nodeGroupObj,loop_count):
    '''
    This is called from the main loop.
    The process_prov_sys_tbls function can block when processing ps_cmds
    so the timing of ~2hz is only when it is idle.
    '''
    #LOG.info(f"BEFORE {'{:>10}'.format(loop_count)} {nodeGroupObj.org.name} ps:{nodeGroupObj.num_ps_cmd} ops:{nodeGroupObj.num_owner_ps_cmd} cnn:{nodeGroupObj.num_onn}")
    nodeGroupObj.refresh_from_db()
    is_idle = process_prov_sys_tbls(nodeGroupObj)
    if is_idle:
        sleep(0.5) # ~2hz when idle
    #
    # The complexity below is to lower the rate of DB transactions 
    # but keeps relevant info for diagnostics
    #
    if ((loop_count % 20) == 0): # about once every ten seconds OR 2 times a second * 10 seconds
        nodeGroupObj.loop_count = loop_count
        nodeGroupObj.save(update_fields=['loop_count'])
    if ((loop_count % 7200) == 0): # about once every hour OR 2 times a second * 3600 seconds in an hour = 18000
        LOG.info(f"{nodeGroupObj.org.name} loop_count:{loop_count} nodeGroupObj.loop_count:{nodeGroupObj.loop_count} ps:{nodeGroupObj.num_ps_cmd} ops:{nodeGroupObj.num_owner_ps_cmd} cnn:{nodeGroupObj.num_onn}")
    loop_count=loop_count+1
    #LOG.info(f"AFTER  {'{:>10}'.format(loop_count)} {nodeGroupObj.org.name} ps:{nodeGroupObj.num_ps_cmd} ops:{nodeGroupObj.num_owner_ps_cmd} cnn:{nodeGroupObj.num_onn}")
    return is_idle,loop_count

def purge_old_PsCmdResultsForOrg(this_org):
    purge_time = datetime.now(timezone.utc)-timedelta(days=this_org.pcqr_retention_age_in_days)
    LOG.info(f"started with {PsCmdResult.objects.filter(org=this_org).count()} for {this_org.name} {purge_time}")
    PsCmdResult.objects.filter(expiration__lte=(purge_time)).filter(org=this_org).delete()    
    LOG.info(f"ended with {PsCmdResult.objects.filter(org=this_org).count()} for {this_org.name}")

@shared_task(name="purge_ps_cmd_rslts",bind=True)  # name is referenced in settings.py
def purge_ps_cmd_rslts(self):
    LOG.info(f"Started -- purge_ps_cmd_rslts {self.request.id}")
    orgs_qs = OrgAccount.objects.all()
    LOG.info("orgs_qs:%s", repr(orgs_qs))
    for orgObj in orgs_qs:
        purge_old_PsCmdResultsForOrg(orgObj)
    LOG.info(f"Finished -- purge_ps_cmd_rslts {self.request.id}")

@shared_task(name="hourly_processing",bind=True)  # name is referenced in settings.py
def hourly_processing(self):
    LOG.info(f"hourly_processing {self.request.id} started")
    try:
        perform_cost_accounting_for_all_clusters() # updates forecasts
        reconcile_all_orgs() # computes balance and FYTD cost
        # Now find all Orgs that ran out of funds (i.e. the are Broke)
        for nodeGroupObj in find_broke_clusters():
            owner_ps_cmd = OwnerPSCmd.objects.create(user=nodeGroupObj.org.owner, cluster=nodeGroupObj, ps_cmd='Destroy', create_time=datetime.now(timezone.utc))
            owner_ps_cmd.save()
            LOG.info(f"Destroy {nodeGroupObj.org.name} queued for processing because it ran out of funds")
        LOG.info(f"hourly_processing {self.request.id} finished")
        return True
    except Exception as e:
        LOG.exception('Exception caught')
        return False

@shared_task(name="flush_expired_refresh_tokens",bind=True)  # name is referenced in settings.py
def refresh_token_maintenance(self):
    LOG.info(f"flush_expired_refresh_tokens {self.request.id} started")
    try:
        flush_expired_refresh_tokens()
        LOG.info(f"flush_expired_refresh_tokens {self.request.id} finished")
        return True
    except Exception as e:
        LOG.exception('Exception caught')
        return False

@shared_task(name="forever_loop_main_task",bind=True) 
def forever_loop_main_task(self,name,loop_count):
    '''
    This is the main loop for each org. 
    '''
    nodeGroupObj = OrgAccount.objects.get(name=name)
    result = True
    task_idle = True
    try:
        LOG.info(f"forever_loop_main_task {self.request.id} STARTED for {nodeGroupObj.org.name}")
        while task_idle and redis_interface.server_is_up() and not get_PROVISIONING_DISABLED():
            task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
    except Exception as e:
        LOG.exception(f'forever_loop_main_task - Exception caught while processing:{nodeGroupObj.org.name}')
        sleep(5) # wait 5 seconds before trying again
        result = False
    #run again if  we have a redis connection and not disabled
    if redis_interface is not None and redis_interface.server_is_up():
        if get_PROVISIONING_DISABLED() == False: 
            sleep(1)
            LOG.info(f" forever_loop_main_task {self.request.id} RE-STARTED for {nodeGroupObj.org.name} after Exception or PS_CMD because get_PROVISIONING_DISABLED is False!")
            forever_loop_main_task.apply_async((nodeGroupObj.name,nodeGroupObj.loop_count),queue=get_node_group_queue_name(nodeGroupObj))
        else:
            LOG.critical(f"forever_loop_main_task NOT RE-STARTED for {nodeGroupObj.org.name} because PROVISIONING_DISABLED is True!")
    else:
        LOG.critical(f"forever_loop_main_task NOT RE-STARTED for {nodeGroupObj.org.name} because we cannot connect to redis!")
    LOG.info(f"forever_loop_main_task {self.request.id} FINISHED for {nodeGroupObj.org.name} with result:{result} @ loop_count:{loop_count}")
    return result
