import pytest
import logging
import sys
import os
import pathlib
import uuid
import pytz
import json
import pprint
import grpc
from users import ps_client

from subprocess import CalledProcessError
import ps_server_pb2
import ps_server_pb2_grpc
from google.protobuf.json_format import MessageToJson

from importlib import import_module
from datetime import datetime, timezone, timedelta
from decimal import *
from django.urls import reverse
from users.tests.utilities_for_unit_tests import init_test_environ,get_test_org,get_test_compute_cluster,get_test_compute_cluster,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user,pytest_approx,the_TEST_USER,init_mock_ps_server
from users.models import Membership,OwnerPSCmd,OrgAccount,ClusterNumNode,NodeGroup,PsCmdResult
from users.forms import NodeGroupCfgForm
from users.tasks import update_burn_rates,purge_old_PsCmdResultsForOrg,process_num_node_table,process_owner_ps_cmds_table,process_Update_cmd,process_Destroy_cmd,process_Refresh_cmd,cost_accounting_cluster,check_provision_env_ready
from time import sleep
from django.contrib import messages
from allauth.account.decorators import verified_email_required
from django.forms.models import model_to_dict

from users.global_constants import *
from users.tests.global_test_constants import *
from users.ps_errors import *
# Import the fixtures
from users.tests.utilities_for_unit_tests import TEST_USER,TEST_PASSWORD,DEV_TEST_USER,DEV_TEST_PASSWORD,TEST_ORG_NAME

module_name = 'tasks'
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

# # do all setup before running all tests here
# def setup_module(tasks_module):
#     logger.info('---setup complete---')
    

# # teardown after running all tests 
# def teardown_module(tasks_module):
#     logger.info('---teardown complete---')

@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_update_burn_rates(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'update_burn_rates' routine
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.cur_asg.num = 1
    nodeGroupObj.cur_asg.min = 0
    nodeGroupObj.cur_asg.max = 100
    nodeGroupObj.budget.balance = 100000.0 # test a large number
    nodeGroupObj.budget.monthly_allowance = 1000.0
    nodeGroupObj.budget.max__allowance = 100000.0
    nodeGroupObj.budget.save()

    assert(orgAccountObj.name == TEST_ORG_NAME)
    init_mock_ps_server(name=TEST_ORG_NAME,num_nodes=1)

    assert(orgAccountObj.owner.username == OWNER_USER)
    forecast_min_hrly, forecast_cur_hrly, forecast_max_hrly = update_burn_rates(nodeGroupObj)
    logger.info(f"{nodeGroupObj.cfg_asg.min}/{nodeGroupObj.cur_asg.num}/{nodeGroupObj.cfg_asg.max}")
    logger.info(f"{forecast_min_hrly}/{forecast_cur_hrly}/{forecast_max_hrly}")
    assert(pytest_approx(forecast_min_hrly,0.0001))
    assert(pytest_approx(forecast_cur_hrly,0.379))
    assert(pytest_approx(forecast_max_hrly,2.413))

    nodeGroupObj.budget.balance = 100000.0 # test a large number
    nodeGroupObj.budget.monthly_allowance = 1000.0
    nodeGroupObj.budget.max__allowance = 100000.0
    nodeGroupObj.budget.save()
    forecast_min_hrly, forecast_cur_hrly, forecast_max_hrly = update_burn_rates(nodeGroupObj)
    logger.info(f"{nodeGroupObj.cfg_asg.min}/{nodeGroupObj.cur_asg.num}/{nodeGroupObj.cfg_asg.max}")
    logger.info(f"{forecast_min_hrly}/{forecast_cur_hrly}/{forecast_max_hrly}")
    assert False # TBD add checks here


#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_purge_old_PsCmdResultsForOrg(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'purge_old_PsCmdResultsForOrg' routine
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    psCmdResultObj = PsCmdResult.objects.create(org=orgAccountObj,expiration=datetime.now(timezone.utc)+timedelta(days=1))
    psCmdResultObj = PsCmdResult.objects.create(org=orgAccountObj,expiration=datetime.now(timezone.utc)-timedelta(minutes=1))

    assert(orgAccountObj.name == TEST_ORG_NAME)
    assert(orgAccountObj.owner.username == OWNER_USER)
    assert(PsCmdResult.objects.count()==2) 
    purge_old_PsCmdResultsForOrg(orgAccountObj)
    assert(PsCmdResult.objects.count()==2) 

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_EMPTY_DESTROY(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when it should destroy the cluster when there are no
        capacity requests (i.e. cnn) 
        and min num nodes is zero
        Note: we use the org name to trigger the specific exception in our Mock ps-server
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.is_deployed = True
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = True
    nodeGroupObj.cfg_asg.min = 0
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 0
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    process_num_node_table(orgAccountObj,False)
    orgAccountObj.refresh_from_db()
    assert nodeGroupObj.num_onn == 1
    assert nodeGroupObj.num_setup_cmd == 0
    assert nodeGroupObj.num_ps_cmd == 1 # Update
    assert nodeGroupObj.num_setup_cmd_successful == 0
    assert nodeGroupObj.num_ps_cmd_successful == 1
    assert PsCmdResult.objects.count() == 1
    logger.info(f"PsCmdResult.objects.count()={PsCmdResult.objects.count()}")
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=nodeGroupObj).order_by('creation_date')
    logger.info(f"[1]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Destroy' in psCmdResultObjs[0].ps_cmd_summary_label


#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_EMPTY_UPDATE(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when it should NOT destroy the cluster when there 
        are no capacity requests and 
        and min num nodes is NOT zero so Update is issued for min num nodes
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.is_deployed = True
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 1
    orgAccountObj.save()
    logger.info(f"desired:{nodeGroupObj.cfg_asg.num}")
    assert nodeGroupObj.cfg_asg.num == 0
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 0
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    process_num_node_table(orgAccountObj,False)
    orgAccountObj.refresh_from_db()
    assert nodeGroupObj.num_onn == 1
    assert nodeGroupObj.num_ps_cmd == 1 #  Update
    assert nodeGroupObj.num_setup_cmd_successful == 0
    assert nodeGroupObj.num_ps_cmd_successful == 1
    assert PsCmdResult.objects.count() == 1
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=nodeGroupObj).order_by('creation_date')
    logger.info(f"PsCmdResult.objects.count()={PsCmdResult.objects.count()}")
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[0].ps_cmd_summary_label
    assert nodeGroupObj.cfg_asg.num == 1
#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_EMPTY_NOT_DEPLOYED(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when it should NOT destroy the cluster when there 
        are no capacity requests and 
        and min num nodes is zero and the cluster is not deployed (i.e. NOTHING HAPPENS)
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.is_deployed = False
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 0
    orgAccountObj.save()
    logger.info(f"desired:{nodeGroupObj.cfg_asg.num}")
    assert nodeGroupObj.cfg_asg.num == 0
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 0
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    process_num_node_table(orgAccountObj,False)
    orgAccountObj.refresh_from_db()
    assert nodeGroupObj.num_onn == 0
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    assert nodeGroupObj.cfg_asg.num == 0

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_EMPTY_SET_TO_MIN(tasks_module,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when it should NOT destroy the cluster when there 
        are no capacity requests and 
        and min num nodes is NOT zero and 
        the current desired_nodes is not 
        the same as min (last deployment was different than min)
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.is_deployed = True
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    orgAccountObj.save()
    logger.info(f"desired:{nodeGroupObj.cfg_asg.num}")
    assert nodeGroupObj.cfg_asg.num == 0
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 0
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    process_num_node_table(orgAccountObj,False)
    orgAccountObj.refresh_from_db()
    assert nodeGroupObj.num_onn == 1
    assert nodeGroupObj.num_ps_cmd == 1 #  Update
    assert nodeGroupObj.num_setup_cmd_successful == 0
    assert nodeGroupObj.num_ps_cmd_successful == 1
    assert PsCmdResult.objects.count() == 1
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=nodeGroupObj).order_by('creation_date')
    logger.info(f"PsCmdResult.objects.count()={PsCmdResult.objects.count()}")
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[0].ps_cmd_summary_label

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_NOT_EMPTY_CHANGE_VERSION(tasks_module,create_TEST_USER,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when the CNN table entry processed changes the version
        and generates a Destroy cmd
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    # nodeGroupObj.is_deployed = True
    # nodeGroupObj.cur_version = 'v3'
    # nodeGroupObj.save()
    logger.info(f"nodeGroupObj.cur_version={nodeGroupObj.cur_version} nodeGroupObj.is_deployed={nodeGroupObj.is_deployed}")
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.cfg_asg.num = 2
    nodeGroupObj.version = 'v3'
    orgAccountObj.save()
    expire_date = datetime.now(timezone.utc)+timedelta(hours=1)
    ClusterNumNode.objects.create(user=the_TEST_USER(),org=orgAccountObj, desired_num_nodes=nodeGroupObj.cfg_asg.num+1,expiration=expire_date)

    logger.info(f"desired:{nodeGroupObj.cfg_asg.num}")
    assert nodeGroupObj.cfg_asg.num == 2
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 1
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    
    process_num_node_table(orgAccountObj,False)
    orgAccountObj.refresh_from_db()
    logger.info(f"orgAccountObj: num_onn={nodeGroupObj.num_onn} num_ps_cmd={nodeGroupObj.num_ps_cmd} desired_num_nodes={nodeGroupObj.cfg_asg.num} cnt:{PsCmdResult.objects.count()}")
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=nodeGroupObj).order_by('creation_date')
    assert PsCmdResult.objects.count() == 1
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert nodeGroupObj.num_onn == 1 
    assert OwnerPSCmd.objects.count() == 0
    assert nodeGroupObj.num_setup_cmd == 0 # handled in fixture
    assert nodeGroupObj.num_ps_cmd == 1
    assert nodeGroupObj.num_setup_cmd_successful == 0
    assert nodeGroupObj.num_ps_cmd_successful == 1
    logger.info(f"PsCmdResult.objects.count()={PsCmdResult.objects.count()}")
    assert PsCmdResult.objects.count() == 1
    psCmdResultObjs = PsCmdResult.objects.filter(cluster=nodeGroupObj).order_by('creation_date')
    logger.info(f"[0]:{psCmdResultObjs[0].ps_cmd_summary_label}")
    assert 'Update' in psCmdResultObjs[0].ps_cmd_summary_label
    assert nodeGroupObj.cfg_asg.num == 3 

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_NOT_EMPTY_CHANGE_IS_PUBLIC(tasks_module,create_TEST_USER,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when the CNN table is not empty and  the is_public flag changes
        and generates a Destroy cmd
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.is_deployed = True
    nodeGroupObj.cur_version = 'v3'
    nodeGroupObj.is_public = True
    nodeGroupObj.provision_env_ready = True
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.cfg_asg.num = 2
    nodeGroupObj.version = 'v3'
    nodeGroupObj.is_public = False
    orgAccountObj.save()
    expire_date = datetime.now(timezone.utc)+timedelta(hours=1)
    ClusterNumNode.objects.create(user=the_TEST_USER(),org=orgAccountObj, desired_num_nodes=nodeGroupObj.cfg_asg.num+1,expiration=expire_date)
    nodeGroupObj.cnnro_ids = []
    nodeGroupObj.cnnro_ids.append(str(ClusterNumNode.objects.first().id))
    logger.info(f"nodeGroupObj.cnnro_ids:{nodeGroupObj.cnnro_ids}")
    nodeGroupObj.save()

    logger.info(f"desired:{nodeGroupObj.cfg_asg.num}")
    assert nodeGroupObj.cfg_asg.num == 2
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 1
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    
    process_num_node_table(orgAccountObj,False)

    orgAccountObj.refresh_from_db()
    assert nodeGroupObj.num_onn == 0
    assert OwnerPSCmd.objects.count() == 0
    assert nodeGroupObj.num_ps_cmd == 1
    assert nodeGroupObj.cfg_asg.num == 0 # destroyed
    assert PsCmdResult.objects.count() == 1
    psCmdResultObj = PsCmdResult.objects.first()
    assert 'Destroy' in psCmdResultObj.ps_cmd_summary_label
    assert ClusterNumNode.objects.count() == 1 # until it expires

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_process_num_node_table_CNN_NOT_EMPTY_UPDATE(tasks_module,create_TEST_USER,initialize_test_environ):
    '''
        This procedure will test the 'process_num_node_table' routine
        for the case when the CNN table entry processed changes desired num nodes
        and generates an Update
    '''

    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.is_deployed = True
    nodeGroupObj.cur_version = 'v3'
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.cfg_asg.num = 2
    new_desire_num_nodes = nodeGroupObj.cfg_asg.num+1
    nodeGroupObj.version = 'v3'
    orgAccountObj.save()
    expire_date = datetime.now(timezone.utc)+timedelta(hours=1)
    ClusterNumNode.objects.create(user=the_TEST_USER(),org=orgAccountObj, desired_num_nodes=new_desire_num_nodes,expiration=expire_date)

    logger.info(f"desired:{nodeGroupObj.cfg_asg.num}")
    assert nodeGroupObj.cfg_asg.num == 2
    assert nodeGroupObj.num_onn == 0
    assert ClusterNumNode.objects.count() == 1
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0
    
    process_num_node_table(orgAccountObj,False)

    orgAccountObj.refresh_from_db()
    assert nodeGroupObj.num_onn == 1
    assert OwnerPSCmd.objects.count() == 0 
    assert nodeGroupObj.num_ps_cmd == 1 # 
    assert nodeGroupObj.cfg_asg.num == new_desire_num_nodes
    assert PsCmdResult.objects.count() == 1
    psCmdResultObj = PsCmdResult.objects.first()
    assert 'Update' in psCmdResultObj.ps_cmd_summary_label
    assert ClusterNumNode.objects.count() == 1 # until it expires
    cnn = ClusterNumNode.objects.first()
    nodeGroupObj.refresh_from_db()
    assert str(cnn.id) in nodeGroupObj.cnnro_ids




test_params = [
    (NEG_TEST_GRPC_ERROR_ORG_NAME, NEG_TEST_GRPC_ERROR_MSG),
    (NEG_TEST_TERRAFORM_ERROR_ORG_NAME, NEG_TEST_TERRAFORM_ERROR_MSG),
    (NEG_TEST_STOP_ITER_ERROR_ORG_NAME, NEG_TEST_STOP_ITER_ERROR_MSG),
]
#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize("NEG_TEST_ERROR_ORG_NAME, NEG_TEST_ERROR_MSG", test_params)
def test_process_Update_cmd_when_exception_occurs(create_TEST_USER, NEG_TEST_ERROR_ORG_NAME, NEG_TEST_ERROR_MSG,setup_logging):
    '''
        This procedure will accept generic parms to negative test the 'process_Update_cmd' routine 
    '''
    logger = setup_logging
    init_test_environ(name=NEG_TEST_ERROR_ORG_NAME,the_logger=logger)
    orgAccountObj = get_test_org(NEG_TEST_ERROR_ORG_NAME)
    nodeGroupObj.is_deployed = True
    nodeGroupObj.cur_version = 'v3'
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.cfg_asg.num = 0
    nodeGroupObj.cfg_asg.max = 4
    nodeGroupObj.is_public = False
    new_desire_num_nodes = nodeGroupObj.cfg_asg.num+1
    nodeGroupObj.version = 'v3'
    orgAccountObj.save()
    test_min = 0
    test_desired = 2
    test_max = 5
    test_version = 'v3'
    test_is_public = False
    test_expire_time = datetime.now(timezone.utc)+timedelta(hours=1)
    deploy_values ={'min_node_cap': test_min, 'desired_num_nodes': test_desired, 'max_node_cap': test_max,'version': test_version, 'is_public': test_is_public, 'expire_time': test_expire_time }

    with pytest.raises(ProvisionCmdError) as error:
        process_Update_cmd(orgAccountObj,TEST_USER,deploy_values,test_expire_time)

    # Assert that the error details match the expected error
    #logger.critical(f"error:{repr(error)}")
    assert str(NEG_TEST_ERROR_MSG) in str(error.value)

test_params = [
    (NEG_TEST_LOW_BALANCE_ORG_NAME, NEG_TEST_LOW_BALANCE_ERROR_MSG)
]
#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize("NEG_TEST_ERROR_ORG_NAME, NEG_TEST_ERROR_MSG", test_params)
def test_process_Update_cmd_when_LOW_BALANCE_exception_occurs_ON_Update(create_TEST_USER, NEG_TEST_ERROR_ORG_NAME, NEG_TEST_ERROR_MSG):
    '''
        This procedure will do a negative test of the 'process_Update_cmd' routine for LOW balance error
    '''

    init_test_environ(name=NEG_TEST_ERROR_ORG_NAME,balance=2.0)
    orgAccountObj = get_test_org(NEG_TEST_ERROR_ORG_NAME)
    nodeGroupObj.is_deployed = True
    nodeGroupObj.cur_version = 'v3'
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.cfg_asg.num = 0
    nodeGroupObj.cfg_asg.max = 4
    nodeGroupObj.is_public = False
    nodeGroupObj.version = 'v3'
    orgAccountObj.save()
    #logger.critical(f"BEFORE orgAccountObj:{pprint.pformat(model_to_dict(orgAccountObj,fields=None))}")
    cost_accounting_cluster(nodeGroupObj)
    #logger.critical(f" AFTER orgAccountObj:{pprint.pformat(model_to_dict(orgAccountObj,fields=None))}")
    test_min = 0
    test_desired = 2
    test_max = 5
    test_version = 'v3'
    test_is_public = False
    test_expire_time = datetime.now(timezone.utc)+timedelta(hours=1)
    deploy_values ={'min_node_cap': test_min, 'desired_num_nodes': test_desired, 'max_node_cap': test_max,'version': test_version, 'is_public': test_is_public, 'expire_time': test_expire_time }

    with pytest.raises((ProvisionCmdError,LowBalanceError)) as error:
        process_Update_cmd(orgAccountObj,TEST_USER,deploy_values,test_expire_time)

    # Assert that the error details match the expected error
    #logger.critical(f"error:{repr(error)}")
    assert str(NEG_TEST_ERROR_MSG) in str(error.value)

test_params = [
    (NEG_TEST_TERRAFORM_ERROR_ORG_NAME, NEG_TEST_TERRAFORM_ERROR_MSG, 'Destroy'),
    (NEG_TEST_TERRAFORM_ERROR_ORG_NAME, NEG_TEST_TERRAFORM_ERROR_MSG, 'Refresh')
]
#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize("NEG_TEST_ERROR_ORG_NAME, NEG_TEST_ERROR_MSG, PS_CMD", test_params)
def test_process_Update_cmd_when_exception_occurs_ON_OWNER_PS_CMD(create_TEST_USER, NEG_TEST_ERROR_ORG_NAME, NEG_TEST_ERROR_MSG, PS_CMD):
    '''
        This procedure will accept generic parms to negative test the 'process_Update_cmd' routine when processing Owner PS Cmds (i.e. Destroy and Refresh)
    '''
    init_test_environ(name=NEG_TEST_ERROR_ORG_NAME)
    orgAccountObj = get_test_org(NEG_TEST_ERROR_ORG_NAME)
    nodeGroupObj.is_deployed = True
    nodeGroupObj.cur_version = 'v3'
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = False
    nodeGroupObj.cfg_asg.min = 2
    nodeGroupObj.cfg_asg.num = 0
    nodeGroupObj.cfg_asg.max = 4
    nodeGroupObj.is_public = False
    new_desire_num_nodes = nodeGroupObj.cfg_asg.num+1
    nodeGroupObj.version = 'v3'
    orgAccountObj.save()
    test_min = 0
    test_desired = 2
    test_max = 5
    test_version = 'v3'
    test_is_public = False
    test_expire_time = datetime.now(timezone.utc)+timedelta(hours=1)
    deploy_values ={'min_node_cap': test_min, 'desired_num_nodes': test_desired, 'max_node_cap': test_max,'version': test_version, 'is_public': test_is_public, 'expire_time': test_expire_time }
    owner_ps_cmd = OwnerPSCmd.objects.create(user=orgAccountObj.owner, org=orgAccountObj, ps_cmd='Destroy', create_time=datetime.now(timezone.utc))
    assert OwnerPSCmd.objects.count()==1
    with pytest.raises(ProvisionCmdError) as error:
        if PS_CMD == 'Destroy':
            process_Destroy_cmd(orgAccountObj,TEST_USER,owner_ps_cmd=owner_ps_cmd)
        elif PS_CMD == 'Refresh':
            process_Refresh_cmd(orgAccountObj,TEST_USER,owner_ps_cmd=owner_ps_cmd)
        else:
            assert False, f"PS_CMD:{PS_CMD} is not a valid value"
    assert OwnerPSCmd.objects.count()==0
    # Assert that the error details match the expected error
    #logger.critical(f"error:{repr(error)}")
    assert str(NEG_TEST_ERROR_MSG) in str(error.value)

def setup_before_process_num_node_table_with_exception(orgAccountObj,ONN_IS_EMPTY,DESTROY_WHEN_NO_NODES,MIN_NODE_CAP,IS_DEPLOYED):
    nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
    nodeGroupObj.is_deployed = IS_DEPLOYED
    nodeGroupObj.is_public = False
    nodeGroupObj.cur_version = 'v1.0.0'
    nodeGroupObj.save()
    nodeGroupObj.destroy_when_no_nodes = DESTROY_WHEN_NO_NODES
    nodeGroupObj.cfg_asg.min = MIN_NODE_CAP
    nodeGroupObj.cfg_asg.num = MIN_NODE_CAP + 1 # we need this to not equal nodeGroupObj.cfg_asg.min to force the Destroy
    nodeGroupObj.is_public = nodeGroupObj.is_public
    nodeGroupObj.version = nodeGroupObj.cur_version
    orgAccountObj.save()

    if not ONN_IS_EMPTY:
        ClusterNumNode.objects.create(  org=orgAccountObj, 
                                    user=orgAccountObj.owner, 
                                    desired_num_nodes= MIN_NODE_CAP + 2, # to force the Update
                                    expiration=datetime.now(timezone.utc)+timedelta(hours=1))
        assert ClusterNumNode.objects.count() == 1
    else:
        # this verfies that the cull_expired_entries is called and properly deletes the expired entry
        ClusterNumNode.objects.create(  org=orgAccountObj, 
                                    user=orgAccountObj.owner, 
                                    desired_num_nodes= MIN_NODE_CAP + 2, # to force the Update
                                    expiration=datetime.now(timezone.utc)-timedelta(minutes=1))# Expired and will be deleted
        assert ClusterNumNode.objects.count() == 1
    assert nodeGroupObj.num_onn == 0
    assert nodeGroupObj.num_ps_cmd == 0
    assert PsCmdResult.objects.count() == 0

def verify_after_process_num_node_table_after_exception(orgAccountObj,ONN_IS_EMPTY,DESTROY_WHEN_NO_NODES,MIN_NODE_CAP,WAS_DEPLOYED):
    '''
        This procedure will verify the ClusterNumNode table after an exception occurs in the process_Update_cmd routine

        Args:       orgAccountObj:          The OrgAccount object
                    ONN_IS_EMPTY:           Boolean indicating if the ClusterNumNode table is empty
                    DESTROY_WHEN_NO_NODES:  Boolean indicating if the OrgAccount.destroy_when_no_nodes is True
                    MIN_NODE_CAP:           The OrgAccount.min_node_cap
                    WAS_DEPLOYED:           Boolean indicating if the NodeGroup.is_deployed is True
        we assume in setup that onnTop.desired_num_nodes != nodeGroupObj.cfg_asg.num: 
    '''

    orgAccountObj.refresh_from_db()
    nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
    logger.info(f"orgAccountObj.max_ddt:{orgAccountObj.max_ddt}")
    logger.info(f"timedelta(hours=MIN_HRS_TO_LIVE_TO_START):{timedelta(hours=MIN_HRS_TO_LIVE_TO_START)}")
    assert ClusterNumNode.objects.count() == 0 # on exception we remove the entry
    assert ((nodeGroupObj.cnnro_ids == []) or (nodeGroupObj.cnnro_ids == None)) # cleaned up on exception
    psCmdResultObj = PsCmdResult.objects.first()
    called_process_Update_cmd = True
    if ONN_IS_EMPTY:
        if DESTROY_WHEN_NO_NODES and MIN_NODE_CAP == 0:
            if not WAS_DEPLOYED:
                called_process_Update_cmd = False
                assert PsCmdResult.objects.count() == 0
    if called_process_Update_cmd:
        assert PsCmdResult.objects.count() == 1
        if psCmdResultObj:
            logger.info(f"psCmdResultObj.error:{psCmdResultObj.error}")
            logger.info(f"psCmdResultObj.ps_cmd_output:{psCmdResultObj.ps_cmd_output}")
            logger.info(f"psCmdResultObj.ps_cmd_summary_label:{psCmdResultObj.ps_cmd_summary_label}")
            logger.info(f"psCmdResultObj.expiration:{psCmdResultObj.expiration}")
    logger.info(f"nodeGroupObj.num_onn:{nodeGroupObj.num_onn} PsCmdResult.objects.count():{PsCmdResult.objects.count()}")
    if PsCmdResult.objects.count() == 1:
        assert nodeGroupObj.num_onn == 1 
    else:
        assert nodeGroupObj.num_onn == 0
    if called_process_Update_cmd:
        assert nodeGroupObj.num_ps_cmd == 1  
    else:
        assert nodeGroupObj.num_ps_cmd == 0

test_params = [
    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,True,2,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,True,2,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,True,2,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,False,2,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,False,2,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,False,2,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,True,0,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,True,0,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,True,0,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,False,0,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,False,0,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,False,0,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,True,2,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,True,2,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,True,2,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,False,2,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,False,2,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,False,2,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,True,0,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,True,0,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,True,0,True),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,False,0,True),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,False,0,True),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,False,0,True),
# ###########
    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,True,2,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,True,2,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,True,2,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,False,2,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,False,2,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,False,2,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,True,0,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,True,0,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,True,0,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,True,False,0,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,True,False,0,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,True,False,0,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,True,2,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,True,2,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,True,2,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,False,2,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,False,2,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,False,2,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,True,0,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,True,0,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,True,0,False),

    ((ProvisionCmdError,Exception),NEG_TEST_GRPC_ERROR_ORG_NAME,False,False,0,False),
    ((ProvisionCmdError,CalledProcessError,Exception),NEG_TEST_TERRAFORM_ERROR_ORG_NAME,False,False,0,False),
    ((ProvisionCmdError,StopIteration,Exception),NEG_TEST_STOP_ITER_ERROR_ORG_NAME,False,False,0,False),

]
#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize("PS_EXCEPTIONS,NEG_TEST_ERROR_ORG_NAME, ONN_IS_EMPTY, DESTROY_WHEN_NO_NODES, MIN_NODE_CAP, IS_DEPLOYED", test_params)
def test_process_num_node_table_NEGATIVE_TESTS(tasks_module,create_TEST_USER, PS_EXCEPTIONS, NEG_TEST_ERROR_ORG_NAME, ONN_IS_EMPTY, DESTROY_WHEN_NO_NODES, MIN_NODE_CAP, IS_DEPLOYED):
    '''
        This procedure will negative test the 'process_num_node_table' routine
        for the case when it should destroy the cluster when there are no
        capacity requests (i.e. cnn) 
        and min num nodes is zero and an terraform exception is raised
        Note: we use the org name to trigger the specific exception in our Mock ps-server
    '''
    init_test_environ(name=NEG_TEST_ERROR_ORG_NAME)
    orgAccountObj = get_test_org(NEG_TEST_ERROR_ORG_NAME)
    setup_before_process_num_node_table_with_exception(orgAccountObj,ONN_IS_EMPTY,DESTROY_WHEN_NO_NODES,MIN_NODE_CAP,IS_DEPLOYED)
    process_num_node_table(orgAccountObj,False)
    verify_after_process_num_node_table_after_exception(orgAccountObj,ONN_IS_EMPTY,DESTROY_WHEN_NO_NODES,MIN_NODE_CAP,IS_DEPLOYED)
    if ONN_IS_EMPTY:
        if DESTROY_WHEN_NO_NODES and MIN_NODE_CAP == 0:
            if IS_DEPLOYED:
                psCmdResultObj = PsCmdResult.objects.first()
                assert 'Destroy' in psCmdResultObj.ps_cmd_summary_label
                assert nodeGroupObj.destroy_when_no_nodes == False # under these conditions on exception we set destroy_when_no_nodes to False to stop loop
                assert nodeGroupObj.cfg_asg.num == 0 # on exception we set desired to zero to match min to stop loop
                assert nodeGroupObj.cfg_asg.min == 0 # on exception we set min to zero
        else:
            if MIN_NODE_CAP != nodeGroupObj.cfg_asg.num:
                psCmdResultObj = PsCmdResult.objects.first()
                assert 'Update' in psCmdResultObj.ps_cmd_summary_label
                assert nodeGroupObj.cfg_asg.num == 0 # on exception we set desired to zero to match min to stop loop
                assert nodeGroupObj.cfg_asg.min == 0 # on exception we set min to zero
                assert nodeGroupObj.cfg_asg.min == 0 # on exception we set min to zero
                assert nodeGroupObj.cfg_asg.num == 0 # on exception we set desired to zero to match min to stop loop
    else:
        # assume onnTop.desired_num_nodes != nodeGroupObj.cfg_asg.num
        # Note: here we just remove the entry that got the exception
        # and we do not expect the desired_num_nodes to be changed in this pass
        psCmdResultObj = PsCmdResult.objects.first()
        assert 'Update' in psCmdResultObj.ps_cmd_summary_label


test_params = [
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,True,2,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,False,2,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,True,0,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,False,0,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,True,2,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,False,2,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,True,0,False),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,False,0,False),

    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,True,2,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,False,2,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,True,0,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',True,False,0,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,True,2,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,False,2,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,True,0,True),
    ((ProvisionCmdError,Exception),'LowBalanceError_org',False,False,0,True),
]
#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
@pytest.mark.parametrize("PS_EXCEPTIONS, NEG_TEST_ERROR_ORG_NAME, ONN_IS_EMPTY, DESTROY_WHEN_NO_NODES, MIN_NODE_CAP, IS_DEPLOYED", test_params)
def test_process_process_num_node_table_when_LOW_BALANCE_exception_occurs_ON_Update(create_TEST_USER, PS_EXCEPTIONS, NEG_TEST_ERROR_ORG_NAME, ONN_IS_EMPTY, DESTROY_WHEN_NO_NODES, MIN_NODE_CAP,IS_DEPLOYED):
    '''
        This procedure will do a negative test of the 'process_Update_cmd' routine for LOW balance error
    '''
    #logger.critical(f"COOLOFF_SECS:{COOLOFF_SECS}")
    init_test_environ(name=NEG_TEST_ERROR_ORG_NAME,balance=2.0)
    orgAccountObj = get_test_org(NEG_TEST_ERROR_ORG_NAME)
    nodeGroupObj = get_test_compute_cluster(orgAccountObj)
    #logger.critical(f"BEFORE orgAccountObj:{pprint.pformat(model_to_dict(orgAccountObj,fields=None))}")
    cost_accountingcluster(nodeGroupObj)
    setup_before_process_num_node_table_with_exception(orgAccountObj,ONN_IS_EMPTY,DESTROY_WHEN_NO_NODES,MIN_NODE_CAP,IS_DEPLOYED)
    orgAccountObj.balance = 2.0 # force low balance exception
    orgAccountObj.save()
    process_num_node_table(orgAccountObj,False)
    verify_after_process_num_node_table_after_exception(orgAccountObj,ONN_IS_EMPTY,DESTROY_WHEN_NO_NODES,MIN_NODE_CAP,IS_DEPLOYED)
    if ONN_IS_EMPTY:
        if DESTROY_WHEN_NO_NODES and MIN_NODE_CAP == 0:
            if IS_DEPLOYED:
                psCmdResultObj = PsCmdResult.objects.first()
                logger.info(f"ps_cmd_summary_label:{psCmdResultObj.ps_cmd_summary_label}")
                assert 'Destroy' in psCmdResultObj.ps_cmd_summary_label
                assert nodeGroupObj.destroy_when_no_nodes == DESTROY_WHEN_NO_NODES # we don't throw an exception when destroying for low balance
                assert nodeGroupObj.cfg_asg.min == MIN_NODE_CAP # on exception we set min to zero
        else:
            if MIN_NODE_CAP != nodeGroupObj.cfg_asg.num:
                psCmdResultObj = PsCmdResult.objects.first()
                assert 'Update' in psCmdResultObj.ps_cmd_summary_label
                assert nodeGroupObj.cfg_asg.num == 0 # on exception we set desired to zero to match min to stop loop
                assert nodeGroupObj.cfg_asg.min == 0 # on exception we set min to zero
                assert nodeGroupObj.cfg_asg.min == 0 # on exception we set min to zero
                assert nodeGroupObj.cfg_asg.num == 0 # on exception we set desired to zero to match min to stop loop
    else:
        # assume onnTop.desired_num_nodes != nodeGroupObj.cfg_asg.num
        # Note: here we just remove the entry that got the exception
        # and we do not expect the desired_num_nodes to be changed in this pass
        psCmdResultObj = PsCmdResult.objects.first()
        assert 'Update' in psCmdResultObj.ps_cmd_summary_label

#@pytest.mark.dev
@pytest.mark.real_ps_server
@pytest.mark.django_db
def test_get_current_version_after_setup(tasks_module,initialize_test_environ,verified_TEST_USER):
    '''
        test current version after setup
    '''
    
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute')
    nodeGroupObj.provision_env_ready = False
    nodeGroupObj.save()
    env_ready,setup_occurred = check_provision_env_ready(orgAccountObj)
    assert env_ready == True
    assert setup_occurred == True # we set provision_env_ready to False above so we forced setup
    with ps_client.create_client_channel("control") as channel:
        stub = ps_server_pb2_grpc.ControlStub(channel)
        rsp = stub.GetCurrentSetUpCfg(ps_server_pb2.GetCurrentSetUpCfgReq(name=orgAccountObj.name))
    assert rsp.setup_cfg.name == orgAccountObj.name
    assert rsp.setup_cfg.version == nodeGroupObj.version
    assert rsp.setup_cfg.is_public == nodeGroupObj.is_public    

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.real_ps_server
@pytest.mark.parametrize('tasks_module_to_import', ['tasks'], indirect=True)
def test_provision_env_ready(tasks_module_to_import,developer_TEST_USER,initialize_test_environ):
    '''
        This procedure will test the 'provision_env_ready' routine 
    '''
    orgAccountObj = get_test_org()
    nodeGroupObj = get_test_compute_cluster()
    nodeGroupObj.version = 'v3'
    nodeGroupObj.is_public = False
    orgAccountObj.save()
    nodeGroupObj = NodeGroup.objects.get(org=orgAccountObj,name='compute') # get the cluster object
    nodeGroupObj.provision_env_ready = False # forces a SetUp to occur
    nodeGroupObj.save()
    logger.info(f"{orgAccountObj.name} v:{nodeGroupObj.version} ip:{nodeGroupObj.is_public} nodeGroupObj.provision_env_ready:{nodeGroupObj.provision_env_ready}")
    env_ready,setup_occurred = check_provision_env_ready(orgAccountObj)
    assert env_ready == True
    assert setup_occurred  # fixture already did setup
    with ps_client.create_client_channel("control") as channel:
        stub = ps_server_pb2_grpc.ControlStub(channel)
        rsp = stub.GetCurrentSetUpCfg(ps_server_pb2.GetCurrentSetUpCfgReq(name=orgAccountObj.name))
    assert rsp.setup_cfg.name == orgAccountObj.name
    assert rsp.setup_cfg.version == nodeGroupObj.version
    assert rsp.setup_cfg.is_public == nodeGroupObj.is_public