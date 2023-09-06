from users.tests.global_test import GlobalTestCase
import pytest
import logging
import logging
import sys
import os
import pathlib
from importlib import import_module
from datetime import datetime, timezone, timedelta
from users.tests.utilities_for_unit_tests import get_test_org,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user,verify_user,process_onn_api,process_cluster_configure,log_CNN,create_active_membership,verify_api_user_makes_onn_ttl,get_test_compute_cluster
from users.tasks import loop_iter
from users.models import OwnerPSCmd,ClusterNumNode,OrgAccount,PsCmdResult,NodeGroup
from django.urls import reverse
import time_machine
import json
from django.contrib.messages import get_messages

from users.global_constants import *

LOG = logging.getLogger('django')

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


## a unit test class here
class ViewsTestCase(GlobalTestCase):

    def setUp(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        return super().setUp()

    def tearDown(self):
        #LOG.info(f"{__name__}({self}) ... ------------------")
        return super().tearDown()   


    def test_index_loads_properly(self):
        """The index page loads properly"""
        #LOG.info(f"{__name__}({self}) ... ------------------")
        response = self.client.get('http://localhost/', follow=True)
        self.assertEqual(response.status_code, 200)

# 
#  -------  pytest stuff follows --------
#

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_adding_an_num_node(caplog,client,mock_email_backend,initialize_test_environ):
    '''
        This procedure will test a privileged user can queue a deploy cmd
    '''

    with time_machine.travel(datetime(year=2023, month=1, day=28,hour=11),tick=False):

        assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))
        
        logger.info(f"will now add cnn by post of submit_value add_onn")
        form_prefix = 'add_onn-'
        url = reverse('org-manage-cluster',args=[get_test_org().id,])
        # setup necessary form data
        ttl_to_test = 20
        form_data = {
            f'{form_prefix}ttl_minutes': ttl_to_test,
            f'{form_prefix}desired_num_nodes': 1,
            f'form_submit': 'add_onn',
        }

        response = client.post(url,form_data)
        logger.info(f"status:{response.status_code}")
        # assert the form was successful
        assert response.status_code == 200 or response.status_code == 302
        assert OwnerPSCmd.objects.count() == 0
        assert ClusterNumNode.objects.count() == 1
        cnn = ClusterNumNode.objects.first()
        assert cnn is not None
        assert cnn.desired_num_nodes == 1
        
        assert cnn.expiration is not None
        assert cnn.org == get_test_org() 

        assert cnn.expiration == datetime.now(timezone.utc) + timedelta(minutes=ttl_to_test)


#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_num_node_form_invalid_ttl_too_low(caplog,client,mock_email_backend,initialize_test_environ):

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))
    
    # setup necessary form data
    form_data = {
        'ttl_minutes': MIN_TTL-1,
        'desired_num_nodes': 1,
        'form_submit': 'add_onn',
    }
    # get the url
    url = reverse('org-manage-cluster', args=[get_test_org().id])
    # send the POST request
    response = client.post(url, form_data)
    # assert the form was successful
    assert response.status_code == 200 or response.status_code == 302
    assert OwnerPSCmd.objects.count() == 0
    assert ClusterNumNode.objects.count() == 0

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_num_node_form_invalid_ttl_too_high(caplog,client,mock_email_backend,initialize_test_environ):

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))
    
    # setup necessary form data
    form_data = {
        'ttl_minutes': MAX_TTL+1,
        'desired_num_nodes': 1,
        'form_submit': 'add_onn',
    }
    # get the url
    url = reverse('org-manage-cluster', args=[get_test_org().id])
    # send the POST request
    response = client.post(url, form_data)
    # assert the form was successful
    assert response.status_code == 200 or response.status_code == 302
    assert OwnerPSCmd.objects.count() == 0
    assert ClusterNumNode.objects.count() == 0

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_reg_user_access_negative_test(caplog,client,mock_email_backend,initialize_test_environ):
    user = verify_user(random_test_user())
    assert client.login(username=user.username, password=TEST_PASSWORD)
    assert not user.groups.filter(name='PS_Developer').exists()
    assert get_test_org().owner != user
    # get the url
    url = reverse('org-manage-cluster', args=[get_test_org().id])
    # send the POST request
    response = client.get(url)
    assert response.status_code == 401

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_account_cfg(caplog,client,mock_email_backend,initialize_test_environ):
    assert OrgAccount.objects.count() == 1
    
    clusterObj = get_test_compute_cluster()
    assert(not clusterObj.is_public) 
    assert(clusterObj.version == 'latest') 

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))
    
    # setup necessary form data
    form_data = {
        'is_public': True,
        'version': 'v3',
        'min_node_cap': 1,
        'max_node_cap': 10,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }
    # get the url
    url = reverse('org-configure', args=[clusterObj.id])
    # send the POST request
    response = client.post(url, data=form_data)
    # assert the form was successful
    # refresh the OrgAccount object
    clusterObj = get_test_compute_cluster()
    assert response.status_code == 200 or response.status_code == 302
    assert(clusterObj.is_public) 
    assert(clusterObj.version == 'v3') 

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_destroy_cluster_only_one(caplog,client,mock_email_backend,initialize_test_environ):
    assert OrgAccount.objects.count() == 1
    
    clusterObj = get_test_compute_cluster()
    assert(not clusterObj.is_public) # fixture default
    assert(clusterObj.version == 'latest')  # fixture default

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))
   
    # get the url
    url = reverse('org-destroy-cluster', args=[org_account_id])
    # send the POST request
    # Test that only one gets queued no matter how many times you try
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_refresh_cluster_only_one(caplog,client,mock_email_backend,initialize_test_environ):
    assert OrgAccount.objects.count() == 1
    
    clusterObj = get_test_compute_cluster()
    assert(not clusterObj.is_public) 
    assert(clusterObj.version == 'latest') 

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))
   
    # get the url
    url = reverse('org-refresh-cluster', args=[org_account_id])
    # send the POST request
    # Test that only one gets queued no matter how many times you try

    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    
    response = client.post(url)
    assert response.status_code in (200, 302) # redirects to org-manage-cluster # redirects to org-manage-cluster
    assert OwnerPSCmd.objects.count() == 1    

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize('initialize_test_environ', [{'version': 'latest', 'is_public': False}, {'version': 'v3', 'is_public': True}], indirect=True)
def test_change_version_with_user_view(setup_logging, client,initialize_test_environ):
    logger = setup_logging
    
    clusterObj = get_test_compute_cluster()
    
    assert(clusterObj.version == clusterObj.cur_version) # ensure initialization is correct 
    initial_version = clusterObj.version
    initial_is_public = clusterObj.is_public
    if initial_version == 'latest':
        new_version = 'v3' 
    elif initial_version == 'v3':
        new_version = 'latest'
    else:
        assert False, f"initial_version:{initial_version} not supported"

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert OrgAccount.objects.count() == 1
    orgAccountObj.save()

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert clusterObj.num_setup_cmd == 0
    assert clusterObj.num_setup_cmd_successful == 0
    assert clusterObj.num_ps_cmd_successful == 0
    assert clusterObj.num_ps_cmd == 0
    assert clusterObj.num_onn == 0

    # setup necessary form data
    form_data = {
        'is_public': initial_is_public,
        'version': initial_version, # First time we use the current version
        'min_node_cap': 1,
        'max_node_cap': 3,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }

    loop_count = process_cluster_configure( client,
                                            clusterObj,
                                            new_time=datetime.now(timezone.utc),
                                            view_name='org-configure',
                                            url_args=[org_account_id],
                                            data=form_data,
                                            loop_count=0,
                                            num_iters=3,
                                            expected_change_ps_cmd=2 # SetUp - Update (min nodes is 1)
                                            )

    # assert the form was successful
    # refresh the OrgAccount object
    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == initial_is_public) 
    assert(clusterObj.version == initial_version) 
    assert clusterObj.num_setup_cmd == 1
    assert clusterObj.num_setup_cmd_successful == 1
    assert clusterObj.num_ps_cmd_successful == 2
    assert clusterObj.num_ps_cmd == 2
    assert clusterObj.num_onn == 1
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 3
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True


    assert PsCmdResult.objects.count() == 2 # SetUp - Refresh 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label # no entries and min_node_cap is 1 so Update


    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 3,
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=1,
                                expected_status='QUEUED')

    assert PsCmdResult.objects.count() == 3 # SetUp - Update - Update
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}") # update to 3
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label

    # setup necessary form data
    form_data = {
        'is_public': initial_is_public,
        'version': new_version,
        'min_node_cap': 1,
        'max_node_cap': 10,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }
    loop_count = process_cluster_configure(client,
                                        orgAccountObj,
                                        new_time=datetime.now(timezone.utc),
                                        view_name='org-configure',
                                        url_args=[org_account_id],
                                        data=form_data,
                                        loop_count=loop_count,
                                        num_iters=3,
                                        expected_change_ps_cmd=2 # SetUp - Refresh 
                                        ) 

    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == initial_is_public) 
    assert(clusterObj.version == new_version) 
    assert clusterObj.num_setup_cmd == 2
    assert clusterObj.num_setup_cmd_successful == 2
    assert clusterObj.num_ps_cmd_successful == 5
    assert clusterObj.num_ps_cmd == 5
    assert clusterObj.num_onn == 2
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 10
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True


    assert PsCmdResult.objects.count() == 5 # SetUp - Refresh - Update - SetUp - Refresh 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label
    logger.info(f"[3]:{psCmdResultObjs[3].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[3].ps_cmd_summary_label
    logger.info(f"[4]:{psCmdResultObjs[4].ps_cmd_summary_label}")
    assert 'Refresh' in psCmdResultObjs[4].ps_cmd_summary_label

    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 3, # NOTE: we did NOT change this here
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3,
                                expected_change_ps_cmd=0, # same desired_num_nodes so no change
                                expected_status='QUEUED')

    assert clusterObj.num_onn == 2
    assert clusterObj.cfg_asg.num == 3 # no change
    assert PsCmdResult.objects.count() == 5 # NO CHANGE - SetUp - Update - Update - SetUp - Destroy - Update
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label
    logger.info(f"[3]:{psCmdResultObjs[3].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[3].ps_cmd_summary_label
    logger.info(f"[4]:{psCmdResultObjs[4].ps_cmd_summary_label}")
    assert 'Refresh' in psCmdResultObjs[4].ps_cmd_summary_label

    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 4, # This triggers a change!
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=2, # different desired_num_nodes Destroy Update
                                expected_org_account_num_cnn_change=1, # Destroy is inline with the Update
                                expected_status='QUEUED')
    assert clusterObj.num_onn == 3
    assert PsCmdResult.objects.count() == 7 # SetUp - Refresh - Update - SetUp - Destroy - Update - Update
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label
    logger.info(f"[3]:{psCmdResultObjs[3].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[3].ps_cmd_summary_label
    logger.info(f"[4]:{psCmdResultObjs[4].ps_cmd_summary_label}")
    assert 'Refresh' in psCmdResultObjs[4].ps_cmd_summary_label
    logger.info(f"[5]:{psCmdResultObjs[5].ps_cmd_summary_label}")
    assert 'Destroy' in psCmdResultObjs[5].ps_cmd_summary_label
    logger.info(f"[6]:{psCmdResultObjs[6].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[6].ps_cmd_summary_label

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize('initialize_test_environ', [{'version': 'latest', 'is_public': False}, {'version': 'v3', 'is_public': True}], indirect=True)
def test_change_is_public_with_user_view(setup_logging, client,initialize_test_environ):
    logger = setup_logging
    
    clusterObj = get_test_compute_cluster()
    
    assert(clusterObj.is_deployed == False)
    assert(clusterObj.version == clusterObj.cur_version) # ensure initialization is correct 
    initial_version = clusterObj.version
    new_version = initial_version
    initial_is_public = clusterObj.is_public
    if initial_is_public:
        new_is_public = False
    else:
        new_is_public = True

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert OrgAccount.objects.count() == 1
    orgAccountObj.save()

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert clusterObj.num_setup_cmd == 0
    assert clusterObj.num_setup_cmd_successful == 0
    assert clusterObj.num_ps_cmd_successful == 0
    assert clusterObj.num_ps_cmd == 0
    assert clusterObj.num_onn == 0

    # setup necessary form data
    form_data = {
        'is_public': initial_is_public,
        'version': initial_version, # First time we use the current version
        'min_node_cap': 1,
        'max_node_cap': 3,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }

    loop_count = process_cluster_configure(client,
                                        orgAccountObj,
                                        new_time=datetime.now(timezone.utc),
                                        view_name='org-configure',
                                        url_args=[org_account_id],
                                        data=form_data,
                                        loop_count=0,
                                        num_iters=3,
                                        expected_change_ps_cmd=2 # SetUp - Update (min nodes is 1)
                                        )

    # assert the form was successful
    # refresh the OrgAccount object
    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == initial_is_public)
    assert(clusterObj.version == initial_version) 
    assert clusterObj.num_setup_cmd == 1
    assert clusterObj.num_setup_cmd_successful == 1
    assert clusterObj.num_ps_cmd_successful == 2
    assert clusterObj.num_ps_cmd == 2
    assert clusterObj.num_onn == 1
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 3
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True


    assert PsCmdResult.objects.count() == 2 # SetUp - Update (min_node_cap is 1)
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label


    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 3,
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=1, # Update (to 3)
                                expected_status='QUEUED')

    assert PsCmdResult.objects.count() == 3 # SetUp - Update (to 1) - Update (to 3)
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label

    # setup necessary form data
    form_data = {
        'is_public': new_is_public,
        'version': new_version,
        'min_node_cap': 1,
        'max_node_cap': 10,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }
    loop_count = process_cluster_configure(client,
                                        orgAccountObj,
                                        new_time=datetime.now(timezone.utc),
                                        view_name='org-configure',
                                        url_args=[org_account_id],
                                        data=form_data,
                                        loop_count=loop_count,
                                        num_iters=3,
                                        expected_change_ps_cmd=2 # SetUp - Refresh
                                        ) 

    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == new_is_public) 
    assert(clusterObj.version == new_version) 
    assert clusterObj.num_setup_cmd == 2
    assert clusterObj.num_setup_cmd_successful == 2
    assert clusterObj.num_ps_cmd_successful == 5
    assert clusterObj.num_ps_cmd == 5
    assert clusterObj.num_onn == 2
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 10
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True


    assert PsCmdResult.objects.count() == 5 # SetUp - Refresh - Destroy - Update (to 3) - SetUp - Refresh 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label
    logger.info(f"[3]:{psCmdResultObjs[3].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[3].ps_cmd_summary_label
    logger.info(f"[4]:{psCmdResultObjs[4].ps_cmd_summary_label}")
    assert 'Refresh' in psCmdResultObjs[4].ps_cmd_summary_label

    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 3, # NOTE: we did NOT change this here
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3,
                                expected_change_ps_cmd=0, # same desired_num_nodes so no change
                                expected_status='QUEUED')

    assert clusterObj.num_onn == 2

    assert clusterObj.cfg_asg.num == 3 # no change
    assert PsCmdResult.objects.count() == 5
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label
    logger.info(f"[3]:{psCmdResultObjs[3].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[3].ps_cmd_summary_label
    logger.info(f"[4]:{psCmdResultObjs[4].ps_cmd_summary_label}")
    assert 'Refresh' in psCmdResultObjs[4].ps_cmd_summary_label

    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 4, # This triggers a change!
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=2, # different Destroy Update
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=1) # Destroy is inline so only 1
    assert clusterObj.num_onn == 3
    assert PsCmdResult.objects.count() == 7 # + Destroy Update (new desired_num_nodes)
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label
    logger.info(f"[2]:{psCmdResultObjs[2].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[2].ps_cmd_summary_label
    logger.info(f"[3]:{psCmdResultObjs[3].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[3].ps_cmd_summary_label
    logger.info(f"[4]:{psCmdResultObjs[4].ps_cmd_summary_label}")
    assert 'Refresh' in psCmdResultObjs[4].ps_cmd_summary_label
    logger.info(f"[5]:{psCmdResultObjs[5].ps_cmd_summary_label}")
    assert 'Destroy' in psCmdResultObjs[5].ps_cmd_summary_label
    logger.info(f"[6]:{psCmdResultObjs[6].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[6].ps_cmd_summary_label

    assert clusterObj.cfg_asg.num == 4    

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_web_user_desired_num_nodes(caplog, setup_logging, client, mock_email_backend, initialize_test_environ, developer_TEST_USER):
    '''
        This procedure will test logic add num nodes from the org-manage-cluster web page
    '''
    logger = setup_logging
    
    clusterObj = get_test_compute_cluster()
    
    assert(clusterObj.is_deployed == False)
    assert(clusterObj.version == clusterObj.cur_version) # ensure initialization is correct 
    initial_version = clusterObj.version
    new_version = initial_version
    initial_is_public = clusterObj.is_public

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert OrgAccount.objects.count() == 1
    orgAccountObj.save()

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert clusterObj.num_setup_cmd == 0
    assert clusterObj.num_setup_cmd_successful == 0
    assert clusterObj.num_ps_cmd_successful == 0
    assert clusterObj.num_ps_cmd == 0
    assert clusterObj.num_onn == 0

    # setup necessary form data
    form_data = {
        'is_public': initial_is_public,
        'version': initial_version, # First time we use the current version
        'min_node_cap': 1,
        'max_node_cap': 3,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }

    loop_count = process_cluster_configure(client,
                                        orgAccountObj,
                                        new_time=datetime.now(timezone.utc),
                                        view_name='org-configure',
                                        url_args=[org_account_id],
                                        data=form_data,
                                        loop_count=0,
                                        num_iters=3,
                                        expected_change_ps_cmd=2 # SetUp - Update (min nodes is 1)
                                        )
    # assert the form was successful
    # refresh the OrgAccount object
    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == initial_is_public) 
    assert(clusterObj.version == initial_version) 
    assert clusterObj.num_setup_cmd == 1
    assert clusterObj.num_setup_cmd_successful == 1
    assert clusterObj.num_ps_cmd_successful == 2
    assert clusterObj.num_ps_cmd == 2
    assert clusterObj.num_onn == 1
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 3
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True
    assert PsCmdResult.objects.count() == 2 # SetUp - Refresh 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label # no entries and min_node_cap is 1 so Update
    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': -1, # Error
        'add_onn-ttl_minutes': 15,
    }
    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=0, # Error
                                expected_status='FAILED',
                                expected_org_account_num_cnn_change=0,
                                expected_html_status=200) # But error message is displayed
    # Get all messages from the response context
    stored_messages = [m.message for m in get_messages(response.wsgi_request)]
    assert any("Input Errors:" in message for message in stored_messages)

    # test clamp to minimum
    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 0, 
        'add_onn-ttl_minutes': 15,
    }

    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=0,# already set to min (i.e. 1) so no cmd issued
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=0)  # entry queued
# test clamp to maximum
    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 20, 
        'add_onn-ttl_minutes': 15,
    }

    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=1, # update to max (i.e. 3)
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=1) # changed; Clamped desired_num_nodes to max so no change 

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_web_user_clear_num_nodes(caplog, setup_logging, client, mock_email_backend, initialize_test_environ, developer_TEST_USER):
    '''
        This procedure will test logic clear num nodes from the org-manage-cluster web page
    '''
    logger = setup_logging
    
    clusterObj = get_test_compute_cluster()
    
    assert(clusterObj.is_deployed == False)
    assert(clusterObj.version == clusterObj.cur_version) # ensure initialization is correct 
    initial_version = clusterObj.version
    new_version = initial_version
    initial_is_public = clusterObj.is_public

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert OrgAccount.objects.count() == 1
    orgAccountObj.save()

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert clusterObj.num_setup_cmd == 0
    assert clusterObj.num_setup_cmd_successful == 0
    assert clusterObj.num_ps_cmd_successful == 0
    assert clusterObj.num_ps_cmd == 0
    assert clusterObj.num_onn == 0

    # setup necessary form data
    form_data = {
        'is_public': initial_is_public,
        'version': initial_version, # First time we use the current version
        'min_node_cap': 1,
        'max_node_cap': 3,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }

    loop_count = process_cluster_configure(client,
                                        orgAccountObj,
                                        new_time=datetime.now(timezone.utc),
                                        view_name='org-configure',
                                        url_args=[org_account_id],
                                        data=form_data,
                                        loop_count=0,
                                        num_iters=3,
                                        expected_change_ps_cmd=2 # SetUp - Update (min nodes is 1)
                                        )
    # assert the form was successful
    # refresh the OrgAccount object
    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == initial_is_public) 
    assert(clusterObj.version == initial_version) 
    assert clusterObj.num_setup_cmd == 1
    assert clusterObj.num_setup_cmd_successful == 1
    assert clusterObj.num_ps_cmd_successful == 2
    assert clusterObj.num_ps_cmd == 2
    assert clusterObj.num_onn == 1
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 3
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True
    assert PsCmdResult.objects.count() == 2 # SetUp - Refresh 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label # no entries and min_node_cap is 1 so Update
    assert ClusterNumNode.objects.count() == 0

    # test clamp to minimum
    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 1, 
        'add_onn-ttl_minutes': 15,
    }

    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=0,# already set to min (i.e. 1) so no cmd issued
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=0)  # this counter is num cnn processed not queued
    log_CNN()
    assert ClusterNumNode.objects.count() == 1


    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 2, 
        'add_onn-ttl_minutes': 15,
    }

    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=1,# bumps to 2
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=0)  # this counter is num cnn processed not queued
    log_CNN()
    assert ClusterNumNode.objects.count() == 2
    url = reverse('clear-num-nodes-reqs',args=[orgAccountObj.id])
    logger.info(f"using url:{url}")

    response = client.post(url,HTTP_ACCEPT='application/json')
    assert((response.status_code == 200) or (response.status_code == 302))
    assert ClusterNumNode.objects.count() == 1

    # now clear the active one

    url = reverse('clear-active-num-node-req',args=[orgAccountObj.id])
    logger.info(f"using url:{url}")

    response = client.post(url,HTTP_ACCEPT='application/json')
    assert((response.status_code == 200) or (response.status_code == 302))
    assert ClusterNumNode.objects.count() == 0



#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_web_user_clear_num_nodes_multiple_users(caplog, setup_logging, client, mock_email_backend, initialize_test_environ, developer_TEST_USER):
    '''
        This procedure will test logic clear num nodes from the org-manage-cluster web page
    '''
    logger = setup_logging
    
    clusterObj = get_test_compute_cluster()
    
    assert(clusterObj.is_deployed == False)
    assert(clusterObj.version == clusterObj.cur_version) # ensure initialization is correct 
    initial_version = clusterObj.version
    new_version = initial_version
    initial_is_public = clusterObj.is_public

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert OrgAccount.objects.count() == 1
    orgAccountObj.save()

    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    assert clusterObj.num_setup_cmd == 0
    assert clusterObj.num_setup_cmd_successful == 0
    assert clusterObj.num_ps_cmd_successful == 0
    assert clusterObj.num_ps_cmd == 0
    assert clusterObj.num_onn == 0

    # setup necessary form data
    form_data = {
        'is_public': initial_is_public,
        'version': initial_version, # First time we use the current version
        'min_node_cap': 1,
        'max_node_cap': 3,
        'allow_deploy_by_token': True,
        'destroy_when_no_nodes': True,
        'provisioning_suspended': False,
    }

    loop_count = process_cluster_configure(client,
                                        orgAccountObj,
                                        new_time=datetime.now(timezone.utc),
                                        view_name='org-configure',
                                        url_args=[org_account_id],
                                        data=form_data,
                                        loop_count=0,
                                        num_iters=3,
                                        expected_change_ps_cmd=2 # SetUp - Update (min nodes is 1)
                                        )
    # assert the form was successful
    # refresh the OrgAccount object
    clusterObj = get_test_compute_cluster()
    assert(clusterObj.is_public == initial_is_public) 
    assert(clusterObj.version == initial_version) 
    assert clusterObj.num_setup_cmd == 1
    assert clusterObj.num_setup_cmd_successful == 1
    assert clusterObj.num_ps_cmd_successful == 2
    assert clusterObj.num_ps_cmd == 2
    assert clusterObj.num_onn == 1
    assert clusterObj.cfg_asg.min == 1
    assert clusterObj.cfg_asg.max == 3
    assert clusterObj.allow_deploy_by_token == True
    assert clusterObj.destroy_when_no_nodes == True
    assert PsCmdResult.objects.count() == 2 # SetUp - Refresh 
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=clusterObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Configure' in psCmdResultObjs[0].ps_cmd_summary_label # we use Configure (it's user friendly) but it's really SetUp)
    logger.info(f"[1]:{psCmdResultObjs[1].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[1].ps_cmd_summary_label # no entries and min_node_cap is 1 so Update
    assert ClusterNumNode.objects.count() == 0

    # test clamp to minimum
    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 1, 
        'add_onn-ttl_minutes': 15,
    }

    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=0,# already set to min (i.e. 1) so no cmd issued
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=0)  # this counter is num cnn processed not queued
    log_CNN()
    assert ClusterNumNode.objects.count() == 1
    form_data = {
        'form_submit': 'add_onn',
        'add_onn-desired_num_nodes': 2, 
        'add_onn-ttl_minutes': 15,
    }

    loop_count,response = process_onn_api(client=client,
                                orgAccountObj=orgAccountObj,
                                new_time=datetime.now(timezone.utc),
                                view_name='org-manage-cluster',
                                url_args=[orgAccountObj.id],
                                access_token=None,
                                data=form_data,
                                loop_count=loop_count,
                                num_iters=3, 
                                expected_change_ps_cmd=1,# bumps to 2
                                expected_status='QUEUED',
                                expected_org_account_num_cnn_change=0)  # this counter is num cnn processed not queued
    assert ClusterNumNode.objects.count() == 2

    rtu = random_test_user()
    m = create_active_membership(orgAccountObj,rtu)
    m.refresh_from_db()
    log_CNN()
    assert verify_api_user_makes_onn_ttl( client=client,
                                    orgAccountObj=orgAccountObj,
                                    user=rtu,
                                    password=TEST_PASSWORD,
                                    desired_num_nodes=1,
                                    ttl_minutes=15,
                                    expected_change_ps_cmd=1) 
    log_CNN()
    assert ClusterNumNode.objects.count() == 3
    clusterObj.refresh_from_db()
    assert len(clusterObj.cnnro_ids) == 2


    # Now negative test non owner user trying to remove entries

    url = reverse('clear-num-nodes-reqs',args=[orgAccountObj.id])
    logger.info(f"using url:{url}")

    response = client.post(url,HTTP_ACCEPT='application/json')
    assert((response.status_code == 200) or (response.status_code == 302))

    idle, loop_count = loop_iter(clusterObj,loop_count)
    idle, loop_count = loop_iter(clusterObj,loop_count)

    assert ClusterNumNode.objects.count() == 3 # non owner cannot clear


    # log back in with owner 
    assert(client.login(username=OWNER_USER, password=OWNER_PASSWORD))

    url = reverse('clear-num-nodes-reqs',args=[orgAccountObj.id])
    logger.info(f"using url:{url}")

    response = client.post(url,HTTP_ACCEPT='application/json')
    assert((response.status_code == 200) or (response.status_code == 302))

    idle, loop_count = loop_iter(clusterObj,loop_count)
    idle, loop_count = loop_iter(clusterObj,loop_count)

    assert ClusterNumNode.objects.count() == 2

    # now clear the active ones

    url = reverse('clear-active-num-node-req',args=[orgAccountObj.id])
    logger.info(f"using url:{url}")
    response = client.post(url,HTTP_ACCEPT='application/json')
    assert((response.status_code == 200) or (response.status_code == 302))
    idle, loop_count = loop_iter(clusterObj,loop_count)
    idle, loop_count = loop_iter(clusterObj,loop_count)
    assert ClusterNumNode.objects.count() == 0
    clusterObj.refresh_from_db()
    assert len(clusterObj.cnnro_ids) == 0
