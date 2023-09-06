import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from users.tests.utilities_for_unit_tests import TEST_EMAIL,TEST_ORG_NAME,TEST_PASSWORD,TEST_USER,DEV_TEST_EMAIL,DEV_TEST_PASSWORD,DEV_TEST_USER,create_test_user
from users.tests.utilities_for_unit_tests import random_test_user,init_test_environ,verify_user,mock_django_email_backend,get_test_org,get_test_compute_cluster,call_SetUp
from users.models import NodeGroup
from datetime import datetime, timezone, timedelta
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.contrib.sites.shortcuts import get_current_site

import logging
from importlib import import_module


@pytest.fixture(scope='session', autouse=True)
def setup_logging():
    # Create a custom logger
    logger = logging.getLogger('unit-testing')
    # Set level of logging
    #logger.setLevel(logging.ERROR)
    logger.setLevel(logging.INFO)
    #logger.setLevel(logging.DEBUG)

    # Create handlers
    console_handler = logging.StreamHandler()
    #console_handler.setLevel(logging.ERROR)
    console_handler.setLevel(logging.INFO)
    #console_handler.setLevel(logging.DEBUG)

    # Create formatter and add it to handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d:%(funcName)s - %(message)s')
    console_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)

    yield logger  # provide the fixture value

    # After the test session, remove the handler to avoid logging duplicate messages
    logger.removeHandler(console_handler)


@pytest.fixture(autouse=True)
def set_test_site(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        Site.objects.update_or_create(id=2, defaults={'domain': 'localhost', 'name': 'localhost'})

@pytest.fixture()
def tasks_module_to_import(request):
    yield import_module(request.param)


@pytest.fixture
def mock_email_backend(mocker):
    mock_django_email_backend(mocker)

@pytest.fixture
def create_TEST_USER():
    user = create_test_user(first_name="Test", last_name="User", username=TEST_USER, email=TEST_EMAIL, password=TEST_PASSWORD)
    return user


@pytest.fixture
def random_TEST_USER(setup_logging):
    user = random_test_user()
    logger = setup_logging
    logger.info(f"random_TEST_USER username:{user.username} password:{user.password}")   
    return user


@pytest.fixture
def developer_TEST_USER(setup_logging):
    logger = setup_logging
    ps_developer_group, _ = Group.objects.get_or_create(name="PS_Developer")
    dev_user = create_test_user(first_name="Dev", last_name="User", username=DEV_TEST_USER, email=DEV_TEST_EMAIL, password=DEV_TEST_PASSWORD)
    dev_user.groups.add(ps_developer_group)
    logger.info(f"developer_TEST_USER username:{dev_user.username} password:{dev_user.password}")   
    return verify_user(dev_user)

@pytest.fixture
def verified_TEST_USER(create_TEST_USER):
    return verify_user(create_TEST_USER)

@pytest.fixture
def initialize_test_environ(setup_logging,request):
    logger = setup_logging
    version = 'latest'
    is_public = False

    if hasattr(request, "param"):
        if 'version' in request.param:
            version = request.param['version']
        if 'is_public' in request.param:
            is_public = request.param['is_public']
    
    logger.info(f"init version: {version}")
    logger.info(f"is_public: {is_public}")
    orgAccountObj,owner = init_test_environ(the_logger=logger,
                                            name=TEST_ORG_NAME,
                                            org_owner=None,
                                            max_allowance=20000, 
                                            monthly_allowance=1000,
                                            balance=2000,
                                            fytd_accrued_cost=100, 
                                            most_recent_charge_time=datetime.now(timezone.utc), 
                                            most_recent_credit_time=datetime.now(timezone.utc),
                                            most_recent_recon_time=datetime.now(timezone.utc),
                                            version=version,
                                            is_public=is_public)
    logger.info(f"org:{orgAccountObj.name} owner:{orgAccountObj.owner.username}")


