from users.tests.global_test import GlobalTestCase
import pytest
import logging
import logging
import sys
import os
import pathlib
from importlib import import_module
from users.tests.utilities_for_unit_tests import get_test_org,get_test_compute_cluster,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,the_TEST_USER
from users.forms import ClusterNumNodeForm
from users.models import OrgAccount
from django.urls import reverse
import time_machine
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

# 
#  -------  pytest stuff follows --------
#

#@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_num_node_form_valid(caplog,client,create_TEST_USER,mock_email_backend,initialize_test_environ):
    form_data = {
        'desired_num_nodes': get_test_compute_cluster().MIN_NODES,
        'ttl_minutes': 15,
    }
    form = ClusterNumNodeForm(form_data, min_nodes=get_test_org().MIN_NODES, max_nodes=get_test_compute_cluster().ABS_MAX_NODES)
    assert form.is_valid()

    form_data = {
        'desired_num_nodes': -1,
        'ttl_minutes': 15,
    }
    form = ClusterNumNodeForm(form_data, min_nodes=get_test_compute_cluster().MIN_NODES, max_nodes=get_test_compute_cluster().ABS_MAX_NODES)
    assert not form.is_valid() # NOTE we catch this in the clean method

    form_data = {
        'desired_num_nodes': get_test_compute_cluster().MIN_NODES+1,
        'ttl_minutes': 15,
    }
    form = ClusterNumNodeForm(form_data, min_nodes=get_test_compute_cluster().MIN_NODES, max_nodes=get_test_compute_cluster().ABS_MAX_NODES)
    assert form.is_valid()

    form_data = {
        'desired_num_nodes': 0,
        'ttl_minutes': 15,
    }
    form = ClusterNumNodeForm(form_data, min_nodes=1, max_nodes=get_test_compute_cluster().ABS_MAX_NODES)
    assert form.is_valid()

    form_data = {
        'desired_num_nodes': 1,
        'ttl_minutes': 15,
    }
    form = ClusterNumNodeForm(form_data, min_nodes=1, max_nodes=get_test_compute_cluster().ABS_MAX_NODES)
    assert form.is_valid()


    form = ClusterNumNodeForm(min_nodes=2, max_nodes=ASGNodeLimits.ABS_MAX_NODES)
    assert form.fields['desired_num_nodes'].initial == 2

    form = ClusterNumNodeForm(min_nodes=0, max_nodes=ASGNodeLimits.ABS_MAX_NODES)
    assert form.fields['desired_num_nodes'].initial == 1

    form = ClusterNumNodeForm(min_nodes=10, max_nodes=ASGNodeLimits.ABS_MAX_NODES)
    assert form.fields['desired_num_nodes'].initial == 10  # min_nodes + 1
