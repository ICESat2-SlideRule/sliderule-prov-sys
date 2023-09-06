import pytest
import logging
import sys
import os
import pathlib
import uuid
import pytz
import json
import pprint
from importlib import import_module
from datetime import datetime, timezone, timedelta
from decimal import *
from users.tests.utilities_for_unit_tests import init_test_environ,get_test_org,get_test_compute_cluster,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user,process_onn_api,the_TEST_USER,the_OWNER_USER,the_DEV_TEST_USER,init_mock_ps_server,create_test_user,verify_api_user_makes_onn_ttl,create_active_membership,initialize_test_org,log_CNN,fake_sync_clusterObj_to_orgAccountObj,call_SetUp
from users.models import Membership,OwnerPSCmd,OrgAccount,ClusterNumNode,NodeGroup,PsCmdResult
from users.forms import ClusterCfgForm
from users.tasks import loop_iter,need_destroy_for_changed_version_or_is_public,get_or_create_ClusterNumNodes,sort_CNN_by_nn_exp,format_onn,sum_of_highest_nodes_for_each_user
from users.views import add_org_cluster_orgcost
from time import sleep
from django.contrib import messages
from django.contrib.auth import get_user_model
from allauth.account.decorators import verified_email_required
from django.contrib.auth.models import User
from django.urls import reverse
from oauth2_provider.models import Application


# Import the fixtures
from users.tests.utilities_for_unit_tests import TEST_USER,TEST_PASSWORD,DEV_TEST_USER,DEV_TEST_PASSWORD,TEST_ORG_NAME

module_name = 'views'
# discover the src directory to import the file being tested
parent_dir = pathlib.Path(__file__).parent.resolve()
src_path = os.path.join(parent_dir, '..', '')
sys.path.append(src_path)
import views

# setup logging to terminal
#level = logging.DEBUG
level = logging.INFO
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


@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_form_create(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'initialize_test_environ' fixture 
        which creates a new organization and initializes the memberships and queue
    '''
    assert(get_test_org().name == TEST_ORG_NAME)
    assert(get_test_org().owner.username == OWNER_USER)

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_owner_refreshes(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test a privileged user can queue a refresh cmd
    '''
    caplog.set_level(logging.DEBUG)
    

    logger.info(f"will now add ps cmds by calling deploy url")
    time_now = datetime.now(timezone.utc)
    caplog.set_level(logging.DEBUG)

    url = reverse('org-refresh-cluster',args=[get_test_org().id,])

    response = client.post(url)
    logger.info(f"status:{response.status_code}")
    assert ((response.status_code == 302) or (response.status_code == 200)) # 302 is redirect to login
    assert (OwnerPSCmd.objects.count()==0)
    
    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    response = client.post(url)
    # Assert the response status code and content
    assert ((response.status_code == 302) or (response.status_code == 200)) # 302 is redirect to login
    assert (OwnerPSCmd.objects.count()==1)
    client.logout()

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_redirect_login(caplog,client,mock_email_backend,create_TEST_USER,initialize_test_environ):
    '''
        This procedure will test redirect to login if not authorized yet
    '''
    caplog.set_level(logging.DEBUG)
    test_org=get_test_org()
    url = reverse('org-refresh-cluster',args=[test_org.id,])
    logger.info(f"url is using id for org:{test_org.name} owner:{test_org.owner}")
    response = client.post(url)
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 302)   
    assert (OwnerPSCmd.objects.count()==0)
    client.logout()

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_unverified_user(caplog,client,mock_email_backend,create_TEST_USER,initialize_test_environ):
    '''
        This procedure will test the response if the user did not verify email before trying to deploy
    '''
    caplog.set_level(logging.DEBUG)
    

    logger.info(f"will now add ps cmds by calling deploy url")
    time_now = datetime.now(timezone.utc)
    caplog.set_level(logging.DEBUG)
    test_org=get_test_org()
    url = reverse('org-refresh-cluster',args=[test_org.id,])
    logger.info(f"url is using id for org:{test_org.name} owner:{test_org.owner}")
    assert (OwnerPSCmd.objects.count()==0)

    # Log in the regular test user that has validated email (using fixture passed in above)
    logger.info(f"logging in with {TEST_USER}")
    assert(client.login(username=TEST_USER, password=TEST_PASSWORD))
    
    response = client.post(url)
    logger.info(f"response.wsgi_request.user.username:{response.wsgi_request.user.username} TEST_USER:{TEST_USER}")
    assert response.wsgi_request.user.username == TEST_USER
    # Assert the response status code and content
    assert response.status_code == 200 # this is because TEST_USER is has unverified email
    #logger.info(f"dir(response):{dir(response)}")
    #logger.info(f"reason_phrase:{response.reason_phrase}")
    assert(response.reason_phrase=='OK')
    #logger.info(f"content:{response.content}")
    # this verifies that allauth sent the web page that tells the user to verify their email address
    assert(b'Verify Your E-mail Address' in response.content)
    assert(b'This part of the site requires us to verify that' in response.content)
    assert(b'you are who you claim to be. For this purpose, we require that you' in response.content)

    client.logout()

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_no_privilege(caplog,client,mock_email_backend,verified_TEST_USER,initialize_test_environ):
    '''
        This procedure will test the response if the user is not a privileged user
    '''
    caplog.set_level(logging.DEBUG)
    

    logger.info(f"will now add ps cmds by calling deploy url")
    time_now = datetime.now(timezone.utc)
    caplog.set_level(logging.DEBUG)
    test_org=get_test_org()
    url = reverse('org-refresh-cluster',args=[test_org.id,])
    logger.info(f"url is using id for org:{test_org.name} owner:{test_org.owner}")
    assert (OwnerPSCmd.objects.count()==0)

    # Log in the regular test user that has validated email (using fixture passed in above)
    logger.info(f"logging in with {TEST_USER} (but this time the email is verified in test fixture)")
    assert(client.login(username=TEST_USER, password=TEST_PASSWORD))
    
    response = client.post(url)
    logger.info(f"response.wsgi_request.user.username:{response.wsgi_request.user.username} TEST_USER:{TEST_USER}")
    assert response.wsgi_request.user.username == TEST_USER
    # Assert the response status code and content
    assert response.status_code == 401 # this is because TEST_USER is a regular user and cannot deploy
    assert (OwnerPSCmd.objects.count()==0)
    client.logout()


@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_token(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test owner grabs token and can queue a CNN
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    assert (OwnerPSCmd.objects.count()==0)
    assert (ClusterNumNode.objects.count()==0)
    # Decode the JSON response
    json_data = json.loads(response.content)    
    #logger.info(f"rsp:{json_data}")
    assert(json_data['access_lifetime']=='3600.0')   
    assert(json_data['refresh_lifetime']=='86400.0')   

#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_get_or_create_ClusterNumNodes(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test get_or_create_ClusterNumNodes
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    clusterObj = get_test_compute_cluster()
    clusterObj.cfg_asg.min = 2
    clusterObj.cfg_asg.num = 2
    clusterObj.cfg_asg.max = 7
    user = random_test_user()
    expire_tm = datetime.now(timezone.utc)+timedelta(hours=1)
    assert (ClusterNumNode.objects.count()==0)
    onn1,redundant,msg = get_or_create_ClusterNumNodes(orgAccountObj,user,3,expire_tm)
    assert(not redundant)
    assert(onn1)
    assert (ClusterNumNode.objects.count()==1)
    # test add redundant
    onn2,redundant,msg = get_or_create_ClusterNumNodes(orgAccountObj,user,3,expire_tm)
    assert(redundant)
    assert(onn2)
    assert(onn1==onn2)
    assert (ClusterNumNode.objects.count()==1)


#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_org_CNN_redundant(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test logic for redundant CNN requests
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    clusterObj = get_test_compute_cluster()
    
    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    assert (OwnerPSCmd.objects.count()==0)
    assert (ClusterNumNode.objects.count()==0)
    json_data = json.loads(response.content)
    logger.info(f"rsp:{json_data}")
    assert(json_data['access_lifetime']=='3600.0')   
    assert(json_data['refresh_lifetime']=='86400.0')
    access_token = json_data['access']   
    url = reverse('put-org-num-nodes',args=[orgAccountObj.name,3])
    
    response = client.put(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db()
    assert(NodeGroup.objects.count()==1)
    assert(ClusterNumNode.objects.count()==1)
    assert(OwnerPSCmd.objects.count()==0)
    json_data = json.loads(response.content)
    assert(json_data['status']=='QUEUED')   
    assert(json_data['msg']!='')   
    assert(json_data['error_msg']=='') 
    logger.info(f"msg:{json_data['msg']}")  
    
    url_ttl = reverse('post-num-nodes-ttl',args=[orgAccountObj.name,clusterObj.name,3,15])
    response = client.post(url_ttl,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db()
    assert(ClusterNumNode.objects.count()==2)
    assert(OwnerPSCmd.objects.count()==0)

    # Test duplicate request does not queue another cnn
    url = reverse('put-num-nodes',args=[orgAccountObj.name,clusterObj.name,3])
    response = client.put(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db()
    assert(ClusterNumNode.objects.count()==2) # Same as above because it's redundant
    json_data = json.loads(response.content)
    assert(json_data['status']=='REDUNDANT')   
    assert(OwnerPSCmd.objects.count()==0)

    # Test a new request with duplicate time does not create new clocked item
    url = reverse('put-num-nodes',args=[orgAccountObj.name,clusterObj.name,1])
    response = client.put(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db()
    assert(ClusterNumNode.objects.count()==3) 
    json_data = json.loads(response.content)
    assert(json_data['status']=='QUEUED')   
    assert(OwnerPSCmd.objects.count()==0)

    loop_count=0
    task_idle, loop_count = loop_iter(clusterObj,loop_count)
    clusterObj.refresh_from_db()
    orgAccountObj.refresh_from_db()
    assert(loop_count==1)
    assert(clusterObj.num_ps_cmd==1)
    assert(clusterObj.num_onn==1) # only process one of the entries
     # only process one here, wait for expire
    
#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_org_CNN_remove(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test the remove cnn api
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    clusterObj = get_test_compute_cluster()
    
    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    assert (OwnerPSCmd.objects.count()==0)
    assert (ClusterNumNode.objects.count()==0)
    json_data = json.loads(response.content)
    logger.info(f"rsp:{json_data}")
    assert(json_data['access_lifetime']=='3600.0')   
    assert(json_data['refresh_lifetime']=='86400.0')
    access_token = json_data['access']   
    url = reverse('put-org-num-nodes',args=[orgAccountObj.name,3])
    
    response = client.put(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db() # The client.put above updated the DB so we need this
    assert(NodeGroup.objects.count()==1)
    assert(ClusterNumNode.objects.count()==1)
    json_data = json.loads(response.content)
    assert(json_data['status']=='QUEUED')   
    assert(json_data['msg']!='')   
    assert(json_data['error_msg']=='') 
    logger.info(f"msg:{json_data['msg']}")  

    url_ttl = reverse('post-num-nodes-ttl',args=[orgAccountObj.name,clusterObj.name,3,15])
    response = client.post(url_ttl,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    assert(ClusterNumNode.objects.count()==2)
    assert(OwnerPSCmd.objects.count()==0)

    url_ttl = reverse('post-num-nodes-ttl',args=[orgAccountObj.name,clusterObj.name,4,15])
    response = client.post(url_ttl,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    assert(ClusterNumNode.objects.count()==3)
    assert(OwnerPSCmd.objects.count()==0)

    url_remove = reverse('remove-user-num-nodes-reqs',args=[orgAccountObj.name,clusterObj.name])
    response = client.put(url_remove,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    assert(ClusterNumNode.objects.count()==0)
    assert(OwnerPSCmd.objects.count()==0)

#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_orgNumNodes(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test the get org num nodes api/view
    '''
    caplog.set_level(logging.DEBUG)
    init_mock_ps_server()
    orgAccountObj = get_test_org()
    clusterObj = get_test_compute_cluster()
    clusterObj.is_deployed = True
    clusterObj.save()
    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    json_data = json.loads(response.content)
    access_token = json_data['access']   
    url = reverse('get-num-nodes',args=[orgAccountObj.name])
    response = client.get(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    assert(clusterObj.is_deployed)
    json_data = json.loads(response.content)
    logger.info(f"{json_data}")
    assert(json_data['status']=='SUCCESS')
    assert(json_data['min_nodes']== 0)
    assert(json_data['max_nodes']== 10)
    assert(json_data['current_nodes']== 0)
    #TBD how do we mock this?
    # assert(json_data['version']== 'latest')

    clusterObj.is_deployed = False
    clusterObj.save()

    response = client.get(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db() # The client.put above updated the DB so we need this
    assert(not clusterObj.is_deployed)
    json_data = json.loads(response.content)
    logger.info(f"{json_data}")
    assert(json_data['status']=='SUCCESS')
    assert(json_data['min_nodes']== 0)
    assert(json_data['max_nodes']== 10)
    assert(json_data['current_nodes']== 0)


#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_org_CNN_redundant_2(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test logic for deploy using CNN requests
    '''
    caplog.set_level(logging.DEBUG)
    init_mock_ps_server()
    orgAccountObj = get_test_org()
    clusterObj = get_test_compute_cluster()
    
    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    assert (OwnerPSCmd.objects.count()==0)
    assert (ClusterNumNode.objects.count()==0)
    json_data = json.loads(response.content)
    logger.info(f"rsp:{json_data}")
    assert(json_data['access_lifetime']=='3600.0')   
    assert(json_data['refresh_lifetime']=='86400.0')
    access_token = json_data['access']   
    url = reverse('put-org-num-nodes',args=[orgAccountObj.name,3])
    
    response = client.put(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db() # The client.put above updated the DB so we need this
    assert(NodeGroup.objects.count()==1)
    assert(ClusterNumNode.objects.count()==1)
    assert(OwnerPSCmd.objects.count()==0)
    json_data = json.loads(response.content)
    assert(json_data['status']=='QUEUED')   
    assert(json_data['msg']!='')   
    assert(json_data['error_msg']=='') 
    logger.info(f"msg:{json_data['msg']}")  
    
    url_ttl = reverse('post-num-nodes-ttl',args=[orgAccountObj.name,clusterObj.name,3,15])
    response = client.post(url_ttl,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    clusterObj.refresh_from_db() # The client.put above updated the DB so we need this
    orgAccountObj.refresh_from_db() # The client.put above updated the DB so we need this
    assert(ClusterNumNode.objects.count()==2)
    assert(OwnerPSCmd.objects.count()==0)


    loop_count=0
    clusterObj.num_ps_cmd=0
    clusterObj.num_ps_cmd_successful=0
    clusterObj.save()
    num_owner_ps_cmd=0
    num_onn=0
    task_idle, loop_count = loop_iter(clusterObj,loop_count)
    clusterObj.refresh_from_db()
    orgAccountObj.refresh_from_db()
    assert(loop_count==1)
    assert(clusterObj.num_ps_cmd==1) # cnn triggered a deploy
    assert(clusterObj.num_ps_cmd_successful==1) # cnn triggered a deploy
    assert(clusterObj.num_onn==1) # only process one of the entries
     # only process one here, wait for expire

    url = reverse('get-num-nodes',args=[orgAccountObj.name])
    response = client.get(url,headers={'Authorization': f"Bearer {access_token}"})
    assert (response.status_code == 200) 
    assert(clusterObj.is_deployed)
    json_data = json.loads(response.content)
    logger.info(f"{json_data}")
    assert(json_data['status']=='SUCCESS')
    assert(json_data['min_nodes']== 0)
    assert(json_data['max_nodes']== 10)
    assert(json_data['current_nodes']== 3)

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_sort_CNN_by_nn_exp(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test logic the routine sort_CNN_by_nn_exp
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    clusterObj = get_test_compute_cluster()
    
    response = client.post(reverse('org-token-obtain-pair'),data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    assert(response.status_code == 200)   
    json_data = json.loads(response.content)
    access_token = json_data['access']   
    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,3,17],
                                access_token=access_token,
                                data=None,
                                loop_count=0,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')

    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,4,16],
                                access_token=access_token,
                                data=None,
                                loop_count=0,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')

    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,5,15],
                                access_token=access_token,
                                data=None,
                                loop_count=0,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')
    
    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,5,25],
                                access_token=access_token,
                                data=None,
                                loop_count=0,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')

    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,5,20],
                                access_token=access_token,
                                data=None,
                                loop_count=0,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')

    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,5,21],
                                access_token=access_token,
                                data=None,
                                loop_count=0,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')

    loop_count,response = process_onn_api(client,
                                orgAccountObj,
                                datetime.now(timezone.utc),
                                view_name='post-num-nodes-ttl',
                                url_args=[orgAccountObj.name,clusterObj.name,5,18],
                                access_token=access_token,
                                loop_count=0,
                                data=None,
                                num_iters=0,
                                expected_change_ps_cmd=0,
                                expected_status='QUEUED')

    prev = None
    qs = sort_CNN_by_nn_exp(orgAccountObj)
    for cnn in qs:
        logger.info(format_onn(cnn))
        if cnn.expiration is not None:
            assert(cnn.expiration > datetime.now(timezone.utc))
        if prev:
            assert(cnn.desired_num_nodes <= prev.desired_num_nodes)
            if cnn.desired_num_nodes == prev.desired_num_nodes:
                if cnn.expiration is not None:
                    assert(cnn.expiration >= prev.expiration)
        prev = cnn 

def just_ONE_CASE(is_deployed, is_public_changes, version_changes, new_highest_onn_id):
    logger.info(f"is_deployed: {is_deployed}, is_public_changes: {is_public_changes}, version_changes: {version_changes}, new_highest_onn_id: {new_highest_onn_id}")

    orgAccountObj = get_test_org()
    clusterObj.cfg_asg.num=1
    clusterObj.is_public = True
    clusterObj.version = 'v2'
    orgAccountObj.save()
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    clusterObj.cfg_asg.num = sum_of_all_users_dnn # quiencent state
    clusterObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
    clusterObj.is_deployed = is_deployed
    clusterObj.cur_version = clusterObj.version+'a' if version_changes else clusterObj.version
    clusterObj.is_public = not clusterObj.is_public if is_public_changes else clusterObj.is_public
    clusterObj.save()

    expire_date = datetime.now(timezone.utc)+timedelta(minutes=16) 
    cnn = ClusterNumNode.objects.create(user=the_TEST_USER(),org=orgAccountObj, desired_num_nodes=clusterObj.cfg_asg.num,expiration=expire_date)
    logger.info(f"created BASE cnn:{cnn} ClusterNumNode.objects.count():{ClusterNumNode.objects.count()}")   
    clusterObj.cnnro_ids = []
    clusterObj.cnnro_ids.append(str(cnn.id))
    clusterObj.save() # same as orgAccount BASE

    expire_date = datetime.now(timezone.utc)+timedelta(hours=1)
    cnn = None
    if new_highest_onn_id:
        # this simulates a new call to the api
        onnTop = sort_CNN_by_nn_exp(orgAccountObj).first()
        new_desired_num_nodes = onnTop.desired_num_nodes+1 # new highest
        expire_date = datetime.now(timezone.utc)+timedelta(minutes=16) # before the one above!
        cnn = ClusterNumNode.objects.create(user=the_TEST_USER(),org=orgAccountObj, desired_num_nodes=new_desired_num_nodes,expiration=expire_date)
        logger.info(f"created new cnn:{cnn} ClusterNumNode.objects.count():{ClusterNumNode.objects.count()}")   
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    need_to_destroy = need_destroy_for_changed_version_or_is_public(orgAccountObj,sum_of_all_users_dnn)
    if not is_deployed:
        assert not need_to_destroy
    else:
        logger.info(f" ** is_deployed: {is_deployed}, is_public_changes: {is_public_changes}, version_changes: {version_changes}, new_highest_onn_id: {new_highest_onn_id}")
        if (is_public_changes or version_changes) and new_highest_onn_id:
            logger.info(f" ** cluster v:{clusterObj.cur_version} ip:{clusterObj.is_public} is_deployed:{clusterObj.is_deployed}")
            logger.info(f" ** org v:{clusterObj.version} ip:{clusterObj.is_public}")
            logger.info(f" ** cnnro_ids:{cnnro_ids} not same as clusterObj.cnnro_ids:{clusterObj.cnnro_ids} ?")
            assert need_to_destroy    
        else:
            assert not need_to_destroy


#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def  test_need_destroy_for_changed_version_or_is_public_ALL_CASES(caplog,create_TEST_USER,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test logic for forced Destroy if all combinations of these are met
    Changes to:
    | is_deployed | is_public | version |  new_highest_onn_id  | Need to destroy?
    |-------------|-----------|---------|--------------|
    | 0           | 0         | 0       | 0            |
    | 0           | 0         | 0       | 1            |
    | 0           | 0         | 1       | 0            |
    | 0           | 0         | 1       | 1            |
    | 0           | 1         | 0       | 0            |
    | 0           | 1         | 0       | 1            |
    | 0           | 1         | 1       | 0            |
    | 0           | 1         | 1       | 1            |
    | 1           | 0         | 0       | 0            |
    | 1           | 0         | 0       | 1            |
    | 1           | 0         | 1       | 0            |
    | 1           | 0         | 1       | 1            | YES
    | 1           | 1         | 0       | 0            |
    | 1           | 1         | 0       | 1            | YES
    | 1           | 1         | 1       | 0            |
    | 1           | 1         | 1       | 1            | YES

    '''

    variables = [False, True]  # Possible values for the boolean variables
    for is_deployed in variables:
        for is_public in variables:
            for version in variables:
                for new_highest_onn_id in variables:
                    just_ONE_CASE(is_deployed, is_public, version, new_highest_onn_id)

def verify_new_entries_in_CNN(orgAccountObj,client):

    # now verify different users can make different requests
    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_OWNER_USER(),
                                    password=OWNER_PASSWORD,
                                    desired_num_nodes=2,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    m = create_active_membership(orgAccountObj,the_DEV_TEST_USER())
    m.refresh_from_db()
    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_DEV_TEST_USER(),
                                    password=DEV_TEST_PASSWORD,
                                    desired_num_nodes=3,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    
    rtu = random_test_user()
    m = create_active_membership(orgAccountObj,rtu)
    m.refresh_from_db()
    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=rtu,
                                    password=TEST_PASSWORD,
                                    desired_num_nodes=1,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==6)
    assert(len(cnnro_ids)==3)
    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_DEV_TEST_USER(),
                                    password=DEV_TEST_PASSWORD,
                                    desired_num_nodes=2, # not highest
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=0)  # no change

    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==6) # still 6
    assert(len(cnnro_ids)==3)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_DEV_TEST_USER(),
                                    password=DEV_TEST_PASSWORD,
                                    desired_num_nodes=2, # same as before
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=0) # no change

    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==6) # still 6
    assert(len(cnnro_ids)==3)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_OWNER_USER(),
                                    password=OWNER_PASSWORD,
                                    desired_num_nodes=1,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=0) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==6) # still 6
    assert(len(cnnro_ids)==3)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=rtu,
                                    password=TEST_PASSWORD,
                                    desired_num_nodes=1,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=0) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==6) # still 6
    log_CNN()

    uuid_objects = [uuid.UUID(uuid_str) for uuid_str in cnnro_ids]
    for node in ClusterNumNode.objects.filter(id__in=uuid_objects):
        logger.info(f"cnnro_ids - {node.user.username} {node.desired_num_nodes} {node.expiration}")

    assert(len(uuid_objects)==4)
    assert(len(cnnro_ids)==4)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=rtu,
                                    password=TEST_PASSWORD,
                                    desired_num_nodes=2,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==7)
    assert(len(cnnro_ids)==3)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_OWNER_USER(),
                                    password=OWNER_PASSWORD,
                                    desired_num_nodes=3,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==8)
    assert(len(cnnro_ids)==3)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_DEV_TEST_USER(),
                                    password=DEV_TEST_PASSWORD,
                                    desired_num_nodes=3,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=0) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    log_CNN()
    uuid_objects = [uuid.UUID(uuid_str) for uuid_str in cnnro_ids]
    for node in ClusterNumNode.objects.filter(id__in=uuid_objects):
        logger.info(f"cnnro_ids - {node.user.username} {node.desired_num_nodes} {node.expiration}")

    assert(sum_of_all_users_dnn==8)
    assert(len(cnnro_ids)==4)

    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_DEV_TEST_USER(),
                                    password=DEV_TEST_PASSWORD,
                                    desired_num_nodes=4,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)

    log_CNN()

    uuid_objects = [uuid.UUID(uuid_str) for uuid_str in cnnro_ids]
    for node in ClusterNumNode.objects.filter(id__in=uuid_objects):
        logger.info(f"cnnro_ids - {node.user.username} {node.desired_num_nodes} {node.expiration}")

    assert(sum_of_all_users_dnn==9)
    assert(len(cnnro_ids)==3)



def verify_sum_of_highest_nodes_for_each_user_default_test_org(orgAccountObj,client):

    #   First configure the default test org
    url = reverse('org-token-obtain-pair')
    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    json_data = json.loads(response.content)
    logger.info(f"rsp:{json_data}")
    assert(json_data['access_lifetime']=='3600.0')   
    assert(json_data['refresh_lifetime']=='86400.0')   

    headers = {
        'Authorization': f"Bearer {json_data['access']}",
        'Accept': 'application/json'  # Specify JSON response
    }

    url = reverse('cluster-cfg',args=[orgAccountObj.name,0,clusterObj.admin_max_node_cap])
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    orgAccountObj.refresh_from_db()
    assert(response.status_code == 200)   
    assert(clusterObj.cfg_asg.min == 0)
    assert(clusterObj.cfg_asg.max == clusterObj.admin_max_node_cap)

    verify_new_entries_in_CNN(orgAccountObj=orgAccountObj,client=client)



#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_sum_of_highest_nodes_for_each_user(caplog,client, mock_email_backend, initialize_test_environ, developer_TEST_USER):
    '''
        This procedure will test logic for sum_of_highest_nodes_for_each_user for two different orgs
    '''

    verify_sum_of_highest_nodes_for_each_user_default_test_org(get_test_org(),client)

    # create a new org and initialize like init_test_environ and then mix it up 
    form = ClusterCfgForm(data={
                        'name': 'test_create',
                        'owner': the_OWNER_USER(), # use same as sliderule org
                        'point_of_contact_name': 'test point of contact here',
                        'email': OWNER_EMAIL, 
                        'max_allowance':5000,
                        'monthly_allowance':1000,
                        'balance':500,
                        'admin_max_node_cap':10})
    valid = form.is_valid() 
    logger.info(f"form_errors:{form.errors.as_data()}")
    assert(valid)
    for app in Application.objects.all():
        logger.info(f"name:{app.name} uris:{app.redirect_uris}")      
    assert Application.objects.count() == 1 
    orgAccountObj,msg,emsg,p = add_org_cluster_orgcost(form)
    logger.info(f"msg:{msg} emsg:{emsg} ")
    assert(emsg=='')
    assert('Owner TestUser (ownertestuser) now owns new org/cluster:test_create' in msg)
    assert(call_SetUp(orgAccountObj))
    assert(fake_sync_clusterObj_to_orgAccountObj(orgAccountObj))
    clusterObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
    logger.info(f"org:{orgAccountObj.name} provision_env_ready:{clusterObj.provision_env_ready} clusterObj.cur_version:{clusterObj.cur_version} clusterObj.version:{clusterObj.version} ")       
    assert clusterObj.cur_version == clusterObj.version
    log_CNN()
    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==0) # for new org
    
    verify_new_entries_in_CNN(orgAccountObj=orgAccountObj,client=client)
    cnn = log_CNN()
    assert len(cnn) == 22
    assert cnn[0].desired_num_nodes == 4
    assert cnn[11].desired_num_nodes == 4


    # now test out of bounds requests, and see that it is clamped

    # now verify different users can make different requests
    LARGE_REQ = clusterObj.cfg_asg.max+5
    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=the_OWNER_USER(),
                                    password=OWNER_PASSWORD,
                                    desired_num_nodes=LARGE_REQ,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 

    sum_of_all_users_dnn,cnnro_ids = sum_of_highest_nodes_for_each_user(clusterObj)
    assert(sum_of_all_users_dnn==clusterObj.cfg_asg.max) # clamped

    cnn = log_CNN()
    assert len(cnn) == 23
    assert cnn[0].desired_num_nodes == 4
    assert cnn[5].desired_num_nodes == LARGE_REQ
