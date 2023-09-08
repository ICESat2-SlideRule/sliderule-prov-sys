import pytest
import logging
import sys
import os
import pathlib
import uuid
import pytz
import json
import pprint
import requests
import time_machine
from importlib import import_module
from datetime import datetime, timezone, timedelta
from decimal import *
from django.urls import reverse
from users.tests.utilities_for_unit_tests import get_test_org,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user,process_onn_api,process_onn_expires,process_owner_ps_cmd,process_owner_ps_Destroy_cmd,process_owner_ps_Refresh_cmd,process_owner_ps_Update_cmd
from users.models import Membership,OwnerPSCmd,OrgAccount,ClusterNumNode,NodeGroup
from users.forms import NodeGroupCfgForm
from users.tasks import loop_iter,init_new_org_memberships,init_new_org_memberships,process_prov_sys_tbls,get_node_group_queue_name,get_or_create_ClusterNumNodes
from django.contrib import messages
from django.contrib.auth import get_user_model
from allauth.account.decorators import verified_email_required

# Import the fixtures
from users.tests.utilities_for_unit_tests import TEST_USER,TEST_PASSWORD,DEV_TEST_USER,DEV_TEST_PASSWORD,TEST_ORG_NAME
from users.global_constants import *
module_name = 'views'
# discover the src directory to import the file being tested
parent_dir = pathlib.Path(__file__).parent.resolve()
src_path = os.path.join(parent_dir, '..', '')
sys.path.append(src_path)
import views

# setup logging to terminal
level = logging.DEBUG
#level = logging.INFO
#logger = logging.getLogger(__name__)
logger = logging.getLogger('django')
logger.setLevel(level)

@pytest.fixture()
def tasks_module():
    yield import_module(module_name)

# do all setup before running all tests here
def setup_module(tasks_module):
    logger.info('---setup complete---')
    

# teardown after running all tests 
def teardown_module(tasks_module):
    logger.info('---teardown complete---')

#@pytest.mark.dev
@pytest.mark.django_db
def test_login(client):
    # Get the URL for the login view.
    login_url = reverse('account_login')

    # Redirect the user to the login view.
    response = client.get(login_url)
    assert response.status_code == 200
    # Set the request body.
    body = {
        'username': DEV_TEST_USER,
        'password': DEV_TEST_PASSWORD
    }

    # Make the request.
    response = client.put(login_url, json=body)

    # Assert that the response is successful.
    assert response.status_code == 200

def login_owner_user(orgAccountObj,client):
    url = reverse('org-token-obtain-pair')
    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert(OwnerPSCmd.objects.count()==0)
    json_data = json.loads(response.content)
    logger.info(f"rsp:{json_data}")
    return json_data['access']   

def simple_test_onn_api(client,
                        orgAccountObj,
                        view_name,
                        url_args,
                        access_token,
                        expected_status):
    url = reverse(view_name,args=url_args)
    logger.info(f"using url:{url}")
    if 'post' in view_name:
        response = client.post(url,headers={'Authorization': f"Bearer {access_token}"})
    else:
        response = client.put(url,headers={'Authorization': f"Bearer {access_token}"})
    json_data = json.loads(response.content)
    if expected_status == 200:
        assert(json_data['msg']!='')   
        assert(json_data['error_msg']=='')
    else:
        logger.info(f"json_data:{json_data}")

    nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
    nodeGroupObj.refresh_from_db()    # The client.post above updated the DB so we need this
    orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
    logger.info(f"{response.status_code} == {expected_status} ?")
    assert(response.status_code == expected_status) 
    return json_data

#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_apis_simple_case(caplog,client,verified_TEST_USER,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test various negative test cases apis
    '''
    caplog.set_level(logging.DEBUG)
    orgAccountObj=get_test_org()
    response = client.post(reverse('org-token-obtain-pair'),data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    assert(response.status_code == 200)   
    json_data = json.loads(response.content)
    access_token = json_data['access']   

    # validate a success first!
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3,15],
                                    access_token=access_token,
                                    expected_status=200)
    assert('created and queued capacity request' in json_data['msg'])

#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_negative_test_apis(caplog,client,verified_TEST_USER,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test various negative test cases apis
    '''
    caplog.set_level(logging.DEBUG)
    orgAccountObj=get_test_org()

    # test invalid name in token
    response = client.post(reverse('org-token-obtain-pair'),data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':'bad_org'})
    assert(response.status_code == 403) 

    # invalid user
    response = client.post(reverse('org-token-obtain-pair'),data={'username':"bad_username",'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    assert(response.status_code == 401)    

    # bad password
    response = client.post(reverse('org-token-obtain-pair'),data={'username':OWNER_USER,'password':"bad_passw", 'org_name':orgAccountObj.name})
    assert(response.status_code == 401)    

    # Now good parms for token
    response = client.post(reverse('org-token-obtain-pair'),data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    assert(response.status_code == 200)    
    access_token = json.loads(response.content)['access']   

    # test corrupted token
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3,15],
                                    access_token=access_token+'BAD',
                                    expected_status=403)
    assert(json_data['messages'][0]['message'] == 'Token is invalid or expired') 

    # test invalid num_nodes gets clamped
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,300,15],
                                    access_token=access_token,
                                    expected_status=200)

    # test invalid num_nodes gets clamped
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='put-num-nodes',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,300],
                                    access_token=access_token,
                                    expected_status=200)

    # test 0 num_nodes (with org accepting 0)
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='put-num-nodes',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,0],
                                    access_token=access_token,
                                    expected_status=200)

    # test num_nodes less than min gets clamped
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.save()
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='put-num-nodes',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,1],
                                    access_token=access_token,
                                    expected_status=200)
    assert (f"Deploying test_org cluster Created new " in json_data['msg'])
    assert((f'Deploying {orgAccountObj.name} cluster' in json_data['msg']) or (f'Updating {orgAccountObj.name} cluster' in json_data['msg']))

    # test num_nodes greater than max gets clamped
    nodeGroupObj.save()
    json_data = simple_test_onn_api(client,
                                    orgAccountObj=orgAccountObj,
                                    view_name='put-num-nodes',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,15],
                                    access_token=access_token,
                                    expected_status=200)
    assert (f"Deploying test_org cluster Created new " in json_data['msg'])
    assert((f'Updating {orgAccountObj.name} cluster' in json_data['msg']) or (f'Deploying {orgAccountObj.name} cluster' in json_data['msg']))


#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_api_urls(caplog,client,verified_TEST_USER,mock_email_backend,initialize_test_environ):
    '''
        This procedure will various cases for the main loop
    '''
    caplog.set_level(logging.DEBUG)
    grpc_timeout = int(os.environ.get("GRPC_TIMEOUT_SECS",1800))
    timeshift = grpc_timeout/2.0
    logger.info(f"Shifting time back by {timeshift} seconds (with a grpc_timeout:{grpc_timeout})")
    fake_time_now = datetime.now(timezone.utc) - timedelta(seconds=timeshift)
    with time_machine.travel(fake_time_now,tick=False):
        orgAccountObj = get_test_org()        
        response = client.post(reverse('org-token-obtain-pair'),data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
        assert(response.status_code == 200)   
        json_data = json.loads(response.content)
        access_token = json_data['access'] 
        current_fake_tm = datetime.now(timezone.utc)  
        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3,15],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=0,
                                    num_iters=0,
                                    expected_change_ps_cmd=0,
                                    expected_status='QUEUED')

        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3,15],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=6,
                                    expected_change_ps_cmd=1, # because num_iters>0 and we had one QUEUED
                                    expected_status='REDUNDANT')
        assert(loop_count==6)
        current_fake_tm = datetime.now(timezone.utc)+timedelta(seconds=3) # 6 iters above is 3 seconds
        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='put-num-nodes',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=0,
                                    expected_change_ps_cmd=0, # because no iters
                                    expected_status='QUEUED')

        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3,16],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=1,
                                    expected_change_ps_cmd=0, # because highest num nodes is still 3
                                    expected_status='QUEUED')

        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,2,17],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=0,
                                    expected_change_ps_cmd=0,
                                    expected_status='QUEUED')

        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,1,19],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=0,
                                    expected_change_ps_cmd=0,
                                    expected_status='QUEUED')

        loop_count,response = process_onn_api(client,
                                    orgAccountObj,
                                    current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,4,15],
                                    access_token=access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=1,
                                    expected_change_ps_cmd=1, # we bumped the highest to 4 and iter
                                    expected_status='QUEUED')

#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_mainloop(caplog,client,verified_TEST_USER,mock_email_backend,initialize_test_environ):
    '''
        This procedure will various cases for the main loop
    '''
    caplog.set_level(logging.DEBUG)
    grpc_timeout = int(os.environ.get("GRPC_TIMEOUT_SECS",1800))
    timeshift = grpc_timeout/2.0
    logger.info(f"Shifting time back by {timeshift} seconds (with a grpc_timeout:{grpc_timeout})")
    fake_time_now = datetime.now(timezone.utc) - timedelta(seconds=timeshift)
    with time_machine.travel(fake_time_now,tick=False):
    
        orgAccountObj = get_test_org()
        owner_access_token = login_owner_user(orgAccountObj=orgAccountObj,client=client)

        nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
        
        assert(OwnerPSCmd.objects.count()==0)
        assert(ClusterNumNode.objects.count()==0)
        assert(orgAccountObj.loop_count==0)
        assert(nodeGroupObj.num_owner_ps_cmd==0)
        assert(nodeGroupObj.num_ps_cmd==0)
        assert(nodeGroupObj.num_ps_cmd_successful==0)
        assert(nodeGroupObj.num_onn==0)
        loop_count=0
        current_fake_tm = datetime.now(timezone.utc)  

        loop_count,response = process_onn_api(client=client,
                                    orgAccountObj=orgAccountObj,
                                    new_time=current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,3,15],
                                    access_token=owner_access_token,
                                    loop_count=loop_count,
                                    data=None,
                                    num_iters=0,
                                    expected_change_ps_cmd=0,
                                    expected_status='QUEUED')
        
        #
        # This tests when multiple calls to api are processed between a loop_iter
        #
        loop_count,response = process_onn_api(client=client,
                                    orgAccountObj=orgAccountObj,
                                    new_time=current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,4,16],
                                    access_token=owner_access_token,
                                    loop_count=loop_count,
                                    data=None,
                                    num_iters=0,
                                    expected_change_ps_cmd=0,
                                    expected_status='QUEUED')

        loop_count,response = process_onn_api(client=client,
                                    orgAccountObj=orgAccountObj,
                                    new_time=current_fake_tm,
                                    view_name='post-num-nodes-ttl',
                                    url_args=[orgAccountObj.name,nodeGroupObj.name,2,17],
                                    access_token=owner_access_token,
                                    data=None,
                                    loop_count=loop_count,
                                    num_iters=0,
                                    expected_change_ps_cmd=0,
                                    expected_status='QUEUED')

        #
        # Note: local var loop_count get updated every pass 
        # BUT model object orgAccountObj only gets 
        # updated every 10 secs for loop_count
        # (i.e. 20 calls to loop_iter)
        # This is to reduce the db i/o rate
        #
        nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
        orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this

        task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
        nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
        orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
        assert(loop_count==1)
        assert(nodeGroupObj.num_onn==1)
        assert(OwnerPSCmd.objects.count()==0)
        assert(ClusterNumNode.objects.count()==3)
        assert(nodeGroupObj.num_owner_ps_cmd==0)
        assert(nodeGroupObj.num_onn==1)

        # this gets updated ever ten seconds (i.e. 50 calls to loop_iter)
        assert(orgAccountObj.loop_count==0)

        # verify setup occurred
        assert(nodeGroupObj.provision_env_ready)
        assert(nodeGroupObj.provisioning_suspended==False)

        # this on gets updated every time a ps cmd is processed
        # regardless of where it originated from (i.e. cnn or owner_ps_cmd)
        assert(nodeGroupObj.num_ps_cmd==1) # update cmd
        assert(nodeGroupObj.cfg_asg.num==4)
        assert(nodeGroupObj.num_ps_cmd_successful==1)
        task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
        assert(loop_count==2)
        
        #nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
        #orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
        assert(orgAccountObj.loop_count==0)# udpates are throttled every 20
        assert(nodeGroupObj.num_onn==1)
        # nothing else changes
        assert(OwnerPSCmd.objects.count()==0)
        assert(ClusterNumNode.objects.count()==3)
        assert(nodeGroupObj.num_owner_ps_cmd==0)
        assert(nodeGroupObj.num_onn==1)
        assert(nodeGroupObj.num_ps_cmd==1)
        assert(nodeGroupObj.num_ps_cmd_successful==1)
        assert(nodeGroupObj.cfg_asg.num==4)
        # web login
        assert(client.login(username=OWNER_USER,password=OWNER_PASSWORD))

        loop_count = process_owner_ps_Refresh_cmd(  client=client,
                                                    orgAccountObj=orgAccountObj,
                                                    new_time=datetime.now(timezone.utc),
                                                    loop_count=loop_count,
                                                    num_iters=0)

        # verify setup occurred
        assert(nodeGroupObj.provision_env_ready)
        assert(nodeGroupObj.provisioning_suspended==False)

        assert(OwnerPSCmd.objects.count()==1) 
        assert(ClusterNumNode.objects.count()==3)
        assert(nodeGroupObj.num_owner_ps_cmd==0) #not until loop_iter
        assert(nodeGroupObj.num_onn==1)
        assert(nodeGroupObj.num_ps_cmd==1)
        assert(nodeGroupObj.num_ps_cmd_successful==1)
        assert(nodeGroupObj.cfg_asg.num==4)
        assert(loop_count==2)
         #not until loop_iter
        assert(nodeGroupObj.num_onn==1)

        task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
        assert(loop_count==3)
        nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
        orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
        assert(OwnerPSCmd.objects.count()==0) # processed and cleared
        assert(ClusterNumNode.objects.count()==3)
        assert(nodeGroupObj.num_owner_ps_cmd==1)
        assert(nodeGroupObj.num_onn==1)
        assert(nodeGroupObj.num_ps_cmd==2) # processed
        assert(nodeGroupObj.num_ps_cmd_successful==2)
        assert(nodeGroupObj.cfg_asg.num==4)
       
        assert(nodeGroupObj.num_onn==1)

        # expire first one (3,15)
        # table is:
        #  3,15 <-- expire this one
        #  4,16
        #  2,17 
        loop_count = process_onn_expires(orgAccountObj,
                                        fake_time_now+timedelta(minutes=15,seconds=1),
                                        loop_count,
                                        num_iters=1,
                                        expected_change_ps_cmd=0, # 4 is highest and was already processed
                                        expected_change_OrgNumNode=-1,
                                        expected_change_num_onn=0,
                                        expected_desired_num_nodes=4)

        # simulate the deploy
        nodeGroupObj.is_deployed = True
        nodeGroupObj.save()

        # expire next one (4,16)
        # table is:
        #  3,15 **expired already
        #  4,16 <-- expire this one
        #  2,17 
        loop_count = process_onn_expires(orgAccountObj,
                                        fake_time_now+timedelta(minutes=16,seconds=1),
                                        loop_count,
                                        num_iters=1,
                                        expected_change_ps_cmd=1,
                                        expected_change_OrgNumNode=-1,
                                        expected_change_num_onn=1,
                                        expected_desired_num_nodes=2)

        # these two set like this mean we call destroy when table is empty
        assert(nodeGroupObj.destroy_when_no_nodes)
        assert(nodeGroupObj.cfg_asg.min==0)
        assert nodeGroupObj.is_deployed
        assert ClusterNumNode.objects.count()==1
        # expire next one (4,16)
        # table is:
        #  3,15 **expired already
        #  4,16 **expired already
        #  2,17 <-- expire this one
        loop_count = process_onn_expires( orgAccountObj,
                                        fake_time_now+timedelta(minutes=17,seconds=1),
                                        loop_count,
                                        num_iters=1,
                                        expected_change_ps_cmd=1, # destroy 
                                        expected_change_OrgNumNode=-1,
                                        expected_change_num_onn=1, # destroy
                                        expected_desired_num_nodes=0)

#@pytest.mark.dev
# @pytest.mark.django_db 
# @pytest.mark.ps_server_stubbed
# def test_owner_ps_cmd_resets_onn_table(caplog,client,verified_TEST_USER,mock_email_backend,initialize_test_environ):
#     '''
#         This procedure will various cases for the main loop
#     '''
#     caplog.set_level(logging.DEBUG)
#     grpc_timeout = int(os.environ.get("GRPC_TIMEOUT_SECS",1800))
#     timeshift = grpc_timeout/2.0
#     logger.info(f"Shifting time back by {timeshift} seconds (with a grpc_timeout:{grpc_timeout})")
#     fake_time_now = datetime.now(timezone.utc) - timedelta(seconds=timeshift)
#     with time_machine.travel(fake_time_now,tick=False):
    
#         orgAccountObj = get_test_org()
#         owner_access_token = login_owner_user(orgAccountObj=orgAccountObj,client=client)

#         nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
        
#         assert(OwnerPSCmd.objects.count()==0)
#         assert(ClusterNumNode.objects.count()==0)
#         assert(orgAccountObj.loop_count==0)
#         assert(nodeGroupObj.num_owner_ps_cmd==0)
#         assert(nodeGroupObj.num_ps_cmd==0)
#         assert(nodeGroupObj.num_ps_cmd_successful==0)
#         assert(nodeGroupObj.num_onn==0)
#         loop_count=0
#         current_fake_tm = datetime.now(timezone.utc)  

#         loop_count,response = process_onn_api(client,
#                                     orgAccountObj,
#                                     current_fake_tm,
#                                     'post-org-num-nodes-ttl',
#                                     [orgAccountObj.name,3,15],
#                                     owner_access_token,
#                                     loop_count,
#                                     num_iters=0,
#                                     expected_change_ps_cmd=0,
#                                     expected_status='QUEUED')
        


#         #
#         # This tests when multiple calls to api are processed between a loop_iter
#         #
#         loop_count,response = process_onn_api(client,
#                                     orgAccountObj,
#                                     current_fake_tm,
#                                     'post-org-num-nodes-ttl',
#                                     [orgAccountObj.name,4,16],
#                                     owner_access_token,
#                                     loop_count,
#                                     num_iters=0,
#                                     expected_change_ps_cmd=0,
#                                     expected_status='QUEUED')

#         loop_count,response = process_onn_api(client,
#                                     orgAccountObj,
#                                     current_fake_tm,
#                                     'post-org-num-nodes-ttl',
#                                     [orgAccountObj.name,2,17],
#                                     owner_access_token,
#                                     loop_count,
#                                     num_iters=0,
#                                     expected_change_ps_cmd=0,
#                                     expected_status='QUEUED')
#         nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
#         orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this

#         task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
#         assert(loop_count==1)
        
#         assert(nodeGroupObj.num_onn==1)
#         nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
#         orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
#         assert(OwnerPSCmd.objects.count()==0)
#         assert(ClusterNumNode.objects.count()==3)
#         assert(nodeGroupObj.num_owner_ps_cmd==0)
#         assert(nodeGroupObj.num_onn==1)

#         # this gets updated ever ten seconds (i.e. 50 calls to loop_iter)
#         assert(orgAccountObj.loop_count==0)

#         # this on gets updated every time a ps cmd is processed
#         # regardless of where it originated from (i.e. cnn or owner_ps_cmd)
#         assert(nodeGroupObj.num_ps_cmd==1)
#         assert(nodeGroupObj.num_ps_cmd_successful==1)


#         # web login
#         assert(client.login(username=OWNER_USER,password=OWNER_PASSWORD))

#         loop_count = process_owner_ps_Refresh_cmd(  client=client,
#                                                     orgAccountObj=orgAccountObj,
#                                                     new_time=datetime.now(timezone.utc),
#                                                     loop_count=loop_count,
#                                                     num_iters=0)
#         nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
#         orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
#         assert(OwnerPSCmd.objects.count()==1) 
#         assert(ClusterNumNode.objects.count()==3)
#         assert(nodeGroupObj.num_owner_ps_cmd==0) #not until loop_iter
#         assert(nodeGroupObj.num_onn==1)
#         assert(nodeGroupObj.num_ps_cmd==1)
#         assert(nodeGroupObj.num_ps_cmd_successful==1)



#         task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)

#         assert(OwnerPSCmd.objects.count()==0) 
#         assert(ClusterNumNode.objects.count()==0)  # deploy should clear this
#         logger.info(f"id:{orgAccountObj.id}")
#         orgAccountObj.refresh_from_db() 
        
#         assert(nodeGroupObj.num_owner_ps_cmd==2)
#         assert(nodeGroupObj.num_onn==1)
#         assert(nodeGroupObj.num_ps_cmd==3)
#         assert(nodeGroupObj.num_ps_cmd_successful==3)

# @pytest.mark.dev
# @pytest.mark.django_db 
# @pytest.mark.ps_server_stubbed
# def test_owner_ps_cmd_resets_onn_table(caplog,client,verified_TEST_USER,mock_email_backend,initialize_test_environ):
#     '''
#         This procedure will various cases for the main loop
#     '''
#     caplog.set_level(logging.DEBUG)
#     orgAccountObj = get_test_org()

#     nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')

#     assert(client.login(username=OWNER_USER,password=OWNER_PASSWORD))
#     loop_count = 0
#     loop_count = process_owner_ps_Refresh_cmd(  client=client,
#                                                 orgAccountObj=orgAccountObj,
#                                                 new_time=datetime.now(timezone.utc),
#                                                 loop_count=loop_count,
#                                                 num_iters=0)
#     assert(OwnerPSCmd.objects.count()==1) 
#     assert(ClusterNumNode.objects.count()==0)
#     assert(nodeGroupObj.num_owner_ps_cmd==0) #not until loop_iter
#     assert(nodeGroupObj.num_onn==0)
#     assert(nodeGroupObj.num_ps_cmd==0)
#     assert(nodeGroupObj.num_ps_cmd_successful==0)
#     assert(loop_count==0)

#     task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
#     assert(loop_count==1)
#     nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
#     orgAccountObj.refresh_from_db() # The client.post above updated the DB so we need this
#     assert(OwnerPSCmd.objects.count()==0) # processed and cleared
#     assert(ClusterNumNode.objects.count()==0) # 
#     assert(nodeGroupObj.num_owner_ps_cmd==1)
#     assert(nodeGroupObj.num_onn==0)
#     assert(nodeGroupObj.num_ps_cmd==1) # processed
#     assert(nodeGroupObj.num_ps_cmd_successful==1)
    
