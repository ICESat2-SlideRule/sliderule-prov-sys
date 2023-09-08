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
from users.tests.utilities_for_unit_tests import initialize_test_org
from users.tests.utilities_for_unit_tests import TEST_EMAIL,TEST_ORG_NAME,TEST_PASSWORD,TEST_USER,DEV_TEST_EMAIL,DEV_TEST_PASSWORD,DEV_TEST_USER

from users.models import Membership
from users.forms import NodeGroupCfgForm
from users.tasks import init_new_org_memberships,init_new_org_memberships
from users.views import add_org_cluster_orgcost
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
@pytest.mark.parametrize(
    'name, point_of_contact_name, email, max_allowance,monthly_allowance,balance,admin_max_node_cap,is_public',
    [
        ('test_org1','the_poc1','devtester@mail.slideruleearth.io',10000,2000,5000,5,True),
        ('test_org2','the_poc2','devtester@mail.slideruleearth.io',10000,2000,5000,5,False),
    ])
def test_new_org(name, point_of_contact_name, email, max_allowance,monthly_allowance,balance,admin_max_node_cap,is_public,tasks_module,caplog,client):
    '''
        This procedure will test creating a new organization
    '''
    caplog.set_level(logging.DEBUG)
    orgAccountObj,owner = initialize_test_org("sliderule",
                                            org_owner=None,
                                            max_allowance=20000, 
                                            monthly_allowance=1000,
                                            balance=2000,
                                            fytd_accrued_cost=100, 
                                            most_recent_charge_time=datetime.now(timezone.utc), 
                                            most_recent_credit_time=datetime.now(timezone.utc),
                                            most_recent_recon_time=datetime.now(timezone.utc))
    form = NodeGroupCfgForm(data={
        'org':orgAccountObj,
        'name': name,
        'owner': orgAccountObj.owner, # use same as sliderule org
        'point_of_contact_name': point_of_contact_name,
        'email': email, 
        'max_allowance':max_allowance,
        'monthly_allowance':monthly_allowance,
        'balance':balance,
        'admin_max_node_cap':admin_max_node_cap,
        'is_public':is_public})
    logger.info(f"{form.errors.as_data()}")
    assert form.is_valid() 
    new_org,msg,emsg,p = add_org_cluster_orgcost(form)  # this is atomic
    assert p.pid is not None
    p.terminate()


