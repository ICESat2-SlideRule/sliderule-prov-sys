import pytest
import logging
import sys
import os
import pathlib
import json
from importlib import import_module
from decimal import *
from django.urls import reverse
from users.models import Membership,OwnerPSCmd,OrgAccount,ClusterNumNode,NodeGroup
from users.tests.utilities_for_unit_tests import init_test_environ,get_test_org,get_test_compute_cluster,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user
from users.tasks import loop_iter
# Import the fixtures
from users.tests.utilities_for_unit_tests import TEST_USER,TEST_PASSWORD,DEV_TEST_USER,DEV_TEST_PASSWORD,TEST_ORG_NAME
from users.models import OwnerPSCmd,ClusterNumNode,OrgAccount,PsCmdResult

module_name = 'views'
# discover the src directory to import the file being tested
parent_dir = pathlib.Path(__file__).parent.resolve()
src_path = os.path.join(parent_dir, '..', '')
sys.path.append(src_path)

# setup logging to terminal
#level = logging.DEBUG
level = logging.INFO
#logger = logging.getLogger(__name__)
logger = logging.getLogger('django')
logger.setLevel(level)
logger.info(f"parent_dir:{parent_dir}src_path:{src_path}")


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
@pytest.mark.ps_server_stubbed
def test_no_token(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure response when no token provided 
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    url = reverse('post-num-nodes-ttl',args=[orgAccountObj.name,nodeGroupObj.name,3,15])

    response = client.post(url)
    assert (response.status_code == 400)   # no token was provided

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_token(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test owner grabs token and can queue a CNN
    '''
    caplog.set_level(logging.DEBUG)
    
    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':TEST_ORG_NAME})
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
def test_org_CNN_ttl(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test owner grabs token and can queue a CNN
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    
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

    loop_count=0
    nodeGroupObj.num_owner_ps_cmd=0
    nodeGroupObj.num_ps_cmd=0
    nodeGroupObj.num_ps_cmd_successful=0
    nodeGroupObj.num_onn=0
    nodeGroupObj.save()

    url = reverse('post-num-nodes-ttl',args=[orgAccountObj.name,nodeGroupObj.name,3,15])    

    response = client.post(url,headers={'Authorization': f"Bearer {json_data['access']}"})
    assert (response.status_code == 200) 
    json_data = json.loads(response.content)
    assert(json_data['status']=='QUEUED')   
    assert(json_data['msg']!='')   
    assert(json_data['error_msg']=='') 
    logger.info(f"msg:{json_data['msg']}")  
    nodeGroupObj.refresh_from_db() # The client.post above updated the DB so we need this
    
    task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
    nodeGroupObj.refresh_from_db()
    assert(loop_count==1)
    assert(nodeGroupObj.provision_env_ready)
    assert(nodeGroupObj.provisioning_suspended==False)
    assert(nodeGroupObj.num_ps_cmd==1) # cnn triggered update
    assert(nodeGroupObj.num_ps_cmd_successful==1) 
    assert(nodeGroupObj.num_onn==1)
    

    task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
    nodeGroupObj.refresh_from_db()
    orgAccountObj.refresh_from_db()
    assert(task_idle)
    assert(loop_count==2)   

    assert PsCmdResult.objects.count() == 1 # Update 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=nodeGroupObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[0].ps_cmd_summary_label

    assert(nodeGroupObj.provisioning_suspended==False)
    assert(nodeGroupObj.num_ps_cmd==1)
    assert(nodeGroupObj.num_ps_cmd_successful==1) 
    assert(nodeGroupObj.num_onn==1)
    

#@pytest.mark.dev
@pytest.mark.django_db 
@pytest.mark.ps_server_stubbed
def test_org_CNN(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test owner grabs token and can queue a CNN
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    
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

    loop_count=0
    nodeGroupObj.num_owner_ps_cmd=0
    nodeGroupObj.num_ps_cmd=0
    nodeGroupObj.num_ps_cmd_successful=0
    nodeGroupObj.num_onn=0
    nodeGroupObj.save()

    url = reverse('put-num-nodes',args=[orgAccountObj.name,nodeGroupObj.name,3])
  
    response = client.put(url,headers={'Authorization': f"Bearer {json_data['access']}"})
    assert (response.status_code == 200) 
    json_data = json.loads(response.content)
    assert(json_data['status']=='QUEUED')   
    assert(json_data['msg']!='')   
    assert(json_data['error_msg']=='') 
    logger.info(f"msg:{json_data['msg']}")  
    nodeGroupObj.refresh_from_db() # The client.put above updated the DB so we need this
    
    
  
    task_idle, loop_count = loop_iter(nodeGroupObj,loop_count)
    nodeGroupObj.refresh_from_db()
    orgAccountObj.refresh_from_db()
    assert(loop_count==1)
    assert(nodeGroupObj.provision_env_ready)
    assert(nodeGroupObj.provisioning_suspended==False)
    assert(nodeGroupObj.num_ps_cmd==1) # cnn triggered update
    assert(nodeGroupObj.num_ps_cmd_successful==1) 
    assert(nodeGroupObj.num_onn==1)
    

