import unittest
import pytest

from users.tests.global_test import GlobalTestCase
from users.tasks import get_current_cost_report
from datetime import timezone,datetime
from datetime import date, datetime, timedelta, timezone, tzinfo
from users.tests.utilities_for_unit_tests import init_test_environ,process_rsp_gen,call_SetUp
from django.test import tag
import ps_server_pb2
import ps_server_pb2_grpc
from users import ps_client
from users.models import NodeGroup
import time
from users.global_constants import *
import logging
logger = logging.getLogger('django')
from users.utils import FULL_FMT
from users.utils import DAY_FMT
from users.utils import MONTH_FMT
from users.tests.utilities_for_unit_tests import init_test_environ,get_test_org,get_test_compute_cluster,OWNER_USER,OWNER_EMAIL,OWNER_PASSWORD,random_test_user

class TasksTestWithPSServer(GlobalTestCase):
    '''
    These tests use the ps-server and are run from build-and-deploy
    '''
    def setUp(self):
        #logger.info(f"{__name__}({self}) ... ------------------")
        return super().setUp()

    def tearDown(self):
        #logger.info(f"{__name__}({self}) ... ------------------")
        return super().tearDown()   

    # This will only be run if it is explicitly called
    @pytest.mark.cost
    @pytest.mark.real_ps_server
    @tag('cost')
    def testadhoc_get_org_cost_report_org_sliderule(self):
        '''
            This procedure will incur some small costs 
            https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/
        '''
        orgAccountObj,owner = init_test_environ("sliderule",
                                                org_owner=None,
                                                max_allowance=20000, 
                                                monthly_allowance=1000,
                                                balance=2000,
                                                fytd_accrued_cost=100, 
                                                most_recent_charge_time=datetime.now(timezone.utc), 
                                                most_recent_credit_time=datetime.now(timezone.utc),
                                                most_recent_recon_time=datetime.now(timezone.utc))
        time_now = datetime.now(timezone.utc)

        ccr,rsp = get_current_cost_report(orgAccountObj.name,'HOURLY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)
        time.sleep(1) # ce is rate limited
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'DAILY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)
        time.sleep(1) # ce is rate limited
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'MONTHLY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)

    # This will only be run if it is explicitly called
    @pytest.mark.cost
    @pytest.mark.real_ps_server
    @tag('cost')
    def testadhoc_get_org_cost_report_org_developers(self):
        '''
            This procedure will incur some small costs 
            https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/
        '''
        time_now = datetime.now(timezone.utc)
        orgAccountObj,owner = init_test_environ("developers",
                                                org_owner=None,
                                                max_allowance=20000, 
                                                monthly_allowance=1000,
                                                balance=2000,
                                                fytd_accrued_cost=100, 
                                                most_recent_charge_time=time_now, 
                                                most_recent_credit_time=time_now,
                                                most_recent_recon_time=time_now)
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'HOURLY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'DAILY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'MONTHLY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)


    @pytest.mark.cost
    @pytest.mark.real_ps_server
    @tag('cost')
    def test_get_org_cost_report_org_uofmdtest(self):
        '''
            This procedure will incur some small costs 
            https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/
        '''
        time_now = datetime.now(timezone.utc)
        orgAccountObj,owner = init_test_environ("UofMDTest",
                                                org_owner=None,
                                                max_allowance=20000, 
                                                monthly_allowance=1000,
                                                balance=2000,
                                                fytd_accrued_cost=100, 
                                                most_recent_charge_time=time_now, 
                                                most_recent_credit_time=time_now,
                                                most_recent_recon_time=time_now)
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'HOURLY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'DAILY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)
        ccr,rsp = get_current_cost_report(orgAccountObj.name,'MONTHLY',time_now)
        #logger.info(f"ccr:{ccr}")
        #logger.info(f"rsp:{rsp}")
        assert(rsp.server_error == False)

# -------- pytests -----
#@pytest.mark.dev
#@pytest.mark.real_ps_server
@pytest.mark.django_db
def test_SetUp(initialize_test_environ,setup_logging):
    '''
        This tests against the real ps-server
    '''
    logger = setup_logging
    orgAccountObj = get_test_org()
    assert(call_SetUp(orgAccountObj))