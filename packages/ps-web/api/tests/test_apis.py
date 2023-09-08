import pytest
import logging
import sys
import os
import pathlib
import uuid
import json
from importlib import import_module
from datetime import datetime, timezone, timedelta
from decimal import *
from django.urls import reverse,resolve
from users.tests.utilities_for_unit_tests import init_test_environ,get_test_org,get_test_compute_cluster,initialize_test_org
from users.tests.utilities_for_unit_tests import TEST_EMAIL,TEST_ORG_NAME,TEST_PASSWORD,TEST_USER,DEV_TEST_EMAIL,DEV_TEST_PASSWORD,DEV_TEST_USER,OWNER_USER,OWNER_PASSWORD,OWNER_EMAIL

from users.models import Membership,NodeGroup,OrgAccount
from users.tasks import init_new_org_memberships

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
    logger.info('------')
    

# teardown after running all tests 
def teardown_module(tasks_module):
    logger.info('------')


#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_config_cluster(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test the config min-max nodes api
    '''
    caplog.set_level(logging.DEBUG)
    
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()

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
    # negative test POST
    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,0,5])
    response = client.post(url)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    assert (response.status_code == 405)  

    # negative test no Token

    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,0,5])
    response = client.put(url)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    assert (response.status_code == 400)  

    # now test config with valid values
    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,0,5])
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    orgAccountObj.refresh_from_db()
    nodeGroupObj.refresh_from_db()
    assert(response.status_code == 200)   
    assert(nodeGroupObj.cfg_asg.min == 0)
    assert(nodeGroupObj.cfg_asg.max == 5)

    # now test config with valid values

    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,0,nodeGroupObj.admin_max_node_cap])
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    orgAccountObj.refresh_from_db()
    nodeGroupObj.refresh_from_db()
    assert(response.status_code == 200)   
    assert(nodeGroupObj.cfg_asg.min == 0)
    assert(nodeGroupObj.cfg_asg.max == nodeGroupObj.admin_max_node_cap)

    # now test config with INVALID: max is 0

    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,0,0])
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    assert (response.status_code == 400)

    # now test config with INVALID: max is max_admin + 1
    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,0,nodeGroupObj.cfg_asg.max+1])
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    assert (response.status_code == 400)

    # now test config with INVALID: min is -1
    name = get_test_org().name
    min_nodes = -1  # Provide an invalid value for min_nodes
    max_nodes = 5
    # Manually construct the URL with an invalid value for min_nodes
    url = f"/org_config/{name}/{min_nodes}/{max_nodes}/"
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 404)

    # now test config with INVALID: max < min
    url = reverse('cluster-cfg',args=[get_test_org().name,nodeGroupObj.name,2,1])
    response = client.put(url,headers=headers)
    logger.info(f"status:{response.status_code} response:{response.json()}")
    assert (response.status_code == 400)

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_membership_status(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test the membership status api
    '''
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()

    url = reverse('org-token-obtain-pair')

    response = client.post(url,data={'username':OWNER_USER,'password':OWNER_PASSWORD, 'org_name':orgAccountObj.name})
    logger.info(f"status:{response.status_code}")
    assert (response.status_code == 200)   
    json_data = json.loads(response.content)

    headers = {
        'Authorization': f"Bearer {json_data['access']}",
        'Accept': 'application/json'  # Specify JSON response
    }

    url = reverse('get-membership-status',args=[get_test_org().name])
    response = client.get(url,headers=headers)
    rsp_body = response.json()
    logger.info(f"status:{response.status_code} response:{rsp_body}")
    orgAccountObj.refresh_from_db()
    nodeGroupObj.refresh_from_db()
    assert(response.status_code == 200)
    assert(rsp_body['active'] == True)   

    url = reverse('get-membership-status',args=[get_test_org().name+"BadOrgName"])
    response = client.get(url,headers=headers)
    rsp_body = response.json()
    logger.info(f"status:{response.status_code} response:{rsp_body}")
    orgAccountObj.refresh_from_db()
    nodeGroupObj.refresh_from_db()
    assert(response.status_code == 400)
    assert(rsp_body['status'] == 'FAILED')
    assert('Unknown org' in rsp_body['error_msg'])   

    PUBLIC_ORG = 'unit-test-public'
    new_test_org,owner = initialize_test_org(PUBLIC_ORG,org_owner=orgAccountObj.owner,is_public=True,max_allowance=10000,monthly_allowance=100,balance=1000)

 
    # org exists but is NOT the one in the token claim, user NOT a member
    url = reverse('get-membership-status',args=[PUBLIC_ORG])
    response = client.get(url,headers=headers)
    rsp_body = response.json()
    logger.info(f"status:{response.status_code} response:{rsp_body}")
    orgAccountObj.refresh_from_db()
    nodeGroupObj.refresh_from_db()
    assert(response.status_code == 400)
    assert(rsp_body['status'] == 'FAILED')
    emsg = f"Token claim org:{orgAccountObj.name} does not match organization given:{PUBLIC_ORG} "
    assert(emsg in rsp_body['error_msg'])   
