# BSD 3-Clause License
#
# Copyright (c) 2022, University of Washington
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Provisioning System server """
import argparse
from concurrent import futures
import contextlib
import logging
import unicodedata
import calendar

import grpc
import ps_server_pb2
import ps_server_pb2_grpc
import sys
import boto3
from statistics import mean, fmean, stdev
from collections import OrderedDict
from botocore.exceptions import ClientError
from botocore.exceptions import NoCredentialsError

import pytz
from datetime import datetime, timezone, timedelta
import re
import stat

import json
import pprint
import os
import subprocess
import time
import requests

# import re
# import io
from time import sleep
from inspect import currentframe, getframeinfo
from collections import defaultdict
from google.protobuf.json_format import MessageToDict
from requests.exceptions import HTTPError
from pathlib import Path
from google.protobuf.json_format import Parse
from google.protobuf.json_format import MessageToJson
from google.protobuf.text_format import MessageToString

MAX_NUM_NODES   = 1000  # TBD what should this absolute max be?
FULL_FMT = "%Y-%m-%dT%H:%M:%SZ"
DAY_FMT = "%Y-%m-%d"

SETUP_JSON_FILE = 'SetUp.json'
S3_BUCKET = os.environ.get("S3_BUCKET",'sliderule')

# logging.basicConfig(filename='/home/logs/ps-server.log',
#     format='[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d:%(funcName)s] [%(message)s]',
#     datefmt='%Y-%m-%d:%H:%M:%S',
#     level=logging.INFO)



formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d:%(funcName)s] [%(message)s]",
    datefmt="%Y-%m-%d:%H:%M:%S",
)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)


LOG = logging.getLogger("ps_logger")
LOG.setLevel(logging.INFO)

LOG.addHandler(console_handler)


_LISTEN_ADDRESS_TEMPLATE = "[::]:%d"
_SIGNATURE_HEADER_KEY = "x-signature"

supported_cmds = ["Refresh", "Update", "Destroy"]

class ProvSys_Exception(Exception):
    """Base class for other Provisioning System Exceptions"""
    pass

class PS_InternalError(ProvSys_Exception):
    """Raised when an internal server error occurs"""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

def _load_credential_from_file(filepath):
    real_path = os.path.join(os.path.dirname(__file__), filepath)
    with open(real_path, "rb") as f:
        return f.read()

def get_domain_env():
    return os.environ.get("DOMAIN")

def get_terraform_cli():
    return os.environ.get("TERRAFORM_CLI", "terraform")

def get_root_dir():
    return '/ps_server'

def get_cluster_root_dir(name,cluster_name):
    return os.path.join(get_root_dir(),name,cluster_name)

def get_terraform_dir(name,cluster_name):
    return os.path.join(get_cluster_root_dir(name,cluster_name),"terraform")

def get_chdir_parm(name,cluster_name):
    return f"-chdir={get_terraform_dir(name,cluster_name)}"

def get_workspace_list(name,cluster_name):
    workspaces = []
    cmd_args = [get_terraform_cli(), get_chdir_parm(name,cluster_name), "workspace", "list"]
    with subprocess.Popen(
        cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=0
    ) as proc:
        try:
            while proc.poll() is None:
                for line in proc.stdout:
                    # LOG.info(line)
                    # the currently selected workspace name is preceded by '* '
                    ws = line.lstrip("* ")
                    workspaces.append(ws.strip())

        except subprocess.TimeoutExpired as e:
            emsg = "Request for cmd  " + repr(cmd_args) + "  for " + name +"-"+cluster_name+ " FAILED! "
            LOG.exception(emsg)
            proc.kill()
    # NOTE: there should always be ['default']
    #       so [] idicates an error occurred
    return workspaces

def skip_this_file(rel_path):
    return ('/.terraform/' in rel_path or 'terraform.tfstate.d' in rel_path or '.terraform.lock.hcl' in rel_path)

def upload_folder_to_s3(s3_client, bucket_name, local_directory, s3_folder):
    """
    Uploads the contents of a local directory to an S3 bucket
    Args:
        bucket_name: the name of the s3 bucket
        local_directory: a relative or absolute directory path in the local file system
        s3_folder: the folder path in the s3 bucket
    """
    LOG.info(f"Uploading {local_directory} to bucket {bucket_name} in folder {s3_folder}")
    uploaded = False
    try:
        cnt = 0
        for root, dirs, files in os.walk(local_directory):
            for file in files:
                local_file = os.path.join(root, file)
                relative_path = os.path.relpath(local_file, local_directory)
                s3_file = os.path.join(s3_folder, relative_path)
                try:
                    if skip_this_file(relative_path):
                        LOG.info(f"Skipping {relative_path} ")
                    else:
                        s3_client.upload_file(local_file, bucket_name, s3_file)
                        cnt += 1
                        uploaded = True
                        LOG.info(f"Uploaded {local_file} to {s3_file} in bucket {bucket_name}")
                except NoCredentialsError:
                    LOG.error("No AWS credentials found")
                    uploaded = False
    except Exception as e:
        LOG.exception(f"FAILED to upload {local_directory} (at cnt:{cnt}) to bucket {bucket_name} in folder {s3_folder}")
        uploaded = False
    if uploaded:
        LOG.info(f"Finished uploading {cnt} files from {local_directory} to bucket {bucket_name} in folder {s3_folder}" ) 
    else:
        LOG.error(f"FAILED to upload {local_directory} to bucket {bucket_name} in folder {s3_folder} cnt:{cnt}")
    return uploaded

def delete_folder_from_s3(s3_client, bucket, s3_folder):
    """
    Delete the contents of a folder from an S3 bucket
    Args:
        s3_client: S3 client to interact with AWS
        bucket_name: the name of the s3 bucket
        s3_folder: the folder path in the s3 bucket
    """
    LOG.info(f"Deleting s3_folder:{s3_folder} in bucket:{bucket} ")
    deleted = False
    try:
        # Add trailing slash if not present
        if not s3_folder.endswith('/'):
            s3_folder += '/'

        # List all objects under the given S3 folder
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=s3_folder
        )

        # If there are no objects under the given S3 folder
        if response['KeyCount'] == 0:
            return False

        # Construct list of keys to delete
        objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]

        # Delete the objects
        s3_client.delete_objects(
            Bucket=bucket,
            Delete={'Objects': objects_to_delete}
        )
        deleted = True
    except Exception as e:
        LOG.exception(f"FAILED to delete {s3_folder} from to bucket {bucket}")
    return deleted

def bucket_exists(s3_client,bucket_name):
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        return True
    except ClientError:
        # if it doesn't exist, ClientError is raised
        return False
    except NoCredentialsError:
        LOG.exception(f"No AWS credentials found when checking if bucket:{bucket_name} exists")
        return False
    except Exception as e:
        LOG.exception(f"FAILED to check if bucket {bucket_name} exists")
        return False

def s3_folder_exists(s3_client, bucket_name, s3_folder):
    """
    Check if a folder exists in an S3 bucket
    Args:
        bucket_name: the name of the s3 bucket
        s3_folder: the folder path in the s3 bucket
    Returns:
        boolean: True if folder exists, False otherwise
    """

    if bucket_exists(s3_client,bucket_name):    
        # Ensure the folder path ends with '/'
        if not s3_folder.endswith('/'):
            s3_folder += '/'
        
        result = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=s3_folder)
        ret = 'Contents' in result
        if ret:
            # Use pprint.pformat to get a formatted string
            contents_str = pprint.pformat(result['Contents'])
            #logger.info(f'Bucket:{bucket_name} contains folder:{s3_folder} with contents:\n{contents_str}')
            LOG.info(f'Bucket:{bucket_name} contains folder:{s3_folder}')
        else:
            LOG.info(f'Bucket:{bucket_name} DOES NOT contain folder:{s3_folder}')
        return ret
    else:
        LOG.info(f'Bucket:{bucket_name} DOES NOT exist')
        return False

def upload_current_tf_files_to_s3(s3_client,name,cluster_name):
    '''
    Uploads the currently used terraform files of an org to s3
    This uploads the entire org dir with subdir of terraform and it's files
    The root path dir is the org name then cluster_name and that contains the terraform subdir and the file with  setup info, i.e. SETUP_JSON_FILE 
    '''
    s3_folder = os.path.join('prov-sys',f'{get_domain_env()}','current_cluster_tf_by_org',name)
    if s3_folder_exists(s3_client=s3_client,bucket_name=S3_BUCKET,s3_folder=s3_folder):
        LOG.info(f"deleting s3_folder:{s3_folder} from s3 for name:{name} cluster_name:{cluster_name}")
        if not delete_folder_from_s3(s3_client=s3_client,
                                    bucket=S3_BUCKET,
                                    s3_folder=s3_folder):
            LOG.error(f"Failed to remove s3_folder:{s3_folder} from s3 for name:{name} cluster_name:{cluster_name}")
    LOG.info(f"uploading tf files for name:{name} cluster_name:{cluster_name}")
    return upload_folder_to_s3(s3_client=s3_client,
                                bucket_name=S3_BUCKET, 
                                local_directory=get_cluster_root_dir(name,cluster_name), 
                                s3_folder=s3_folder)

def download_s3_folder(s3_client, bucket_name, s3_folder, local_dir=None):
    """
    Download the contents of a folder directory
    Args:
        bucket_name: the name of the s3 bucket
        s3_folder: the folder path in the s3 bucket
        local_dir: a relative or absolute directory path in the local file system
    """
    LOG.info(f"download_s3_folder: bucket_name:{bucket_name} s3_folder:{s3_folder} local_dir:{local_dir}")

    paginator = s3_client.get_paginator('list_objects_v2')

    prefix = s3_folder
    if not prefix.endswith('/'):
        prefix += '/'

    downloaded = False
    count = 0
    for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
        for obj in page.get('Contents', []):
            target = obj['Key'] if local_dir is None \
                else os.path.join(local_dir, os.path.relpath(obj['Key'], s3_folder))
            dir = os.path.dirname(target)
            if not os.path.exists(dir):
                if not skip_this_file(dir):
                    os.makedirs(dir)
            if obj['Key'][-1] == '/':
                continue
            if not skip_this_file(target):
                LOG.info(f"Downloading {obj['Key']} to {target}")
                s3_client.download_file(bucket_name, obj['Key'], target)
                count += 1
                downloaded = True
            else:
                LOG.info(f"Skipping {obj['Key']} ") 
    if downloaded:
        LOG.info(f"Successfully downloaded {count} files from {s3_folder} into {local_dir}")
    else:
        LOG.error(f"FAILED to download from bucket:{bucket_name} folder:{s3_folder} into local_dir:{local_dir} count:{count}")
    return downloaded

def poll_for_localstack_status(logger):
    '''
    Checks the status of localstack. This is only called when the domain is localhost
    '''
    # wait for localstack to be ready
    start_time = time.time()
    error_encountered = False  # Flag to track if an error is encountered
    elapsed_time = 0
    logger.info("Polling --- Waiting for LocalStack to be ready ---")
    cnt = 0
    session = requests.Session()
    while cnt < 100: # can't poll forever
        response = None
        try:
            elapsed_time = time.time() - start_time
            if elapsed_time > 1:
               time.sleep(1) 
            else:
               time.sleep(0.1)
            response = session.get('http://localstack:4566/_localstack/init')
            logger.debug(f"response:{response}")
            response.raise_for_status()  # will raise an exception if the status is not 200
            data = response.json()
            logger.debug(f"data:{data}")
            for completed in data.get('completed', []):
                logger.debug(f"completed:{completed}")
                if data['completed']['READY'] == True:
                    logger.info("LocalStack is ready")
                    return  True # exit the loop and the fixture
        except requests.exceptions.HTTPError as e:
            logger.info(f"LocalStack not ready yet e:{e}")
        except requests.exceptions.ConnectionError as e:
            logger.info(f"LocalStack not ready yet e:{e}")
        except Exception as e:
            logger.exception(f"Exception:{e}")
            error_encountered = True
        cnt += 1

        if error_encountered:
            logger.error("Error encountered during LocalStack setup")
            logger.info(f"elapsed_time:{elapsed_time} secs cnt:{cnt}")
            break
        if elapsed_time > 15:  # timeout 
            logger.error("Timeout while waiting for LocalStack to be ready")
            logger.info(f"elapsed_time:{elapsed_time} secs cnt:{cnt}")
            break
    logger.info(f"elapsed_time:{elapsed_time} secs cnt:{cnt}")
    return False

def get_ps_versions():
    '''
        These are the pkg versions obtained from the ps_server_versions file extracted from the container
    '''
    versions = open('/ps_server/.ps_server_versions', 'r').read()
    LOG.debug(f'{versions}')
    return versions


def get_num_nodes_suffix(region, name, cluster_name, version, suffix):
    endpoint_url = os.environ.get('AWS_EC2_ENDPOINT_URL')
    #LOG.info(f"get_num_nodes_suffix: region:{region} name:{name} cluster_name:{cluster_name} version:{version} suffix:{suffix} endpoint_url:{endpoint_url}")
    if endpoint_url is None or endpoint_url == "":
        ec2_client = boto3.client('ec2', region_name=region)
    else:
        ec2_client = boto3.client('ec2', region_name=region, endpoint_url=endpoint_url)

    orgGrok = name+"-"+cluster_name
    if version != "":
        orgGrok = orgGrok
    orgGrok = orgGrok + suffix
    # LOG.info(orgGrok)

    filters= [{
        'Name':'tag:Name',
        'Values':[orgGrok]
        },
        {
        'Name': 'instance-state-name',
        'Values': ['running']
        }]

    response = ec2_client.describe_instances(Filters=filters)

    ncnt = 0
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            ncnt = ncnt + 1
            # LOG.info(f'Instances with Tag "Name={orgGrok}":')
            # LOG.info(f'EC2 instance {instance.id} information:')
            # LOG.info(f'Instance state: {instance.state["Name"]}')
            # LOG.info(f'Instance AMI: {instance.image.id}')
            # LOG.info(f'Instance platform: {instance.platform}')
            # LOG.info(f'Instance type: {instance.instance_type}')
            # LOG.info(f'Public IPv4 address: {instance.public_ip_address}')
            # LOG.info('-'*60)   
    return ncnt

def get_num_nodes(region,name,cluster_name,version):
    return get_num_nodes_suffix(region,name,cluster_name,version,"-node")

def get_sorted_tm_cost(ccr1,ccr2):
    '''
        returns a de-duplicated list of tuples sorted by cost
    '''
    # assumes ccr.tm is same len as ccr.cost
    #LOG.info(f"type(ccr1.tm):{type(ccr1.tm)} type(ccr1.cost):{type(ccr1.cost)}")
    tuple_1 = tuple(zip(ccr1.tm,ccr1.cost))
    #LOG.info(f"tuple_1:{tuple_1}")
    tuple_2 = tuple(zip(ccr2.tm,ccr2.cost))
    #LOG.info(f"tuple_2:{tuple_2}")
    unsorted_tuple = tuple_1 + tuple_2
    #LOG.info(f"sz:{len(unsorted_tuple)} unsorted_tuple:{unsorted_tuple}")
    sorted_tuple = sorted(unsorted_tuple, key = lambda x:(x[0]))
    #LOG.info(f"sz:{len(sorted_tuple)} sorted_tuple:{sorted_tuple}")
    return[(k,v) for (k,v) in OrderedDict(sorted_tuple).items()]

def merge_ccrs(ccr,ccr1,ccr2):
    sorted_tm_cost_tuple = get_sorted_tm_cost(ccr1,ccr2)
    del ccr.tm[:] 
    del ccr.cost[:] 
    for t in sorted_tm_cost_tuple:
        ccr.tm.append(t[0])
        ccr.cost.append(t[1])
    return ccr

def ce_get_cost_and_usage(ce_client,ccr,st_str,et_str,tag_key,tag_values):
    result = ce_client.get_cost_and_usage(
        TimePeriod={
            "Start": st_str,
            "End": et_str,
        },
        Filter={"Tags": {"Key": tag_key, "Values": tag_values}},
        Granularity=ccr.granularity,
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}],
    )
    LOG.info(f" $$$$$$$$$$$$$$$$$$$ cost explorer call $$$$$$$$$$$$$$ {ccr.name} {ccr.granularity} {st_str} - {et_str}")
    #LOG.info(result)
    rbt = result.get("ResultsByTime")
    for r in rbt:
        tp = r.get("TimePeriod")
        tm = tp.get("Start")
        grps = r.get("Groups")
        # assume grouped by account and only one account
        if(len(grps)>0):
            for grp in grps:
                metrics = grp.get("Metrics")
                cost = metrics.get("UnblendedCost")
                amount = float(cost.get("Amount"))
                u = cost.get("Unit")
                if u != "":
                    ccr.unit = u
                ccr.tm.append(tm)
                ccr.cost.append(amount)
                ccr.total += amount
        else:
                ccr.tm.append(tm)
                ccr.cost.append(0.0)
    if len(ccr.cost) > 0:
        ccr.stats.avg = fmean(ccr.cost)
        ccr.stats.min = min(ccr.cost)
        ccr.stats.max = max(ccr.cost)
    if len(ccr.cost) > 1:
        ccr.stats.std = stdev(ccr.cost)
    return ccr

def get_s3_client():
    endpoint_url = os.environ.get("AWS_S3_ENDPOINT_URL")
    aws_region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    LOG.info(f"endpoint_url:{endpoint_url} aws_region:{aws_region}")
    if endpoint_url is None or endpoint_url == "":
        s3_client = boto3.client("s3", region_name=aws_region)
    else:
        s3_client = boto3.client("s3", region_name=aws_region, endpoint_url=endpoint_url)
    return s3_client

def get_versions(s3_client):
    VERSIONS = []
    sorted_versions = []
    try:
        result = s3_client.list_objects(Bucket=S3_BUCKET,
                                        Prefix='prov-sys/cluster_tf_versions/',
                                        Delimiter='/'
                                        )
        #LOG.info(f"result:{pprint.pformat(result)}")
        for o in result.get('CommonPrefixes'):
            #LOG.info(o.get('Prefix'))
            path = o.get('Prefix')
            #LOG.info(path.split("/")[:-1][2])
            version = path.split("/")[:-1][2].rstrip()
            VERSIONS.append(version)
        sorted_versions = sorted(VERSIONS,reverse=True)
        #LOG.info(f"sorted_versions:{sorted_versions}")
        # Move 'latest' to the first position
        sorted_versions.remove('latest')
        sorted_versions.insert(0, 'latest')
        #LOG.info(f"lastest is first now in sorted_versions:{sorted_versions}")
    except Exception as e:
        LOG.exception(f"get_versions caught exception:{repr(e)}") 
    LOG.info(f"sorted_versions:{sorted_versions}")  
    return sorted_versions

def read_SetUpCfg(name,cluster_name):
    setup_json_file_path = os.path.join(get_cluster_root_dir(name,cluster_name),SETUP_JSON_FILE)
    setup_cfg = ps_server_pb2.SetUpReq()
    try:
        with open(setup_json_file_path, 'r') as json_file:
            json_str = json_file.read()
            # Parse the JSON string into the protobuf object
            Parse(json_str, setup_cfg)
    except FileNotFoundError:
        # Not always fatal....
        LOG.info(f"Uninitialized SetUp: FileNotFoundError reading :{setup_json_file_path}")  
    LOG.info(f"{setup_cfg} from {setup_json_file_path}")
    return setup_cfg

def write_SetUpCfg(name,cluster_name, setup_cfg):
    setup_json_file_path = os.path.join(get_cluster_root_dir(name=name,cluster_name=cluster_name),SETUP_JSON_FILE)
    json_str = MessageToJson(setup_cfg)
    with open(setup_json_file_path, 'w') as json_file:
        json_file.write(json_str)
        LOG.info(f"{MessageToString(setup_cfg)} to {setup_json_file_path} ")

def update_SetUpCfg(name,cluster_name,version,is_public,now):
    LOG.info(f"update_SetUpCfg: name:{name} cluster_name:{cluster_name} version:{version} is_public:{is_public} now:{now}")
    try:
        setup_cfg = read_SetUpCfg(name=name,cluster_name=cluster_name) # might not exist
        LOG.info(f"FROM: {setup_cfg}")
        setup_cfg.name = name
        setup_cfg.version = version
        setup_cfg.is_public = is_public
        setup_cfg.now = now
        LOG.info(f"update_SetUpCfg: {MessageToString(setup_cfg,print_unknown_fields=True)}")
        write_SetUpCfg(name, cluster_name, setup_cfg)
    except Exception as e:
        LOG.exception(f" FAILED to read to read_SetUpCfg({name},{cluster_name}) caught UNKNOWN exception:{repr(e)}")
        raise e
    LOG.info(f"  TO: {MessageToString(setup_cfg)}")
 

class Account(ps_server_pb2_grpc.AccountServicer):
    # Assumes st is on an hourly or daily boundry that lines up with cost explorer times
    def GetCostAndUsageRsp(self,name,cluster_name,gran,st,et):  ## This is a common routine
        LOG.info(f"{name} {cluster_name} {gran} {st} {et}")
        ccr = ps_server_pb2.CostAndUsageRsp(
            name=name,
            cluster_name=cluster_name,
            total=0.0,
            unit="",
            server_error=False,
            error_msg="",
        )
        try:
            delta_tm = et - st
            LOG.info(f"delta_tm:{delta_tm}=({et} - {st})")
            max_delta_tm = timedelta(days=365)  # only 365 days are  supported for daily and monthly
            if gran == "HOURLY":
                max_delta_tm = timedelta(days=14)  # only 14 days are  supported for hourly
                fmt = FULL_FMT
                tm_increment = timedelta(hours=1)
            elif gran == "DAILY":
                fmt = DAY_FMT
                tm_increment = timedelta(days=1)    
            elif gran == "MONTHLY":
                fmt = DAY_FMT
                tm_increment = 0 # each month is different
            else:
                emsg = "FAILED with unknown granularity:"+gran
                LOG.error(emsg)
                ccr.server_error = True
                ccr.error_msg = emsg
                return ccr

            ccr.granularity = gran
            ccr_using_project_tags = ccr
            ccr_using_name_tags = ccr
            legacy_name_tags = []
            project_tags = []
            endpoint_url = os.environ.get("AWS_CE_ENDPOINT_URL")
            aws_region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
            if endpoint_url is None or endpoint_url == "":
                ce_client = boto3.client("ce", region_name=aws_region)
            else:
                # we use this for testing
                ce_client = boto3.client("ce", region_name=aws_region, endpoint_url=endpoint_url)
            #for tags use entire period
            if delta_tm > max_delta_tm:
                delta_tm = max_delta_tm
            if delta_tm < timedelta(days=1,hours=1, seconds=1):
                tags_st = et - timedelta(days=1,hours=1,seconds=1)
            else:
                tags_st = et - delta_tm
            LOG.info(f"delta_tm:{delta_tm} tags_st:{tags_st.strftime(DAY_FMT)} et:{et.strftime(DAY_FMT)}")

            #
            # These are the default_tags in the provider section of the cluster terraform definition
            #
            ProjectTagValuesToUse = "cluster-"+name+"-"+cluster_name
            LOG.info(f"get_tags {ProjectTagValuesToUse} {tags_st.strftime(DAY_FMT)} {et.strftime(DAY_FMT)}")
            if endpoint_url is None or endpoint_url == "": # i.e. not localstack or local test env
                tags_result = ce_client.get_tags(  SearchString=ProjectTagValuesToUse,
                                            TimePeriod={
                                                "Start": tags_st.strftime(DAY_FMT),
                                                "End": et.strftime(DAY_FMT),
                                            },
                                            TagKey='Project')
                project_tags = tags_result.get('Tags')
                LOG.info("project tags -- rs:%s ts:%s",tags_result.get('ReturnSize'),tags_result.get('TotalSize'))
                LOG.info(f"project_tags{project_tags}")
                if tags_result.get('ReturnSize') == 0:
                    LOG.info(f"No project cost tags returned for {name} {cluster_name} {tags_st.strftime(fmt)} {et.strftime(fmt)}")
                if (tags_result.get('ReturnSize') != tags_result.get('TotalSize')):
                    LOG.error(f"Unexpected multiple pages of cost tags returned for {name}  {cluster_name} {tags_st.strftime(fmt)} {et.strftime(fmt)}")

                if len(project_tags) > 0:
                    LOG.info(f"ce_get_cost_and_usage {st.strftime(fmt)} {et.strftime(fmt)}")
                    ccr_using_project_tags = ce_get_cost_and_usage(ce_client,ccr_using_project_tags,st.strftime(fmt),et.strftime(fmt),"Project",project_tags)
                    ccr = ccr_using_project_tags
                # as of 3/6/2023 these were the legacy name tags found in clusters resources deployed by ps-server 

                # This code will eventually be deprecated and removed.
                # These below are the legacy Name tags we need to search for in order to get correct cost for the time before
                # we started using the default_tags in the provider section of the cluster terraform definition.
                # Eventually these completely overlap the project tags and this can be removed
                LOG.info("legacy_name_tags:%s,",legacy_name_tags) 
                # These Name tags will continue to exist but will be (are) redundant with Project tags 
                legacy_name_tags = [
                    name+"-ilb",                
                    name+"-lb",                
                    name+"-monitor",                
                    name+"-node",                
                    name+"-node-manager",                
                    name+"-orchestrator",                
                    name+"-proxy",                
                ]
                # These tags are obsolete and no longer exist but they were active until ~ 3/5/2023
                if name == 'sliderule':
                    sliderule_org_legacy_names = [
                        "sliderule-alpha",
                        "sliderule-asg",
                        "sliderule-asg-consul",
                        "sliderule-asg-node",
                        "sliderule-base-20210618161452",
                        "sliderule-base-image",
                        "sliderule-consul-server",
                        "sliderule-node",
                        "sliderule-node-base-image",
                        "sliderule-node-<dates>",
                        "sliderule-node-asg",
                        "sliderule-node_v1",
                        "sliderule-node_v1.0.1",
                        "sliderule-prod-beta",
                        "sliderule-prometheus",
                        "sliderule-prometheus-server",
                        "sliderule-prometheus-server_v1.0.1",
                        "sliderule-ps-base-image",
                    ]
                    legacy_name_tags += sliderule_org_legacy_names

                ccr_using_name_tags = ce_get_cost_and_usage(ce_client,ccr_using_name_tags,st.strftime(fmt),et.strftime(fmt),"Name",legacy_name_tags)
                sorted_tm_cost_tuple = get_sorted_tm_cost(ccr_using_name_tags,ccr_using_project_tags)
                del ccr.tm[:] 
                del ccr.cost[:] 
                for t in sorted_tm_cost_tuple:
                    ccr.tm.append(t[0])
                    ccr.cost.append(t[1])
            else:
                LOG.warning(f"SKIPPING because of endpoint_url:{endpoint_url} localstack or local test env? ")
        except Exception as e:
            emsg = (f" Processing for org: {name}  {cluster_name} cluster caught this exception:")
            LOG.exception(emsg)
            emsg += repr(e)
            ccr.server_error = True
            ccr.error_msg = emsg
        #LOG.info(ccr)
        return ccr


    def CurrentCost(self, currentCostReq, context):  ## This is called by GRPC framework
        #gran = ps_server_pb2.GRANULARITY.Name(currentCostReq.granularity)
        try:
            gran = currentCostReq.granularity
            LOG.info(f"{currentCostReq.name} {currentCostReq.cluster_name} {gran}")
            et = pytz.utc.localize(datetime.strptime(currentCostReq.tm, FULL_FMT)) + timedelta(hours=1) 
            et.replace(minute=0, second=0, microsecond=0) #end of this hour
            if gran == "HOURLY":
                days = 14  # only 14 days are  supported for hourly
                fmt = FULL_FMT
                st = et - timedelta(days)
                st = st.replace(minute=0, second=0, microsecond=0)
            elif gran == "DAILY":
                days = 365  # only 365 days are  supported for daily
                fmt = DAY_FMT
                st = et - timedelta(days)
                st =st.replace(hour=0, minute=0, second=0, microsecond=0)
            elif gran == "MONTHLY":
                days = 365  # only 365 days are  supported for monthly
                fmt = DAY_FMT
                st = et - timedelta(days)
                st =st.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                errMsg = " FAILED, Unknown granularity "+gran
                LOG.error("FAILED")
                return ps_server_pb2.CostAndUsageRsp(   name  = currentCostReq.name,
                                                        cluster_name = currentCostReq.cluster_name,
                                                        granularity = currentCostReq.granularity,
                                                        server_error = True,
                                                        error_msg = errMsg)
            LOG.info("%s",repr(currentCostReq))
            return self.GetCostAndUsageRsp( currentCostReq.name,
                                            currentCostReq.cluster_name,
                                            currentCostReq.granularity,
                                            st,
                                            et)
        except Exception as e:
            emsg = (f" Processing for org: {currentCostReq.name} {currentCostReq.cluster_name} cluster caught this exception:")
            LOG.exception(emsg)
            emsg += repr(e)
            return ps_server_pb2.CostAndUsageRsp(
                name=currentCostReq.name,
                cluster_name=currentCostReq.cluster_name,
                total=0.0,
                unit="",
                server_error=True,
                error_msg=emsg)            

    def TodaysCost(self, todaysCostReq, context):  ## This is called by GRPC framework
        LOG.info(f"{todaysCostReq.name} {todaysCostReq.cluster_name} cluster")
        try:
            tm = pytz.utc.localize(datetime.strptime(todaysCostReq.tm, FULL_FMT))
            # st truncate to start of the day et extrapolate to beginning of this hour
            st = tm.replace(hour=0, minute=0, second=0, microsecond=0)
            if (tm - st) < timedelta(hours=1): # must have a range
                tm = st + timedelta(hours=1)
            LOG.info(f"st:{st.strftime(FULL_FMT)} et:{tm.strftime(FULL_FMT)}")
            return self.GetCostAndUsageRsp( todaysCostReq.name,
                                            todaysCostReq.cluster_name,
                                            "HOURLY",
                                            st,
                                            tm)
        except Exception as e:
            emsg = (f" Processing for org: {todaysCostReq.name} {todaysCostReq.cluster_name} cluster caught this exception:")
            LOG.exception(emsg)
            emsg += repr(e)
            return ps_server_pb2.CostAndUsageRsp(
                name=todaysCostReq.name,
                cluster_name=todaysCostReq.cluster_name,
                total=0.0,
                unit="",
                server_error=True,
                error_msg=emsg)   

    ## This is called by GRPC framework
    def DailyHistCost(self, dailyHistCost, context):
        try:
            LOG.info(f"{dailyHistCost.name} {dailyHistCost.cluster_name} cluster")
            # truncate to start of day for time passed as st
            st = datetime.strptime(dailyHistCost.start_tm, FULL_FMT).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=pytz.utc
            )
            # truncate to start of day
            et = datetime.strptime(dailyHistCost.end_tm, FULL_FMT).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=pytz.utc
            )
            LOG.info("st:%s et:%s", st.strftime(FULL_FMT), et.strftime(FULL_FMT))
            return self.GetCostAndUsageRsp( dailyHistCost.name,
                                            dailyHistCost.cluster_name,
                                            "DAILY",
                                            st,
                                            et)
        except Exception as e:
            emsg = (f" Processing for org: {dailyHistCost.name} {dailyHistCost.cluster_name} cluster caught this exception:")
            LOG.exception(emsg)
            emsg += repr(e)
            return ps_server_pb2.CostAndUsageRsp(
                name=dailyHistCost.name,
                cluster_name=dailyHistCost.cluster_name,
                total=0.0,
                unit="",
                server_error=True,
                error_msg=emsg)            

    def NumNodes(self,numNodesReq, context):
        #LOG.info("%s %s %s",numNodesReq.region,numNodesReq.name,numNodesReq.version)
        num_nodes = get_num_nodes(numNodesReq.region,numNodesReq.name, numNodesReq.cluster_name, numNodesReq.version)
        return ps_server_pb2.NumNodesRsp(name=numNodesReq.name,cluster_name=numNodesReq.cluster_name,version=numNodesReq.version,region=numNodesReq.region,num_nodes = num_nodes)


class Control(ps_server_pb2_grpc.ControlServicer):

    def get_Response_Obj(self, name, cluster_name, ps_cmd, done=False, ps_server_error=False, error_msg=""):
        rsp = ps_server_pb2.Response(
            name=name,
            cluster_name=cluster_name,
            ps_cmd=ps_cmd,
            done=done,
            ps_server_error=ps_server_error,
            error_msg=error_msg,
        )
        return rsp

    def get_cmd_args_response(self, name, cluster_name, ps_cmd, cmd_args):
        #LOG.info("cmd_args:\n%s", repr(cmd_args))
        r = self.get_Response_Obj(name=name, cluster_name=cluster_name, ps_cmd=ps_cmd)
        r.cli.cmd_args = (
            "\n**************** cmd submitted: "
            + repr(cmd_args)
            + " at "
            + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            + "\n"
        )
        r.cli.valid = True
        r.cli.updating = True
        r.cli.stdout = ""
        r.cli.stderr = ""
        return r

    def get_stdout_response(self, name, cluster_name, ps_cmd, line):
        plain_txt = str(line, "utf-8")
        plain_txt.lstrip()
        plain_txt.rstrip()
        #LOG.info("STDOUT:\n%s", plain_txt)
        r = self.get_Response_Obj(name=name, cluster_name=cluster_name, ps_cmd=ps_cmd)
        r.cli.cmd_args = ""
        r.cli.valid = True
        r.cli.updating = True
        r.cli.stdout = line
        r.cli.stderr = ""
        return r

    def get_stderr_response(self, name, cluster_name, ps_cmd, line):
        plain_txt = str(line, "utf-8")
        plain_txt.lstrip()
        plain_txt.rstrip()
        #LOG.info("STDERR:\n%s", plain_txt)
        r = self.get_Response_Obj(name=name, cluster_name=cluster_name, ps_cmd=ps_cmd)
        r.cli.cmd_args = ""
        r.cli.valid = True
        r.cli.updating = True
        r.cli.stdout = ""
        r.cli.stderr = line
        return r

    def get_console_txt(self, io_rdr):
        console_txt = ""
        plain_txt = ""
        for line in io_rdr:
            console_txt += line
            plain_txt += str(line, "utf-8")
            plain_txt.lstrip()
            plain_txt.rstrip()
        return console_txt, plain_txt

    def get_state_response(self, name, cluster_name, ps_cmd, cmd_args, proc, deployed, deployed_state, ip_address):
        console_stderr, plain_txt_err = self.get_console_txt(proc.stderr)
        console_stdout, plain_txt_out = self.get_console_txt(proc.stdout)
        LOG.info("console output:\n%s %s", plain_txt_err, plain_txt_out)
        r = self.get_Response_Obj(name=name, cluster_name=cluster_name, ps_cmd=ps_cmd)
        r.cli.valid = False
        r.cli.updating = False
        r.cli.cmd_args = ""
        r.cli.stdout = console_stdout
        r.cli.stderr = console_stderr
        valid = proc.returncode == 0
        r.state.valid = valid
        r.state.deployed = deployed
        r.state.deployed_state = deployed_state
        r.state.ip_address = ip_address
        #LOG.info(r)
        return r

    def valid_deploy_args(self, request):
        return (
            request.min_nodes >= 0
            and request.max_nodes > 0
            and request.num_nodes >= 0
            and request.min_nodes <= MAX_NUM_NODES
            and request.max_nodes <= MAX_NUM_NODES
            and request.num_nodes <= MAX_NUM_NODES
            and request.max_nodes >= request.min_nodes
            and request.num_nodes <= request.max_nodes
            and request.num_nodes >= request.min_nodes)

    def after_tf_refresh_cmd_proc(self, name, cluster_name, ps_cmd):
        #LOG.info("Enter")
        cmd_args = ["state", "list"]
        yield from self.execute_sequence_of_terraform_cmds(name, cluster_name, ps_cmd, cmd_args)

    def poll_and_process_tf_state_list_cmd_proc(self, name, cluster_name, ps_cmd, state_list_cmd_args, state_list_cmd_proc):
        #LOG.info("Enter")
        NUM_LINES_TO_SEND = 150
        deployed = False
        deployed_state = "unknown"
        ip_address = "unknown"
        expected_str = str("data.aws_ami.sliderule_cluster_ami\n")
        outStr = ""
        yield self.get_cmd_args_response(name, cluster_name, ps_cmd, state_list_cmd_args)
        while state_list_cmd_proc.poll() is None:
            # handle std err
            yield from self.batch_std_lines(name, cluster_name, ps_cmd, state_list_cmd_proc.stderr, self.get_stderr_response)
            # don't call batch_std_lines for stdout this way we can capture plain text in outStr
            olines = "".encode()
            for line in state_list_cmd_proc.stdout:
                outStr = outStr + str(line, "utf-8").strip()
                olines = olines + line
            yield self.get_stdout_response(name, cluster_name, ps_cmd, olines)
        if state_list_cmd_proc.returncode != 0:
            emsg = (
                "terraform cmd "
                + repr(state_list_cmd_args)
                + " FAILED for ps_cmd |"
                + ps_cmd
                + "|"
            )
            LOG.error(emsg)
            raise PS_InternalError(emsg)
        #LOG.info("outStr:%s", outStr)
        if (outStr == expected_str.strip()) or (outStr == ""):
            # this is what we see when the cluster is NOT deployed. one line state with only AMI
            LOG.info("Assuming NOT deployed, state list shows empty set")
            deployed = False
            deployed_state = "NOT deployed"
            ip_address = "0.0.0.0"
            yield self.get_state_response(
                name,
                cluster_name,
                ps_cmd,
                state_list_cmd_args,
                state_list_cmd_proc,
                deployed,
                deployed_state,
                ip_address,
            )
        else:
            ## Assuming deployed now do an output cmd
            LOG.info("Assuming deployed")
            cmd_args = ["output"]
            LOG.info(f"{name} {cluster_name} out_cmd_args:{repr(cmd_args)}" )
            yield from self.execute_sequence_of_terraform_cmds(name, ps_cmd, cmd_args)

    def poll_and_process_tf_output_cmd_proc(self, name, cluster_name, ps_cmd, out_cmd_args, out_cmd_proc):
        LOG.info("Enter")
        lines = []
        olines = "".encode()
        NUM_LINES_TO_SEND = 150
        LOG.info("calling for stderr")
        yield self.get_cmd_args_response(
            name, cluster_name, ps_cmd, out_cmd_args
        )
        while out_cmd_proc.poll() is None:
            yield from self.batch_std_lines(name, cluster_name, ps_cmd, out_cmd_proc.stderr, self.get_stderr_response)
            oln_cnt = 0
            LOG.info("Reading stdout lines...")
            for line in out_cmd_proc.stdout:
                ln = str(line, "utf-8")
                lines.append(ln.replace(" ", "").split("="))
                olines = olines + line
                oln_cnt = oln_cnt + 1
            LOG.info("Sending response with %d lines",oln_cnt)
            if oln_cnt > 0:
                yield self.get_stdout_response(name, cluster_name, ps_cmd, olines)
                LOG.info(olines.decode())
        LOG.handlers[0].flush()

        if out_cmd_proc.returncode != 0:
            emsg = (
                "terraform cmd "
                + repr(out_cmd_args)
                + " FAILED for ps_cmd |"
                + ps_cmd
                + "|"
            )
            LOG.error(emsg)
            LOG.handlers[0].flush()
            raise PS_InternalError(emsg)

        t_out = {"ilb_state": "", "ilb_ip_address": "", "ilb_id": "","monitor_state": "", "monitor_id": ""}
        if (
            lines.__len__() == 5
        ):  # expected ini formatted output of ilb_state=<xxx> and ilb_ip_address = <xxx>
            t_out = dict(lines)
            LOG.info(t_out)
            deployed = True
            monitor_deployed_state = t_out.get("monitor_state").strip()[1:-1]  # strip newline then quotes
            deployed_state = monitor_deployed_state # Note: could be initializing or running
            LOG.info("monitor_deployed_state:%s",monitor_deployed_state)  # Note: could be initializing or running
            ilb_ip_address = t_out.get("ilb_ip_address").strip()[1:-1]  # strip newline then quotes
            LOG.info(ilb_ip_address)
            id = t_out.get("monitor_id").strip()[1:-1]  # strip newline then quotes
            LOG.info(id)
            ilb_deployed_state = t_out.get("ilb_state").strip()[1:-1]  # strip newline then quotes
            LOG.info("ilb_deployed_state:%s",ilb_deployed_state) # Note: could be initializing or running
        else:
            LOG.warning(" didn't get expected number of terraform out lines?")
            if b'No outputs found' in olines:
                deployed_state = 'NOT deployed' # assumed if no outputs found
            else:
                deployed_state = "NOT deployed" # output cmd gave unexpected output?
            deployed = False
            ilb_ip_address = "0.0.0.0"

        LOG.info("Sending state rsp")
        yield self.get_state_response(
            name,
            cluster_name,
            ps_cmd,
            out_cmd_args,
            out_cmd_proc,
            deployed,
            deployed_state,
            ilb_ip_address,
        )

    def after_tf_apply_cmd_proc(self, name, cluster_name, ps_cmd):
        #LOG.info(cmd_args)
        cmd_args = ["state", "list"]
        yield from self.execute_sequence_of_terraform_cmds(name, cluster_name, ps_cmd, cmd_args)

    def after_tf_destroy_cmd_proc(self, name, cluster_name, ps_cmd):
        #LOG.info(cmd_args)
        cmd_args = ["state", "list"]
        yield from self.execute_sequence_of_terraform_cmds(name, cluster_name, ps_cmd, cmd_args)

    def batch_std_lines(self, name, cluster_name, ps_cmd, proc_lines, get_std_response):
        stdlines = "".encode()
        LOG.info("Reading lines...")
        n = 0
        for line in proc_lines:
            #LOG.info("Read line:%s", n)
            stdlines = stdlines + line
            n = n + 1
        LOG.info(f"Sending response with {n} lines")
        yield get_std_response(
            name,
            cluster_name,
            ps_cmd,
            stdlines,
        )
        #LOG.info(stdlines.decode())
        LOG.handlers[0].flush()

    def poll_proc_and_yield_console_rsps(self, name, cluster_name, ps_cmd, cmd_args, proc):
        '''
        The method `proc.poll()` checks if the subprocess has terminated. If the subprocess has not terminated, `proc.poll()` returns `None` and if it has terminated, it returns the exit code of the subprocess. Therefore, `proc.poll()` is non-blocking; it simply checks the status of the process and returns immediately.

        The `while proc.poll() is None:` loop will continue to iterate as long as the subprocess is running. During each iteration, it reads from stdout and stderr, yields any output lines, and then sleeps for 1 second if loop count is > 1 before checking again. So, this loop is essentially polling the process status and its output, allowing the function to process the output in a streaming manner while the subprocess is still running.

        Once `proc.poll()` returns a non-`None` value (i.e., the subprocess has terminated), the loop will exit, and the code will proceed to the cleanup and error checking steps after the loop.

        By default, stdout and stderr streams are blocking, meaning that reading from them will wait until there is some data to read or until the pipe is closed. So the loop is likely to only iterate once: it's getting blocked on the first call to self.batch_std_lines(...), waiting for the subprocess to finish and close its stdout or stderr.

        If the subprocess doesn't output anything (or very little) to its stdout or stderr until it has completed, the self.batch_std_lines(...) calls are likely to block until the subprocess is finished.

        '''
        LOG.info(f"Enter -- {ps_cmd} {name} {cluster_name} with {cmd_args}")
        yield self.get_cmd_args_response(name, ps_cmd, cmd_args)
        n = 0
        while proc.poll() is None:
            LOG.info("calling for stdout")
            yield from self.batch_std_lines(name, cluster_name, ps_cmd, proc.stdout, self.get_stdout_response)
            LOG.info("calling for stderr")
            yield from self.batch_std_lines(name, cluster_name, ps_cmd, proc.stderr, self.get_stderr_response)
            if n > 1:
                LOG.info("sleeping 1 ...")
                sleep(1)
            n = n + 1
        LOG.info(f"Polled {n} times with return code:{proc.returncode} for {ps_cmd} SUB cmd_args:{cmd_args}")
        LOG.handlers[0].flush()
        if proc.returncode != 0:
            emsg = (f"{cmd_args} FAILED for ps_cmd |{ps_cmd}| {name} {cluster_name} with return code {proc.returncode}")
            LOG.warning(emsg)
            raise subprocess.CalledProcessError(returncode=proc.returncode, cmd=cmd_args)

    def execute_sequence_of_terraform_cmds(self, name, cluster_name, ps_cmd, cmd_args):
        cmd_args = [get_terraform_cli(), get_chdir_parm(name,cluster_name)] + cmd_args
        LOG.info(cmd_args)
        with subprocess.Popen(
            cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as proc:
            # at this point the subprocess is done
            # Here we call the followup processing function specific to that terraform cmd
            if "refresh" in cmd_args:
                yield from self.poll_proc_and_yield_console_rsps(name, cluster_name,  ps_cmd, cmd_args, proc)
                yield from self.after_tf_refresh_cmd_proc(name, cluster_name, ps_cmd)
            elif "-destroy" in cmd_args:
                yield from self.poll_proc_and_yield_console_rsps(name, cluster_name, ps_cmd, cmd_args, proc)
                yield from self.after_tf_destroy_cmd_proc(name, cluster_name, ps_cmd)
            elif "apply" in cmd_args:
                yield from self.poll_proc_and_yield_console_rsps(name, cluster_name, ps_cmd, cmd_args, proc)
                yield from self.after_tf_apply_cmd_proc(name, cluster_name, ps_cmd)
            elif "plan" in cmd_args:
                yield from self.poll_proc_and_yield_console_rsps(name, cluster_name, ps_cmd, cmd_args, proc)
                yield from self.after_tf_apply_cmd_proc(name, cluster_name, ps_cmd)
            elif "state" in cmd_args:
                yield from self.poll_and_process_tf_state_list_cmd_proc(name, cluster_name, ps_cmd, cmd_args, proc)
            elif "output" in cmd_args:
                yield from self.poll_and_process_tf_output_cmd_proc(name, cluster_name, ps_cmd, cmd_args, proc)
            else:
                emsg = (f"terraform cmd {repr(cmd_args)} FAILED for ps_cmd |{ps_cmd} {cluster_name}|")
                LOG.error(emsg)
                raise PS_InternalError(emsg)

    def execute_cmd(self, name, cluster_name, ps_cmd, cmd_args):
        LOG.info(f'{name} {ps_cmd} {cluster_name} with {cmd_args}')
        with subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            # at this point the subprocess is done
            # Here we call the followup processing function specific to that terraform cmd
            yield from self.poll_proc_and_yield_console_rsps(name, cluster_name, ps_cmd, cmd_args, proc)

    def execute_terraform_cmd(self, name, cluster_name, ps_cmd, cmd_args):
        cmd_args = [get_terraform_cli(), get_chdir_parm(name)] + cmd_args
        LOG.info(f'{name} {cluster_name} {ps_cmd} with {cmd_args}')
        with subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            # at this point the subprocess is done
            # Here we call the followup processing function specific to that terraform cmd
            yield from self.poll_proc_and_yield_console_rsps(name, cluster_name, ps_cmd, cmd_args, proc)

    def delete_workspace_for_org(self, name, cluster_name):
        try:
            tf_cmd_args = ["workspace", "select", "default"]
            yield from self.execute_terraform_cmd(name, cluster_name, 'TearDown', tf_cmd_args)
            tf_cmd_args = ["workspace", "delete", f'localhost-{name}']
            yield from self.execute_terraform_cmd(name, cluster_name, 'TearDown', tf_cmd_args)
        except Exception as e:
            LOG.exception(f'Exception:{e}')
            raise e

    def get_specific_tf_version_files_from_s3(self, s3_client, name, cluster_name, version):
        try:
            tf_dir = get_terraform_dir(name, cluster_name) # of the form /<org>/<cluster_name>/terraform/
            folder = f"prov-sys/cluster_tf_versions/{version}"
            if download_s3_folder(s3_client=s3_client, 
                                    bucket_name=S3_BUCKET,
                                    s3_folder=folder,
                                    local_dir=tf_dir):
                LOG.info(f"downloaded s3 bucket:{S3_BUCKET} folder:{folder} into local_dir:{tf_dir} for SetUp")
            else:
                emsg = f"FAILED! error in download of s3 bucket:{S3_BUCKET} folder:{folder} into local_dir:{tf_dir} for SetUp "
                LOG.error(emsg)
                raise PS_InternalError(emsg)
        except Exception as e:
            LOG.exception(f'Exception:{e}')
            raise e

    def setup_terraform_env(self, s3_client, name, cluster_name, version, is_public, now):
        LOG.info(f"Start SetUp of provision environment for org:{name} {cluster_name} version:{version}")
        st = datetime.now(timezone.utc)
        try:
            tf_dir = get_terraform_dir(name, cluster_name) # of the form /<org>/terraform/
            # 
            # First check if terraform files are already downloaded 
            #  
            if Path(tf_dir).exists():
                LOG.info(f"terrform files in folder {tf_dir} already exist")
                setup_cfg = read_SetUpCfg(name, cluster_name)
                if version != setup_cfg.version:
                    LOG.info(f"terraform files in folder {tf_dir} are for version:{setup_cfg.version} and not for requested setup version:{version} replacing...")
                    yield from self.execute_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp', cmd_args=["rm", "-rvf", tf_dir])
            else:
                LOG.info(f"terraform folder {tf_dir} do not exist creating it")
                try:
                    yield from self.execute_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp', cmd_args=["mkdir", "-vp", tf_dir])
                    setup_cfg = ps_server_pb2.SetUpReq()
                    setup_cfg.name = name
                    setup_cfg.cluster_name = cluster_name
                    setup_cfg.version = version
                    setup_cfg.is_public = is_public
                    setup_cfg.now = now
                except Exception as e:
                    emsg = (f" Processing SetUp {name} {cluster_name} cluster caught this exception creating tf_dir: ")
                    LOG.exception(emsg)
                try:
                    write_SetUpCfg(name, cluster_name, setup_cfg)
                    yield from self.execute_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp', cmd_args=["ls", tf_dir])
                except subprocess.CalledProcessError as e:
                    # expect to see this--> "ls: cannot access {tf_dir}: No such file or directory"
                    LOG.info(f"terraform folder {tf_dir} do not exist expect to see this--> 'ls: cannot access {tf_dir}: No such file or directory' in e:{e}")
            yield from self.execute_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp', cmd_args=["mkdir", "-vp", tf_dir])
            self.get_specific_tf_version_files_from_s3(s3_client, name, cluster_name, version)       
            update_SetUpCfg(name, version, is_public, now)
            yield from self.execute_cmd          (name=name, cluster_name=cluster_name, ps_cmd='SetUp',    cmd_args=["ls", '-al', tf_dir])
            yield from self.execute_terraform_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp',    cmd_args=["init"])
            yield from self.execute_terraform_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp',    cmd_args=["validate"])
            ws_name = get_domain_env()+"-"+name+"-"+cluster_name
            workspaces = get_workspace_list(name, cluster_name)
            LOG.info("%s in %s ?",ws_name,workspaces)
            if ws_name not in workspaces:
                yield from self.execute_terraform_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp',  cmd_args=["workspace", "new", ws_name])
            else:
                yield from self.execute_terraform_cmd(name=name, cluster_name=cluster_name, ps_cmd='SetUp',  cmd_args=["workspace", "select", ws_name])

            if not upload_current_tf_files_to_s3(s3_client=s3_client,name=name, cluster_name=cluster_name):
                emsg = f"FAILED! to upload_current_tf_files_to_s3()"
                LOG.error(emsg)
                raise PS_InternalError(emsg)

        except Exception as e:
            emsg = (f" Processing SetUp {name} {cluster_name} cluster caught this exception: ")
            LOG.exception(emsg)
            emsg += repr(e)
            yield self.get_Response_Obj(
                name=name,
                cluster_name=cluster_name,
                ps_cmd='SetUp',
                ps_server_error=True,
                error_msg=emsg,)
            raise e
        finally:
            # ALWAYS send a done!
            r = self.get_Response_Obj(
                name=name,
                cluster_name=cluster_name,
                ps_cmd='SetUp',
                done=True,
            )
            r.cli.valid = True
            r.cli.updating = True
            elapsed_tm = datetime.now(timezone.utc) - st
            r.cli.stdout = (
                "**************** "
                + name
                + " "
                + cluster_name
                + " "
                + 'SetUp'
                + " Completed "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                + " elasped:"
                + repr(elapsed_tm)
            )
            yield r
            LOG.info(f"Done SetUp of provision environment for org:{name} {cluster_name} version:{version}")
            LOG.handlers[0].flush()

    def teardown_terraform_env(self, s3_client, name, cluster_name):
        LOG.info(f"Start TearDown of provision environment for org:{name} {cluster_name}")
        st = datetime.now(timezone.utc)
        try:
            ws_list = get_workspace_list(name, cluster_name)
            if ws_list is not None:
                for ws in ws_list:
                    if name in ws:
                        LOG.info(f'clean_workspace: deleting workspace ws:{ws} for org:{name} cluster:{cluster_name}')
                        yield from self.delete_workspace_for_org(name, cluster_name)
            LOG.info(f"Removing tf files from s3 for name:{name} cluster:{cluster_name}")
            s3_folder = os.path.join('prov-sys',f'{get_domain_env()}','current_cluster_tf_by_org',name)
            if s3_folder_exists(s3_client,S3_BUCKET,s3_folder):
                LOG.info(f"Removing s3_folder:{s3_folder} from s3 for name:{name} cluster:{cluster_name}")
                if not delete_folder_from_s3(s3_client=s3_client,
                                            bucket=S3_BUCKET,
                                            s3_folder=s3_folder):
                    LOG.error(f"Failed to remove s3_folder:{s3_folder} from s3 for name:{name} cluster:{cluster_name}")
            LOG.info(f"Removing local cluster_dir:{get_cluster_root_dir(name)} and tf_dir:{get_terraform_dir(name, cluster_name)} for org:{name} cluster:{cluster_name}")
            yield from self.execute_cmd(name=name, ps_cmd='TearDown', cmd_args=['rm','-rvf',get_cluster_root_dir(name, cluster_name)])
        except Exception as e:
            emsg = (f" Processing {'TearDown'} {name} {cluster_name} cluster caught this exception: ")
            LOG.exception(emsg)
            emsg += repr(e)
            yield self.get_Response_Obj(
                name=name,
                cluster_name=cluster_name,
                ps_cmd='TearDown',
                ps_server_error=True,
                error_msg=emsg,)
        finally:
            # ALWAYS send a done!
            r = self.get_Response_Obj(
                name=name,
                cluster_name=cluster_name,
                ps_cmd='TearDown',
                done=True,
            )
            r.cli.valid = True
            r.cli.updating = True
            elapsed_tm = datetime.now(timezone.utc) - st
            r.cli.stdout = (
                "**************** "
                + name
                + " "
                + cluster_name
                + " "
                + 'TearDown'
                + " Completed "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                + " elasped:"
                + repr(elapsed_tm)
            )
            yield r
            LOG.info(f"TearDown {name} {cluster_name} cluster completed")
            LOG.handlers[0].flush()

    def process_Update_cmd(self,request,s3_client):
        LOG.info(f"Update {request.name} {request.cluster_name}")
        try:
            st = datetime.now(timezone.utc)
            tf_dir = get_terraform_dir(request.name, request.cluster_name)
            dir_exists = Path(tf_dir).exists()
            current_version = None
            if dir_exists:
                # the files already existed so we assume the cluster is already deployed
                # so we need to use the version that is in the SetUp.json file
                setup_cfg = read_SetUpCfg(name=request.name, cluster_name=request.cluster_name)
            else:
                emsg = f"FAILED! terraform files in folder {tf_dir} do not exist, SetUp must be run first"
                LOG.error(emsg)
                raise PS_InternalError(emsg)
            cluster_version = f"cluster_version={setup_cfg.version}"
            is_public = "is_public="+ str(setup_cfg.is_public)
            if self.valid_deploy_args(request):
                domain = "domain=" + get_domain_env()
                org_name = "org_name="+request.name  # lowercase enables matches to dns records
                cluster_name = "cluster_name="+request.cluster_name  # lowercase enables matches to dns records
                node_asg_min_capacity = "node_asg_min_capacity=" + str(request.min_nodes)
                node_asg_max_capacity = "node_asg_max_capacity=" + str(request.max_nodes)
                node_asg_desired_capacity = "node_asg_desired_capacity=" + str(request.num_nodes)
                cmd_args = [
                    "apply",
                    "-auto-approve",  # comment out here for testing/debugging
                    "-var",
                    cluster_version,
                    "-var",
                    domain,
                    "-var",
                    is_public,
                    "-var",
                    org_name,
                    "-var",
                    cluster_name,
                    "-var",
                    node_asg_min_capacity,
                    "-var",
                    node_asg_max_capacity,
                    "-var",
                    node_asg_desired_capacity
                ]
                try:
                    yield from self.execute_sequence_of_terraform_cmds(name=request.name, cluster_name=cluster_name, ps_cmd='Update', cmd_args=cmd_args)
                except Exception as e:
                    emsg = f"FAILED! {str(e)} for Update {request.name} {request.cluster_name} with version:{setup_cfg.version}"
                    LOG.error(emsg)
                    raise PS_InternalError(emsg)
            else:
                emsg = f"valid_deploy_args FAILED for Update {request.name} {request.cluster_name} INVALID deploy args (unmentioned fields are ZERO!) {str(request)}"
                LOG.error(emsg)
                raise PS_InternalError(emsg)
        except Exception as e:
            emsg = (f" Processing Update {request.name} {request.cluster_name} cluster caught this exception: ")
            LOG.exception(emsg)
            emsg += repr(e)
            yield self.get_Response_Obj(
                name=request.name,
                cluster_name=request.cluster_name,
                ps_cmd='Update',
                ps_server_error=True,
                error_msg=emsg,)
        finally:
            # ALWAYS send a done!
            r = self.get_Response_Obj(
                name=request.name,
                cluster_name=request.cluster_name,
                ps_cmd='Update',
                done=True,
            )
            r.cli.valid = True
            r.cli.updating = True
            elapsed_tm = datetime.now(timezone.utc) - st
            r.cli.stdout = (
                "**************** "
                + request.name
                + " "
                + request.cluster_name
                + " "
                + 'Update'
                + " Completed "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                + " elasped:"
                + repr(elapsed_tm)
            )
            yield r
            LOG.info(f"Update {request.name} {cluster_name} cluster completed")
            LOG.handlers[0].flush()

    def process_Refresh_cmd(self,request,s3_client):
        LOG.info(f"Refresh {request.name} {request.cluster_name}")
        try:
            st = datetime.now(timezone.utc)
            tf_dir = get_terraform_dir(request.name, request.cluster_name)
            if not Path(tf_dir).exists():
                clirsp = ps_server_pb2.cli_rsp(valid=True, stdout=f"terraform files in folder {tf_dir} do not exist, assuming cluster is not deployed")
                stateObj = ps_server_pb2.StateOfCluster(valid=True,deployed=False,deployed_state="",ip_address="")
                yield ps_server_pb2.Response(   name=request.name,
                                                cluster_name=request.cluster_name,
                                                ps_cmd='Refresh',
                                                state=stateObj,
                                                cli=clirsp)
            else:
                yield from self.execute_sequence_of_terraform_cmds(name=request.name, cluster_name=request.cluster_name, ps_cmd='Refresh', cmd_args=["refresh"])                    
        except Exception as e:
            emsg = (f" Processing Refresh {request.name} {request.cluster_name} cluster caught this exception: ")
            LOG.exception(emsg)
            emsg += repr(e)
            yield self.get_Response_Obj(
                name=request.name,
                cluster_name=request.cluster_name,
                ps_cmd='Refresh',
                ps_server_error=True,
                error_msg=emsg,)
        finally:
            # ALWAYS send a done!
            r = self.get_Response_Obj(
                name=request.name,
                cluster_name=request.cluster_name,
                ps_cmd='Refresh',
                done=True,
            )
            r.cli.valid = True
            r.cli.updating = True
            elapsed_tm = datetime.now(timezone.utc) - st
            r.cli.stdout = (
                "**************** "
                + request.name
                + " "
                + request.cluster_name
                + " "
                + 'Refresh'
                + " Completed "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                + " elasped:"
                + repr(elapsed_tm)
            )
            yield r
            LOG.info(f"Refresh {request.name} {request.cluster_name} cluster completed")
            LOG.handlers[0].flush()

    def process_Destroy_cmd(self,request,s3_client):
        LOG.info(f"Destroy {request.name} ")
        try:
            st = datetime.now(timezone.utc)
            tf_dir = get_terraform_dir(request.name,request.cluster_name)
            LOG.info(f"Destroying cluster {request.name} {request.cluster_name} with version:{read_SetUpCfg(request.name).version}")
            cmd_args = [
                "apply",
                "-auto-approve", 
                "-destroy" 
            ]
            yield from self.execute_sequence_of_terraform_cmds(name=request.name, ps_cmd='Destroy', cmd_args=cmd_args)
        except Exception as e:
            emsg = (f" Processing Destroy {request.name} {request.cluster_name} cluster caught this exception: ")
            LOG.exception(emsg)
            emsg += repr(e)
            yield self.get_Response_Obj(
                name=request.name,
                cluster_name=request.cluster_name,
                ps_cmd='Destroy',
                ps_server_error=True,
                error_msg=emsg,)
        finally:
            # ALWAYS send a done!
            r = self.get_Response_Obj(
                name=request.name,
                cluster_name=request.cluster_name,
                ps_cmd='Destroy',
                done=True,
            )
            r.cli.valid = True
            r.cli.updating = True
            elapsed_tm = datetime.now(timezone.utc) - st
            r.cli.stdout = (
                "**************** "
                + request.name
                + " "
                + request.cluster_name
                + " "
                + 'Destroy'
                + " Completed "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                + " elasped:"
                + repr(elapsed_tm)
            )
            yield r
            LOG.info(f"Destroy {request.name} {request.cluster_name} cluster completed")
            LOG.handlers[0].flush()

    def SetUp(self, request, context):
        LOG.info(f"SetUp request:{MessageToString(request)} ")
        s3_client = get_s3_client()
        yield from self.setup_terraform_env(s3_client=s3_client, name=request.name, cluster_name=request.cluster_name, version=request.version, is_public=request.is_public, now=request.now)

    def TearDown(self, request, context):
        s3_client = get_s3_client()
        yield from self.teardown_terraform_env(s3_client=s3_client, name=request.name, cluster_name=request.cluster_name)

    ############## Control RPC Entrances #########
    def GetVersions(self, request, context):
        '''
            This is the list of versions of terraform files available in s3
        '''
        versions=get_versions(get_s3_client())
        return ps_server_pb2.GetVersionsRsp(versions=versions)

    def GetCurrentSetUpCfg(self,request,context):
        '''
        This is the version of terraform files setup for the Org's cluster
        '''
        setup_cfg = read_SetUpCfg(request.name, request.cluster_name)
        return ps_server_pb2.GetCurrentSetUpCfgRsp(setup_cfg=setup_cfg)

    # these are the pkg versions obtained from the container
    def GetPSVersions(self, request, context):
        ps_server_versions = get_ps_versions()
        LOG.debug(f'ps server versions:{ps_server_versions}')
        return ps_server_pb2.GetPSVersionsRsp(ps_versions=ps_server_versions)

    def Update(self, request, context):  ## This is called by GRPC framework
        LOG.info(f'Update domain:{get_domain_env()} TERRAFORM_CLI:{get_terraform_cli()}')
        s3_client = get_s3_client()
        yield from self.process_Update_cmd(request,s3_client)

    def Refresh(self, request, context):  ## This is called by GRPC framework
        LOG.info(f'Refresh {request.name} {request.cluster_name} domain:{get_domain_env()} TERRAFORM_CLI:{get_terraform_cli()}')
        s3_client = get_s3_client()
        yield from self.process_Refresh_cmd(request,s3_client)

    def Destroy(self, request, context):  ## This is called by GRPC framework
        LOG.info(f'Destroy {request.name} {request.cluster_name} domain:{get_domain_env()} TERRAFORM_CLI:{get_terraform_cli()}')
        s3_client = get_s3_client()
        yield from self.process_Destroy_cmd(request,s3_client)


####################### Control End ##################################
def update_key_in_file(filename, old_key, new_key):
    updated = False  # To track if we've made any changes to the JSON data
    # Open the JSON file and load its content
    with open(filename, 'r') as file:
        data = json.load(file)
        
    # Check if the old key exists
    if old_key in data:
        # Update the key
        data[new_key] = data.pop(old_key)
        updated = True

    # Check if the 'cluster_name' key doesn't exist, and insert it
    if 'cluster_name' not in data:
        data['cluster_name'] = 'compute'
        updated = True

    # Save the updated content back to the file, if there were any changes
    if updated:
        with open(filename, 'w') as file:
            json.dump(data, file, indent=4)


def update_key_in_dir(local_dir, target_filename, old_key, new_key):
    # Walk through all directories and files under the given directory
    for dirpath, _, filenames in os.walk(local_dir):
        for file in filenames:
            if file == target_filename:
                update_key_in_file(os.path.join(dirpath, file), old_key, new_key)

def idempotent_migration():
    '''
    This is a one time migration to change the key from orgName to name in the SetUp.json file
    This routine can run many times without any side effects
    '''
    # this can be removed after a successful migration and deploy to production servers
    update_key_in_dir(local_dir=get_root_dir(), target_filename="SetUp.json", old_key="orgName", new_key="name")


@contextlib.contextmanager
def run_server(host, port, use_tls):
    hoststring = host + ":" + str(port)

    LOG.info("running server listening on:%s use_tls:%s", hoststring, use_tls)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    ps_server_pb2_grpc.add_ControlServicer_to_server(Control(), server)
    ps_server_pb2_grpc.add_AccountServicer_to_server(Account(), server)
    if use_tls == "True":
        # We use this in development so we test the ps-web client with tls certs
        SERVER_CERTIFICATE = _load_credential_from_file("credentials/ps-server.crt")
        SERVER_CERTIFICATE_KEY = _load_credential_from_file("credentials/ps-server.key")
        server_credentials = grpc.ssl_server_credentials(
            ((SERVER_CERTIFICATE_KEY, SERVER_CERTIFICATE),)
        )
        LOG.info("Secure port:%s",hoststring)
        server.add_secure_port(hoststring, server_credentials)
    else:
        # NOTE: for prod we use this insecure channel because the loadbalancer does the cert
        LOG.info("Insecure port:%s",hoststring)
        port = server.add_insecure_port(hoststring)

    server.start()
    try:
        yield server, host, port, use_tls
    finally:
        server.stop(0)

def main():
    os.environ['TZ'] = 'UTC'
    time.tzset()
    LOG = logging.getLogger("ps_logger")
    LOG.info(f"Starting ps-server @ {datetime.now().astimezone()}")
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--host", nargs="?", type=str, default="[::]", help="the listening host"
        )
        parser.add_argument(
            "--port", nargs="?", type=int, default=50051, help="the listening port"
        )
        parser.add_argument(
            "--use_tls",
            default="False",
            help="secured with tls certs?",
        )
        args = parser.parse_args()

        #LOG.info(repr(get_num_nodes("us-west-2","Developers","latest")))
        #LOG.info(repr(get_num_nodes("us-west-2","esr","")))
        if get_domain_env() == "":
            emsg = "DOMAIN env not set!"
            LOG.error(emsg)
            LOG.handlers[0].flush()
            raise PS_InternalError(emsg)
        versions = get_ps_versions()
        LOG.info(f'ps server versions: {versions}')

        try:
            if get_domain_env() == "localhost":
                # Get the current environment
                env_vars = os.environ
                # Print each environment variable on a new line
                for key, value in env_vars.items():
                    LOG.info(f"{key}: {value}")            
                poll_for_localstack_status(LOG)
            s3_client = get_s3_client()
            LOG.info(f"terraform versions found in S3:{get_versions(s3_client)}")
            s3_folder = f"prov-sys/{get_domain_env()}/current_cluster_tf_by_org"
            local_dir = get_root_dir()
            if download_s3_folder(s3_client=s3_client, 
                                    bucket_name=S3_BUCKET,
                                    s3_folder=s3_folder,
                                    local_dir=local_dir):
                LOG.info(f"downloaded s3 bucket:{S3_BUCKET} folder:{s3_folder} into local_dir:{local_dir}")
            else:
                emsg = f"FAILED! error in download of s3 bucket:{S3_BUCKET} folder:{s3_folder} into local_dir:{local_dir}"
                LOG.error(emsg)

            idempotent_migration()
        except Exception as e:
            LOG.exception(f"download_dir caught exception:{repr(e)}")
        #LOG.info("calling run_server")
        with run_server(args.host, args.port, args.use_tls) as (
            server,
            host,
            port,
            use_tls,
        ):
            LOG.info(f"---------- Server is READY listening at {host}:{port} use_tls?:{use_tls} domain:{get_domain_env()} terraform_cli:{get_terraform_cli()}----------"
            )
            LOG.handlers[0].flush()
            server.wait_for_termination()

    except HTTPError as http_err:
        LOG.error(f"HTTP error occurred getting Org names from website: {http_err}")
    except Exception as e:
        LOG.error(f"Caught an exception in main: {e}")
    LOG.info("Exiting ps-server ")


if __name__ == "__main__":
    main()
