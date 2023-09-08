import pytest
import logging
import sys
import os
import pathlib
from importlib import import_module
from decimal import *
from users.tests.utilities_for_unit_tests import OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,create_test_user
from users.forms import NodeGroupCfgForm
from users.views import add_org_cluster_orgcost
from django.contrib import messages
from django.contrib.auth import get_user_model
from oauth2_provider.models import Application
from time import sleep

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

@pytest.mark.dev
@pytest.mark.django_db
@pytest.mark.ps_server_stubbed
def test_org_form_create_uri(tasks_module):
    '''
        This procedure will test form for creating a new org
    '''

    owner = create_test_user(first_name='Owner_test_create', last_name="User_test_create", username=OWNER_USER+'test_create', email=OWNER_EMAIL, password=OWNER_PASSWORD)
    app = Application.objects.create(client_id='1492',user=owner,client_secret='1492',name='test_app')

    form = NodeGroupCfgForm(data={
        'name': 'test_create',
        'owner': owner, # use same as sliderule org
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

    new_org,msg,emsg,p = add_org_cluster_orgcost(form)
