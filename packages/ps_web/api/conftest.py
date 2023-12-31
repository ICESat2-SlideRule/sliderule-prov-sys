import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from users.tasks import log_scheduled_jobs
from users.tests.utilities_for_unit_tests import TEST_EMAIL,TEST_ORG_NAME,TEST_PASSWORD,TEST_USER,DEV_TEST_EMAIL,DEV_TEST_PASSWORD,DEV_TEST_USER
from users.tests.utilities_for_unit_tests import random_test_user,init_test_environ,verify_user,mock_django_email_backend,create_test_user,check_redis_for_testing,check_for_scheduled_jobs,clear_enqueue_process_state_change
from users.tests.conftest import TEST_USER,TEST_PASSWORD,DEV_TEST_USER,DEV_TEST_PASSWORD,TEST_ORG_NAME
from datetime import datetime, timezone, timedelta
from django.contrib.auth.models import Group
from django.conf import settings
from django.core.cache import cache


import logging
logger = logging.getLogger('test_console')


@pytest.fixture(scope="session", autouse=True)
def redis_scheduled_jobs_setup(setup_logging):
    logger = setup_logging
    check_redis_for_testing(logger,__name__)
    assert(check_for_scheduled_jobs(logger,__name__,3)) # three cron jobs in docker init script
    jobs = log_scheduled_jobs()
    assert (len(jobs) == 3)


@pytest.fixture
def mock_email_backend(mocker):
    mock_django_email_backend(mocker)

@pytest.fixture
def create_TEST_USER():
    create_test_user(first_name="Test", last_name="User", username=TEST_USER, email=TEST_EMAIL, password=TEST_PASSWORD)

@pytest.fixture
def the_TEST_USER():
    return get_user_model().objects.get(username=TEST_USER)


@pytest.fixture
def random_TEST_USER():
    user = random_test_user()
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
def initialize_test_environ(setup_logging,redis_scheduled_jobs_setup,request):
    logger = setup_logging
    version = 'latest'
    is_public = True
    settings.DEBUG = True

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
    clear_enqueue_process_state_change(logger)